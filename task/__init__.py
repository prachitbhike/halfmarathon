"""Shared task spec, types, fixtures, and fixture clock for halfmarathon.

All four implementations import from this package.
Implementations MUST NOT modify these — they are the cross-impl contract.
"""

from task.types import (
    Approval,
    ApprovalStatus,
    Digest,
    DigestItem,
    DigestStatus,
    EventLogEntry,
    KnowledgeBaseItem,
    Source,
    SourceEvent,
    SourceKind,
    UserProfile,
)

__all__ = [
    "Approval",
    "ApprovalStatus",
    "Digest",
    "DigestItem",
    "DigestStatus",
    "EventLogEntry",
    "KnowledgeBaseItem",
    "Source",
    "SourceEvent",
    "SourceKind",
    "UserProfile",
]
