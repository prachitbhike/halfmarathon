"""Temporal activities — the unit of side effects + durability.

All non-deterministic work the workflow needs (filesystem, model calls, fixture
clock reads) lives here. Each activity is replayed-from-history on workflow
resume, so workflows themselves see them as deterministic functions of input.

Event-log writes are guarded by ``activity.info().attempt == 1`` so that
Temporal retries don't produce duplicate log lines.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from temporalio import activity

from implementations.temporal_pydantic.agent import (
    OFFLINE,
    RelevanceScores,
    relevance_agent,
    summary_agent,
)
from task.clock import FixtureClock
from task.digests import (
    items_from_kb,
    read_approval,
    render_digest_md,
    write_draft,
)
from task.digests import (
    publish as publish_digest,
)
from task.event_log import EventLog
from task.llm import Llm
from task.types import (
    Approval,
    ApprovalStatus,
    Digest,
    DigestItem,
    DigestStatus,
    KnowledgeBaseItem,
    SourceEvent,
    UserProfile,
)

log = logging.getLogger("halfmarathon.temporal_pydantic.activities")
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"


def _first_attempt() -> bool:
    """True on the initial activity attempt; False on Temporal retries.

    Used to guard append-only side effects (event log) so that a retried
    activity doesn't duplicate entries. The workflow history replay story
    for the *main* side effects (publishing a digest, writing KB) is
    already idempotent by filename or key; only the event log was append-
    only and needed this guard.
    """
    try:
        return activity.info().attempt == 1
    except RuntimeError:
        # Called outside of an activity context (tests/smoke). Treat as
        # first-attempt so local callers still see their writes.
        return True


def _load_kb(state_dir: Path) -> list[dict[str, Any]]:
    p = state_dir / "kb.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save_kb(state_dir: Path, kb: list[dict[str, Any]]) -> None:
    p = state_dir / "kb.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kb, indent=2, default=str))


def _load_meta(state_dir: Path) -> dict[str, Any]:
    p = state_dir / "run_meta.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_meta(state_dir: Path, meta: dict[str, Any]) -> None:
    p = state_dir / "run_meta.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, default=str))


def _published_week_ids(state_dir: Path) -> list[str]:
    digests = state_dir / "digests"
    if not digests.exists():
        return []
    return sorted(
        p.stem.removeprefix("published-")
        for p in digests.glob("published-week-*.md")
    )


# Activities take pydantic models as inputs/outputs (PydanticPayloadConverter
# is registered on the worker so they serialize cleanly).


# ---- state recovery ------------------------------------------------------


@dataclass
class LoadPriorStateInput:
    state_dir: str


@dataclass
class LoadPriorStateOutput:
    kb_json: str                 # list[KnowledgeBaseItem.model_dump()]
    published_weeks: list[str]
    last_fetch_iso: str | None
    procedural_notes: list[str]


@activity.defn
async def load_prior_state(req: LoadPriorStateInput) -> LoadPriorStateOutput:
    """Reconstruct workflow state from state_dir.

    Called once at workflow boot so that a fresh workflow execution (new
    Temporal run id, same state_dir) resumes where the previous run left
    off. This mirrors what a checkpointer-backed framework does implicitly.
    """
    state_dir = Path(req.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    meta = _load_meta(state_dir)
    return LoadPriorStateOutput(
        kb_json=json.dumps(_load_kb(state_dir)),
        published_weeks=_published_week_ids(state_dir),
        last_fetch_iso=meta.get("last_fetch_iso"),
        procedural_notes=list(meta.get("procedural_notes") or []),
    )


@dataclass
class PersistMetaInput:
    state_dir: str
    last_fetch_iso: str | None
    procedural_notes: list[str]


@activity.defn
async def persist_meta(req: PersistMetaInput) -> None:
    _save_meta(
        Path(req.state_dir),
        {
            "last_fetch_iso": req.last_fetch_iso,
            "procedural_notes": list(req.procedural_notes),
        },
    )


@dataclass
class FetchEventsInput:
    state_dir: str   # Path-as-str — Temporal converters are happier with str
    fixture_start_iso: str
    fixture_now_iso: str
    last_fetch_iso: str | None
    speed: float
    fixtures_dir: str | None = None  # override default fixtures location


@dataclass
class FetchEventsOutput:
    events_json: str  # list[SourceEvent.model_dump_json()] joined with newlines
    count: int


@activity.defn
async def fetch_events(req: FetchEventsInput) -> FetchEventsOutput:
    fx = Path(req.fixtures_dir) if req.fixtures_dir else FIXTURES
    clock = FixtureClock.from_fixtures(
        timeline_path=fx / "timeline.json",
        sources_path=fx / "sources.json",
        fixture_start=datetime.fromisoformat(req.fixture_start_iso),
        speed=req.speed,
    )
    # `fetch_events_until` evaluates at fixture_now_iso explicitly and does
    # not need the wall-clock anchor; still, call start() so the clock is
    # in a consistent state if future callers read clock.now().
    clock.start()
    fixture_now = datetime.fromisoformat(req.fixture_now_iso)
    since = (
        datetime.fromisoformat(req.last_fetch_iso) if req.last_fetch_iso else None
    )
    events = clock.fetch_events_until(fixture_now, since=since)
    payload = json.dumps([e.model_dump(mode="json") for e in events])
    if _first_attempt():
        EventLog(Path(req.state_dir) / "events.jsonl").append(
            "fetch", {"new_events": len(events)}, ts=fixture_now,
        )
    return FetchEventsOutput(events_json=payload, count=len(events))


@dataclass
class ScoreSummarizeInput:
    state_dir: str
    fixture_now_iso: str
    events_json: str
    profile_json: str
    existing_kb_event_ids: list[str]


@dataclass
class ScoreSummarizeOutput:
    new_kb_items_json: str  # list[KnowledgeBaseItem.model_dump()]


@activity.defn
async def score_and_summarize(req: ScoreSummarizeInput) -> ScoreSummarizeOutput:
    """Score relevance + summarize all-in-one. Single activity = single retry
    boundary. Pydantic AI for the typed output (online); offline mock for CI.
    """
    state_dir = Path(req.state_dir)
    events: list[SourceEvent] = [
        SourceEvent.model_validate(e) for e in json.loads(req.events_json)
    ]
    profile = UserProfile.model_validate(json.loads(req.profile_json))
    fixture_now = datetime.fromisoformat(req.fixture_now_iso)
    events_log = EventLog(state_dir / "events.jsonl")
    existing = set(req.existing_kb_event_ids)

    # ---- score ----
    scores: list[dict[str, Any]]
    if OFFLINE:
        # Reuse the offline keyword-match path from task/llm.py for parity
        # with the other impls.
        llm = Llm()
        rows = llm.score_relevance(events=events, profile=profile, ts=fixture_now)
        scores = list(rows)
    else:
        agent = relevance_agent()
        prompt = (
            "User interests:\n"
            + "\n".join(f"- {i}" for i in profile.interests)
            + "\n\nEvents:\n"
            + json.dumps(
                [{"id": e.id, "title": e.title, "body": e.body_md[:600]} for e in events],
                indent=2,
            )
        )
        result = await agent.run(prompt)
        out: RelevanceScores = result.output
        scores = [r.model_dump() for r in out.items]
    if _first_attempt():
        events_log.append(
            "llm_call", {"purpose": "relevance", "n": len(events)}, ts=fixture_now,
        )

    score_by_id = {row.get("event_id"): float(row.get("relevance_score", 0)) for row in scores}

    # ---- summarize the items that crossed the threshold ----
    new_items: list[dict[str, Any]] = []
    for evt in events:
        if evt.id in existing:
            continue
        score = score_by_id.get(evt.id, 0.0)
        if score < 0.3:
            continue
        if OFFLINE:
            llm = Llm()
            summary = llm.summarize_for_digest(event=evt, profile=profile, ts=fixture_now)
        else:
            agent = summary_agent()
            prompt = (
                f"User tone preference: {profile.tone}\n\n"
                f"Item title: {evt.title}\n"
                f"Source: {evt.source_id}\n\n"
                f"Body:\n{evt.body_md}\n"
            )
            result = await agent.run(prompt)
            summary = result.output.strip()
        if _first_attempt():
            events_log.append(
                "llm_call", {"purpose": "summarize", "event_id": evt.id},
                ts=fixture_now,
            )
        kbi = KnowledgeBaseItem(
            event_id=evt.id, source_id=evt.source_id,
            fixture_timestamp=evt.fixture_timestamp,
            title=evt.title, url=evt.url, summary=summary,
            relevance_score=score,
        )
        new_items.append(kbi.model_dump(mode="json"))
        if _first_attempt():
            events_log.append(
                "summary", {"event_id": evt.id, "score": score}, ts=fixture_now,
            )

    # Persist KB to disk so a subsequent workflow execution (same state_dir,
    # new run id) can reload it via load_prior_state. Writing the full file
    # is idempotent on retry.
    existing_kb = _load_kb(state_dir)
    existing_ids = {it.get("event_id") for it in existing_kb}
    for it in new_items:
        if it.get("event_id") not in existing_ids:
            existing_kb.append(it)
            existing_ids.add(it.get("event_id"))
    _save_kb(state_dir, existing_kb)

    return ScoreSummarizeOutput(new_kb_items_json=json.dumps(new_items))


@dataclass
class DraftDigestInput:
    state_dir: str
    fixture_now_iso: str
    week_start_iso: str
    week_end_iso: str
    week_id: str
    kb_json: str
    max_items: int
    fixtures_dir: str | None = None  # override default fixtures location


@dataclass
class DraftDigestOutput:
    pending_json: str  # serialized PendingDigest dict, or "" if nothing to draft


@activity.defn
async def draft_digest(req: DraftDigestInput) -> DraftDigestOutput:
    state_dir = Path(req.state_dir)
    fixture_now = datetime.fromisoformat(req.fixture_now_iso)
    week_start = datetime.fromisoformat(req.week_start_iso)
    week_end = datetime.fromisoformat(req.week_end_iso)
    kb_items = [KnowledgeBaseItem.model_validate(d) for d in json.loads(req.kb_json)]
    fx = Path(req.fixtures_dir) if req.fixtures_dir else FIXTURES
    sources = json.loads((fx / "sources.json").read_text())
    source_name_by_id = {s["id"]: s["name"] for s in sources}
    items = items_from_kb(
        kb_items, source_name_by_id=source_name_by_id,
        week_start=week_start, week_end=week_end, max_items=req.max_items,
    )
    if not items:
        return DraftDigestOutput(pending_json="")
    body = render_digest_md(
        digest_id=req.week_id, week_start=week_start, week_end=week_end, items=items,
    )
    digest = Digest(
        id=req.week_id, week_start=week_start, week_end=week_end,
        items=items, body_md=body, status=DigestStatus.DRAFT,
        drafted_at=fixture_now,
    )
    write_draft(state_dir, digest)
    if _first_attempt():
        EventLog(state_dir / "events.jsonl").append(
            "digest_draft",
            {"week_id": req.week_id, "items": len(items)},
            ts=fixture_now,
        )
    pending = {
        "week_id": req.week_id,
        "week_start": req.week_start_iso,
        "week_end": req.week_end_iso,
        "body_md": body,
        "items": [it.model_dump(mode="json") for it in items],
    }
    return DraftDigestOutput(pending_json=json.dumps(pending))


@dataclass
class PollApprovalInput:
    state_dir: str
    week_id: str
    fixture_now_iso: str


@dataclass
class PollApprovalOutput:
    approval_json: str  # "" if not present yet


@activity.defn
async def poll_approval(req: PollApprovalInput) -> PollApprovalOutput:
    appr = read_approval(Path(req.state_dir), req.week_id)
    if appr is None:
        return PollApprovalOutput(approval_json="")
    return PollApprovalOutput(approval_json=appr.model_dump_json())


@dataclass
class PublishInput:
    state_dir: str
    pending_json: str
    approval_json: str
    fixture_now_iso: str


@dataclass
class PublishOutput:
    week_id: str
    published: bool        # False if approval was a rejection
    feedback: str | None


@activity.defn
async def publish(req: PublishInput) -> PublishOutput:
    state_dir = Path(req.state_dir)
    fixture_now = datetime.fromisoformat(req.fixture_now_iso)
    pending = json.loads(req.pending_json)
    appr = Approval.model_validate_json(req.approval_json)
    items = [DigestItem.model_validate(d) for d in pending["items"]]
    week_start = datetime.fromisoformat(pending["week_start"])
    week_end = datetime.fromisoformat(pending["week_end"])
    wid = pending["week_id"]
    events_log = EventLog(state_dir / "events.jsonl")

    if appr.status != ApprovalStatus.APPROVED:
        if _first_attempt():
            events_log.append(
                "approval", {"week_id": wid, "status": "rejected"}, ts=fixture_now,
            )
        return PublishOutput(week_id=wid, published=False, feedback=appr.feedback)

    digest = Digest(
        id=wid, week_start=week_start, week_end=week_end,
        items=items, body_md=pending["body_md"],
        status=DigestStatus.APPROVED,
        drafted_at=fixture_now, approved_at=appr.received_at,
    )
    publish_digest(state_dir, digest, appr)
    if _first_attempt():
        events_log.append("publish", {"week_id": wid}, ts=fixture_now)
        events_log.append(
            "approval", {"week_id": wid, "status": "approved"}, ts=fixture_now,
        )
    return PublishOutput(week_id=wid, published=True, feedback=appr.feedback)


@dataclass
class WriteEventInput:
    state_dir: str
    kind: str
    payload_json: str
    ts_iso: str


@activity.defn
async def write_event_log(req: WriteEventInput) -> None:
    # The workflow uses this for "wake" events. Retries must not duplicate,
    # so we guard on first attempt too.
    if _first_attempt():
        EventLog(Path(req.state_dir) / "events.jsonl").append(
            req.kind,
            json.loads(req.payload_json),
            ts=datetime.fromisoformat(req.ts_iso),
        )


# All activities the worker should register.
ALL_ACTIVITIES = [
    fetch_events,
    score_and_summarize,
    draft_digest,
    poll_approval,
    publish,
    write_event_log,
    load_prior_state,
    persist_meta,
]


# Re-export for convenience (workflow imports input/output dataclasses)
__all__ = [
    "ALL_ACTIVITIES",
    "DraftDigestInput",
    "DraftDigestOutput",
    "FetchEventsInput",
    "FetchEventsOutput",
    "LoadPriorStateInput",
    "LoadPriorStateOutput",
    "PersistMetaInput",
    "PollApprovalInput",
    "PollApprovalOutput",
    "PublishInput",
    "PublishOutput",
    "ScoreSummarizeInput",
    "ScoreSummarizeOutput",
    "WriteEventInput",
    # activities
    "draft_digest",
    "fetch_events",
    "load_prior_state",
    "persist_meta",
    "poll_approval",
    "publish",
    "score_and_summarize",
    "write_event_log",
]
