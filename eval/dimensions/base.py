"""Shared types + helpers used by every dimension test."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from task.digests import (
    published_path,
    week_id_for,
)
from task.digests import (
    write_approval as _write_approval,
)
from task.types import ApprovalStatus


class DimensionStatus(StrEnum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    SKIPPED = "skipped"  # impl unavailable in this env (e.g. needs API key)
    ERROR = "error"      # the test itself blew up


@dataclass
class DimensionResult:
    impl_id: str
    dimension_id: int   # 1..10 per plan §6
    dimension_name: str
    status: DimensionStatus
    notes: str          # 1-3 sentences for the matrix cell
    metrics: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None  # populated when status == ERROR
    # Numerical accuracy in [0.0, 1.0]. None for SKIPPED/ERROR so those cells
    # are excluded from the composite rather than counted as zeros.
    accuracy: float | None = None
    accuracy_components: dict[str, float] = field(default_factory=dict)
    accuracy_explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "impl_id": self.impl_id,
            "dimension_id": self.dimension_id,
            "dimension_name": self.dimension_name,
            "status": str(self.status),
            "notes": self.notes,
            "metrics": self.metrics,
            "elapsed_s": round(self.elapsed_s, 3),
            "error": self.error,
            "accuracy": None if self.accuracy is None else round(self.accuracy, 4),
            "accuracy_components": {
                k: round(v, 4) for k, v in self.accuracy_components.items()
            },
            "accuracy_explanation": self.accuracy_explanation,
        }


def expected_week_ids(start: datetime, until: datetime) -> list[str]:
    """Week ids the agent should publish across the [start, until) window.

    A digest is drafted on Sunday for the prior 7 days, so this enumerates
    each Sunday in the window and returns its (week_start - 7d) week id.
    """
    out: list[str] = []
    d = start
    while d < until:
        if d.weekday() == 6:
            week_start = (d - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            out.append(week_id_for(week_start))
        d += timedelta(days=1)
    return out


def write_approval_for(
    state_dir: Path,
    week_id: str,
    *,
    received_at: datetime,
    feedback: str = "(eval auto-approved)",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> Path:
    """Convenience wrapper around the harness's approval-file convention."""
    return _write_approval(
        state_dir, week_id,
        status=status,
        feedback=feedback,
        received_at=received_at,
    )


def count_published(state_dir: Path) -> int:
    digests = state_dir / "digests"
    if not digests.exists():
        return 0
    return len(list(digests.glob("published-week-*.md")))


def published_week_ids(state_dir: Path) -> list[str]:
    digests = state_dir / "digests"
    if not digests.exists():
        return []
    return sorted(
        p.stem.removeprefix("published-")
        for p in digests.glob("published-week-*.md")
    )


def file_byte_count(state_dir: Path, week_id: str) -> int | None:
    p = published_path(state_dir, week_id)
    if not p.exists():
        return None
    return p.stat().st_size
