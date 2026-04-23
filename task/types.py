"""Shared Pydantic schemas — the cross-implementation contract.

All four implementations exchange data using these types. The fixture files,
event log, and digest outputs all conform to these schemas.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

# -------------- sources & events ----------------------------------------


class SourceKind(StrEnum):
    RSS = "rss"
    GITHUB_RELEASES = "github_releases"


class Source(BaseModel):
    id: str
    type: SourceKind
    url: HttpUrl
    name: str
    topics: list[str] = Field(default_factory=list)


class SourceEvent(BaseModel):
    id: str
    source_id: str
    fixture_timestamp: datetime
    kind: str  # "post" | "release" | etc.
    title: str
    url: HttpUrl
    body_md: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# -------------- user profile ---------------------------------------------


class UserProfile(BaseModel):
    user_id: str
    interests: list[str]
    tone: str = "concise, technical, no marketing language"
    max_items_per_digest: int = 8


# -------------- knowledge base & digests ---------------------------------


class KnowledgeBaseItem(BaseModel):
    """An event the agent decided is relevant. Stored in the per-impl KB."""

    event_id: str
    source_id: str
    fixture_timestamp: datetime
    title: str
    url: HttpUrl
    summary: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    topics: list[str] = Field(default_factory=list)
    notes: str | None = None  # any extra agent annotation


class DigestStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class DigestItem(BaseModel):
    event_id: str
    title: str
    source_name: str
    url: HttpUrl
    summary: str


class Digest(BaseModel):
    id: str  # e.g. "week-2026-04-01"
    week_start: datetime
    week_end: datetime
    items: list[DigestItem]
    body_md: str
    status: DigestStatus = DigestStatus.DRAFT
    drafted_at: datetime
    approved_at: datetime | None = None
    published_at: datetime | None = None


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Approval(BaseModel):
    digest_id: str
    status: ApprovalStatus
    feedback: str | None = None
    edits: str | None = None  # optional edited markdown body
    received_at: datetime


# -------------- event log (for replay) -----------------------------------

# kinds the eval harness will look for. Implementations may emit additional
# kinds; the harness ignores unknown kinds for replay correctness.
EventLogKind = Literal[
    "wake",
    "fetch",
    "llm_call",
    "tool_call",
    "summary",
    "digest_draft",
    "approval",
    "publish",
    "error",
]


class EventLogEntry(BaseModel):
    """One line in state/events.jsonl. Append-only, replay-able."""

    ts: datetime  # fixture-time
    kind: EventLogKind | str
    payload: dict[str, Any] = Field(default_factory=dict)
