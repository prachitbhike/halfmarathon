"""Dimension test modules.

Each module exposes a `run(spec, *, results_dir, profile)` function returning a
`DimensionResult`. The harness collects these into a matrix.

Phase 2 added the deterministic three: dim1_crash, dim6_hitl, dim8_replay.
Phase 4 added the wall-clock-bound five: dim2_multiday, dim3_context,
dim4_memory, dim5_drift, dim7_stale.
"""

from eval.dimensions import (
    dim1_crash,
    dim2_multiday,
    dim3_context,
    dim4_memory,
    dim5_drift,
    dim6_hitl,
    dim7_stale,
    dim8_replay,
)
from eval.dimensions.base import (
    DimensionResult,
    DimensionStatus,
    expected_week_ids,
    write_approval_for,
)

__all__ = [
    "DimensionResult",
    "DimensionStatus",
    "dim1_crash",
    "dim2_multiday",
    "dim3_context",
    "dim4_memory",
    "dim5_drift",
    "dim6_hitl",
    "dim7_stale",
    "dim8_replay",
    "expected_week_ids",
    "write_approval_for",
]
