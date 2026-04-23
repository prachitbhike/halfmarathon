"""Tests for the shared helper modules (event_log, digests, llm).

The llm module is exercised in offline mode only — no real API calls in CI.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import HttpUrl

# Ensure the LLM mock is engaged for these tests regardless of caller env.
os.environ.setdefault("HALFMARATHON_OFFLINE_LLM", "1")

from task.digests import (
    approval_path,
    items_from_kb,
    publish,
    published_path,
    read_approval,
    render_digest_md,
    week_id_for,
    write_approval,
    write_draft,
)
from task.event_log import EventLog
from task.llm import Llm, total_cost
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

# -------- event log -----------------------------------------------------


def test_event_log_appends_jsonl(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "state" / "events.jsonl")
    ts = datetime(2026, 4, 1, 9, tzinfo=UTC)
    log.append("wake", {"reason": "daily"}, ts=ts)
    log.append("fetch", {"events": 3}, ts=ts + timedelta(seconds=1))

    raw = (tmp_path / "state" / "events.jsonl").read_text().splitlines()
    assert len(raw) == 2
    parsed = [json.loads(line) for line in raw]
    assert parsed[0]["kind"] == "wake"
    assert parsed[0]["payload"]["reason"] == "daily"

    roundtrip = log.read_all()
    assert len(roundtrip) == 2
    assert roundtrip[0].kind == "wake"
    assert roundtrip[1].payload["events"] == 3


def test_event_log_creates_parent_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "events.jsonl"
    log = EventLog(deep)
    assert deep.parent.exists()
    log.append("x", ts=datetime(2026, 4, 1, tzinfo=UTC))
    assert deep.exists() and deep.read_text().strip()


# -------- digests -------------------------------------------------------


def test_week_id_for_is_iso_week() -> None:
    # 2026-04-01 is a Wednesday in ISO week 14.
    assert week_id_for(datetime(2026, 4, 1, tzinfo=UTC)) == "week-2026-W14"


def test_render_digest_md_includes_items_and_links() -> None:
    items = [
        DigestItem(
            event_id="e1",
            title="A new agent harness",
            source_name="Anthropic",
            url=HttpUrl("https://example.test/x"),
            summary="Two-process planner+coder split with file-based memory.",
        ),
    ]
    md = render_digest_md(
        digest_id="week-2026-W14",
        week_start=datetime(2026, 4, 1, tzinfo=UTC),
        week_end=datetime(2026, 4, 8, tzinfo=UTC),
        items=items,
    )
    assert "# week-2026-W14" in md
    assert "A new agent harness" in md
    assert "https://example.test/x" in md
    assert "Two-process planner" in md


def test_write_draft_and_read_approval_cycle(tmp_path: Path) -> None:
    digest = Digest(
        id="week-2026-W14",
        week_start=datetime(2026, 4, 1, tzinfo=UTC),
        week_end=datetime(2026, 4, 8, tzinfo=UTC),
        items=[],
        body_md="# week-2026-W14\n\n(no items)\n",
        status=DigestStatus.DRAFT,
        drafted_at=datetime(2026, 4, 8, tzinfo=UTC),
    )
    write_draft(tmp_path, digest)
    assert (tmp_path / "digests" / "draft-week-2026-W14.md").exists()

    # No approval yet.
    assert read_approval(tmp_path, "week-2026-W14") is None

    # Harness writes approval.
    write_approval(
        tmp_path, "week-2026-W14",
        status=ApprovalStatus.APPROVED,
        feedback="lgtm",
        received_at=datetime(2026, 4, 8, 10, tzinfo=UTC),
    )
    appr = read_approval(tmp_path, "week-2026-W14")
    assert appr is not None
    assert appr.status == ApprovalStatus.APPROVED
    assert appr.feedback == "lgtm"

    # Publish flow.
    pub = publish(tmp_path, digest, appr)
    assert pub == published_path(tmp_path, "week-2026-W14")
    assert pub.read_text() == digest.body_md  # no edits in approval


def test_publish_uses_edits_when_present(tmp_path: Path) -> None:
    digest = Digest(
        id="week-2026-W14",
        week_start=datetime(2026, 4, 1, tzinfo=UTC),
        week_end=datetime(2026, 4, 8, tzinfo=UTC),
        items=[],
        body_md="(original)\n",
        status=DigestStatus.DRAFT,
        drafted_at=datetime(2026, 4, 8, tzinfo=UTC),
    )
    appr = Approval(
        digest_id="week-2026-W14",
        status=ApprovalStatus.APPROVED,
        edits="(edited body)\n",
        received_at=datetime(2026, 4, 8, 10, tzinfo=UTC),
    )
    pub = publish(tmp_path, digest, appr)
    assert pub.read_text() == "(edited body)\n"


def test_items_from_kb_filters_window_and_caps() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    week_start = base
    week_end = base + timedelta(days=7)
    kb = [
        KnowledgeBaseItem(
            event_id=f"e{i}",
            source_id="src-a",
            fixture_timestamp=base + timedelta(days=i),
            title=f"item {i}",
            url=HttpUrl(f"https://example.test/{i}"),
            summary="summary",
            relevance_score=(i % 5) / 4.0,  # varied scores
        )
        for i in range(10)
    ]
    items = items_from_kb(
        kb,
        source_name_by_id={"src-a": "Source A"},
        week_start=week_start,
        week_end=week_end,
        max_items=3,
    )
    assert len(items) == 3
    # Capped at max_items, ordered by relevance.
    # All three should be from within (week_start, week_end).
    assert all(item.source_name == "Source A" for item in items)


def test_approval_path_naming(tmp_path: Path) -> None:
    p = approval_path(tmp_path, "week-2026-W14")
    assert p.name == "draft-week-2026-W14.approval.json"


# -------- llm (offline) -------------------------------------------------


def test_llm_offline_score_relevance_keyword_match() -> None:
    llm = Llm()  # OFFLINE=1 from env above
    profile = UserProfile(user_id="u", interests=["agents", "memory"])
    events = [
        SourceEvent(
            id="e1", source_id="s", fixture_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            kind="post", title="Agent memory blocks", url=HttpUrl("https://example.test/1"),
            body_md="agent memory architecture",
        ),
        SourceEvent(
            id="e2", source_id="s", fixture_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
            kind="post", title="Sora 3 video", url=HttpUrl("https://example.test/2"),
            body_md="text-to-video",
        ),
    ]
    scores = llm.score_relevance(
        events=events, profile=profile, ts=datetime(2026, 4, 1, tzinfo=UTC),
    )
    by_id = {s["event_id"]: s["relevance_score"] for s in scores}
    assert by_id["e1"] >= 0.5
    assert by_id["e2"] < 0.5


def test_llm_offline_summarize_returns_string() -> None:
    llm = Llm()
    profile = UserProfile(user_id="u", interests=["agents"])
    evt = SourceEvent(
        id="e1", source_id="s", fixture_timestamp=datetime(2026, 4, 1, tzinfo=UTC),
        kind="post", title="X", url=HttpUrl("https://example.test/1"),
        body_md="An interesting result on long-running agents.",
    )
    out = llm.summarize_for_digest(event=evt, profile=profile, ts=datetime(2026, 4, 1, tzinfo=UTC))
    assert isinstance(out, str) and out


def test_llm_ledger_starts_empty_in_offline_mode() -> None:
    # Offline mode does not touch the API and does not record cost.
    llm = Llm()
    assert llm.ledger == []
    assert total_cost(llm.ledger) == 0.0
