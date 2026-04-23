"""Digest rendering, draft/approval/publish file conventions.

Spec R3 + R4 require:
    state/digests/draft-<week>.md             (the proposed digest)
    state/digests/draft-<week>.approval.json  (signal from harness)
    state/digests/published-<week>.md         (post-approval, after edits)

We centralize the file naming + render so all four implementations produce
identical output paths. Each impl decides WHEN to draft and how to compose
the body — but the path layout is the contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from task.types import (
    Approval,
    ApprovalStatus,
    Digest,
    DigestItem,
    DigestStatus,
    KnowledgeBaseItem,
)

WEEK_ID_FMT = "%Y-W%V"  # e.g. "2026-W14"


def week_id_for(ts: datetime) -> str:
    """Return ISO week id, e.g. '2026-W14'."""
    return f"week-{ts.strftime(WEEK_ID_FMT)}"


def draft_path(state_dir: Path, week_id: str) -> Path:
    return state_dir / "digests" / f"draft-{week_id}.md"


def approval_path(state_dir: Path, week_id: str) -> Path:
    return state_dir / "digests" / f"draft-{week_id}.approval.json"


def published_path(state_dir: Path, week_id: str) -> Path:
    return state_dir / "digests" / f"published-{week_id}.md"


def render_digest_md(
    digest_id: str,
    week_start: datetime,
    week_end: datetime,
    items: Iterable[DigestItem],
    *,
    intro: str | None = None,
) -> str:
    lines = [
        f"# {digest_id}",
        "",
        f"_Coverage: {week_start.date().isoformat()} → {week_end.date().isoformat()}_",
        "",
    ]
    if intro:
        lines += [intro, ""]
    for i, item in enumerate(items, 1):
        lines += [
            f"## {i}. {item.title}",
            f"_{item.source_name}_ — [link]({item.url})",
            "",
            item.summary,
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def write_draft(
    state_dir: Path,
    digest: Digest,
) -> Path:
    path = draft_path(state_dir, digest.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(digest.body_md)
    return path


def read_approval(state_dir: Path, week_id: str) -> Approval | None:
    """Return the approval if present, else None. Tolerates partial writes."""
    p = approval_path(state_dir, week_id)
    if not p.exists():
        return None
    raw = p.read_text().strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return Approval.model_validate(data)


def write_approval(
    state_dir: Path,
    week_id: str,
    *,
    status: ApprovalStatus,
    feedback: str | None = None,
    edits: str | None = None,
    received_at: datetime,
) -> Path:
    """Used by the harness (and by tests) to simulate human approval."""
    p = approval_path(state_dir, week_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    appr = Approval(
        digest_id=week_id,
        status=status,
        feedback=feedback,
        edits=edits,
        received_at=received_at,
    )
    p.write_text(appr.model_dump_json(indent=2))
    return p


def publish(
    state_dir: Path,
    digest: Digest,
    approval: Approval,
) -> Path:
    """Write the final published digest. If approval.edits is set, use those."""
    body = approval.edits if approval.edits else digest.body_md
    p = published_path(state_dir, digest.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def items_from_kb(
    kb_items: Iterable[KnowledgeBaseItem],
    source_name_by_id: dict[str, str],
    *,
    week_start: datetime,
    week_end: datetime,
    max_items: int,
) -> list[DigestItem]:
    """Filter KB to the past week and rank by relevance_score, capped to max_items."""
    in_window = [
        it for it in kb_items
        if week_start <= it.fixture_timestamp < week_end
    ]
    in_window.sort(key=lambda x: x.relevance_score, reverse=True)
    selected = in_window[:max_items]
    return [
        DigestItem(
            event_id=it.event_id,
            title=it.title,
            source_name=source_name_by_id.get(it.source_id, it.source_id),
            url=it.url,
            summary=it.summary,
        )
        for it in selected
    ]


__all__ = [
    "WEEK_ID_FMT",
    "DigestStatus",
    "approval_path",
    "draft_path",
    "items_from_kb",
    "publish",
    "published_path",
    "read_approval",
    "render_digest_md",
    "week_id_for",
    "write_approval",
    "write_draft",
]
