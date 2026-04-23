"""Dimension test modules.

Each module exposes a `run(spec, *, results_dir, profile)` function returning a
`DimensionResult`. The harness collects these into a matrix.

Phase 2 implements three: dim1_crash, dim6_hitl, dim8_replay.
"""

from eval.dimensions.base import (
    DimensionResult,
    DimensionStatus,
    expected_week_ids,
    write_approval_for,
)

__all__ = [
    "DimensionResult",
    "DimensionStatus",
    "expected_week_ids",
    "write_approval_for",
]
