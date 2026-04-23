"""Temporal activities — the unit of side effects + durability.

All non-deterministic work the workflow needs (filesystem, model calls, fixture
clock reads) lives here. Each activity is replayed-from-history on workflow
resume, so workflows themselves see them as deterministic functions of input.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import HttpUrl
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
    Source,
    SourceEvent,
    UserProfile,
)

log = logging.getLogger("halfmarathon.temporal_pydantic.activities")
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"


# Activities take pydantic models as inputs/outputs (PydanticPayloadConverter
# is registered on the worker so they serialize cleanly).


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
    # The activity is being called at workflow-fixture-now; we don't need
    # clock.start() since we evaluate at fixture_now_iso explicitly.
    fixture_now = datetime.fromisoformat(req.fixture_now_iso)
    since = (
        datetime.fromisoformat(req.last_fetch_iso) if req.last_fetch_iso else None
    )
    events = clock.fetch_events_until(fixture_now, since=since)
    payload = json.dumps([e.model_dump(mode="json") for e in events])
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
    events_log.append("llm_call", {"purpose": "relevance", "n": len(events)}, ts=fixture_now)

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
        events_log.append(
            "llm_call", {"purpose": "summarize", "event_id": evt.id}, ts=fixture_now,
        )
        kbi = KnowledgeBaseItem(
            event_id=evt.id, source_id=evt.source_id,
            fixture_timestamp=evt.fixture_timestamp,
            title=evt.title, url=evt.url, summary=summary,
            relevance_score=score,
        )
        new_items.append(kbi.model_dump(mode="json"))
        events_log.append(
            "summary", {"event_id": evt.id, "score": score}, ts=fixture_now,
        )

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
    EventLog(state_dir / "events.jsonl").append(
        "digest_draft", {"week_id": req.week_id, "items": len(items)}, ts=fixture_now,
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
    EventLog(Path(req.state_dir) / "events.jsonl").append(
        req.kind, json.loads(req.payload_json), ts=datetime.fromisoformat(req.ts_iso),
    )


# All activities the worker should register.
ALL_ACTIVITIES = [
    fetch_events,
    score_and_summarize,
    draft_digest,
    poll_approval,
    publish,
    write_event_log,
]


# Re-export for convenience (workflow imports input/output dataclasses)
__all__ = [
    "ALL_ACTIVITIES",
    "DraftDigestInput",
    "DraftDigestOutput",
    "FetchEventsInput",
    "FetchEventsOutput",
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
    "poll_approval",
    "publish",
    "score_and_summarize",
    "write_event_log",
]


# Touch unused-but-public types so ruff doesn't complain in this module.
_ = HttpUrl, Source
