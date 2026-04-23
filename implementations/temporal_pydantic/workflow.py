"""Temporal workflow definition.

The workflow is a strict deterministic state machine. All non-determinism
(filesystem, model calls, fixture reads, real time) is delegated to activities
defined in `activities.py`.

Fixture-time mapping:
    The workflow does not call clock.now() directly (that would touch real
    time inside the workflow, which is non-deterministic on replay).
    Instead, it tracks `workflow.start_time()` and computes fixture_now
    from `workflow.now() - start_time` x speed. workflow.now() is
    deterministic on replay because Temporal records each timer firing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from implementations.temporal_pydantic.activities import (
        DraftDigestInput,
        FetchEventsInput,
        PollApprovalInput,
        PublishInput,
        ScoreSummarizeInput,
        WriteEventInput,
        draft_digest,
        fetch_events,
        poll_approval,
        publish,
        score_and_summarize,
        write_event_log,
    )
    from task.digests import week_id_for


# ---- workflow input / output --------------------------------------------


@dataclass
class WorkflowArgs:
    state_dir: str
    profile_json: str
    fixture_start_iso: str
    until_iso: str
    speed: float


@dataclass
class WorkflowSummary:
    published_weeks: list[str]
    kb_size: int
    procedural_notes: list[str]
    ticks: int


@dataclass
class _PendingDigest:
    week_id: str
    week_start_iso: str
    week_end_iso: str
    body_md: str
    items_json: str  # serialized items list


# ---- timing constants ----------------------------------------------------

_DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_attempts=3,
)
_ACTIVITY_TIMEOUT = timedelta(seconds=120)
# Polling interval inside workflow time, in fixture seconds.
_APPROVAL_POLL_FIXTURE_SECONDS = 3600  # 1 fixture-hour


@workflow.defn
class ReleaseRadarWorkflow:
    """Long-running Release Radar workflow."""

    def __init__(self) -> None:
        self.last_fetch_iso: str | None = None
        self.kb: list[dict] = []  # list[KnowledgeBaseItem.model_dump()]
        self.pending: _PendingDigest | None = None
        self.published_weeks: list[str] = []
        self.procedural_notes: list[str] = []
        self.ticks: int = 0
        self._workflow_start_dt: datetime | None = None  # for fixture-time mapping
        self._args: WorkflowArgs | None = None

    # ---- helpers (run inside workflow context) --------------------------

    def _fixture_now(self) -> datetime:
        assert self._args is not None and self._workflow_start_dt is not None
        elapsed_wall_s = (
            workflow.now() - self._workflow_start_dt
        ).total_seconds()
        return datetime.fromisoformat(self._args.fixture_start_iso) + timedelta(
            seconds=elapsed_wall_s * self._args.speed
        )

    def _wall_seconds_for_fixture_seconds(self, fixture_s: float) -> float:
        assert self._args is not None
        return fixture_s / self._args.speed

    async def _sleep_fixture(self, fixture_seconds: float) -> None:
        """Workflow sleep, in fixture seconds (mapped to wall via speed)."""
        await workflow.sleep(self._wall_seconds_for_fixture_seconds(fixture_seconds))

    async def _next_daily_wake_fixture(self) -> datetime:
        """Next midnight (fixture time) after fixture_now."""
        fnow = self._fixture_now()
        return (fnow + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def _emit(self, kind: str, payload: dict) -> None:
        assert self._args is not None
        await workflow.execute_activity(
            write_event_log,
            WriteEventInput(
                state_dir=self._args.state_dir,
                kind=kind,
                payload_json=json.dumps(payload),
                ts_iso=self._fixture_now().isoformat(),
            ),
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_DEFAULT_RETRY,
        )

    # ---- main loop ------------------------------------------------------

    @workflow.run
    async def run(self, args: WorkflowArgs) -> WorkflowSummary:
        self._args = args
        self._workflow_start_dt = workflow.now()
        until = datetime.fromisoformat(args.until_iso)

        await self._emit("wake", {"reason": "boot"})

        while self._fixture_now() < until:
            self.ticks += 1
            now = self._fixture_now()
            await self._emit("wake", {"now": now.isoformat()})

            # 1) fetch new events
            fetched = await workflow.execute_activity(
                fetch_events,
                FetchEventsInput(
                    state_dir=args.state_dir,
                    fixture_start_iso=args.fixture_start_iso,
                    fixture_now_iso=now.isoformat(),
                    last_fetch_iso=self.last_fetch_iso,
                    speed=args.speed,
                ),
                start_to_close_timeout=_ACTIVITY_TIMEOUT,
                retry_policy=_DEFAULT_RETRY,
            )
            self.last_fetch_iso = now.isoformat()

            # 2) score + summarize new events; merge into KB
            if fetched.count > 0:
                scored = await workflow.execute_activity(
                    score_and_summarize,
                    ScoreSummarizeInput(
                        state_dir=args.state_dir,
                        fixture_now_iso=now.isoformat(),
                        events_json=fetched.events_json,
                        profile_json=args.profile_json,
                        existing_kb_event_ids=[i["event_id"] for i in self.kb],
                    ),
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_DEFAULT_RETRY,
                )
                new_items = json.loads(scored.new_kb_items_json)
                self.kb.extend(new_items)

            # 3) maybe draft a weekly digest
            if self.pending is None and now.weekday() in (0, 6):
                week_start = (now - timedelta(days=7)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                week_end = now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                wid = week_id_for(week_start)
                if wid not in self.published_weeks:
                    profile = json.loads(args.profile_json)
                    drafted = await workflow.execute_activity(
                        draft_digest,
                        DraftDigestInput(
                            state_dir=args.state_dir,
                            fixture_now_iso=now.isoformat(),
                            week_start_iso=week_start.isoformat(),
                            week_end_iso=week_end.isoformat(),
                            week_id=wid,
                            kb_json=json.dumps(self.kb),
                            max_items=int(profile.get("max_items_per_digest", 8)),
                        ),
                        start_to_close_timeout=_ACTIVITY_TIMEOUT,
                        retry_policy=_DEFAULT_RETRY,
                    )
                    if drafted.pending_json:
                        d = json.loads(drafted.pending_json)
                        self.pending = _PendingDigest(
                            week_id=d["week_id"],
                            week_start_iso=d["week_start"],
                            week_end_iso=d["week_end"],
                            body_md=d["body_md"],
                            items_json=json.dumps(d["items"]),
                        )

            # 4) try to resolve pending approval (poll once per tick)
            if self.pending is not None:
                appr = await workflow.execute_activity(
                    poll_approval,
                    PollApprovalInput(
                        state_dir=args.state_dir,
                        week_id=self.pending.week_id,
                        fixture_now_iso=now.isoformat(),
                    ),
                    start_to_close_timeout=_ACTIVITY_TIMEOUT,
                    retry_policy=_DEFAULT_RETRY,
                )
                if appr.approval_json:
                    pending_dict = {
                        "week_id": self.pending.week_id,
                        "week_start": self.pending.week_start_iso,
                        "week_end": self.pending.week_end_iso,
                        "body_md": self.pending.body_md,
                        "items": json.loads(self.pending.items_json),
                    }
                    pub = await workflow.execute_activity(
                        publish,
                        PublishInput(
                            state_dir=args.state_dir,
                            pending_json=json.dumps(pending_dict),
                            approval_json=appr.approval_json,
                            fixture_now_iso=now.isoformat(),
                        ),
                        start_to_close_timeout=_ACTIVITY_TIMEOUT,
                        retry_policy=_DEFAULT_RETRY,
                    )
                    if pub.published:
                        self.published_weeks.append(pub.week_id)
                        self.procedural_notes.append(
                            f"[{pub.week_id}] approved"
                            + (f" with feedback: {pub.feedback}" if pub.feedback else "")
                        )
                    else:
                        self.procedural_notes.append(
                            f"[{pub.week_id}] rejected: {pub.feedback or 'unknown'}"
                        )
                    self.pending = None

            # 5) sleep until the next wake
            next_wake = await self._next_daily_wake_fixture()
            if self.pending is not None:
                # While pending, poll more frequently than daily.
                next_poll = self._fixture_now() + timedelta(
                    seconds=_APPROVAL_POLL_FIXTURE_SECONDS
                )
                next_wake = min(next_wake, next_poll)
            if next_wake >= until:
                break
            sleep_s = (next_wake - self._fixture_now()).total_seconds()
            await self._sleep_fixture(max(0.0, sleep_s))

        await self._emit(
            "wake",
            {"reason": "exit", "published": list(self.published_weeks)},
        )
        return WorkflowSummary(
            published_weeks=list(self.published_weeks),
            kb_size=len(self.kb),
            procedural_notes=list(self.procedural_notes),
            ticks=self.ticks,
        )


# Touch unused imports for tooling cleanliness.
_ = field
