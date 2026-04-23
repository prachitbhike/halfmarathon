"""Evaluation harness for the long-running-agent comparison.

Each dimension test is a small Python module under `eval/dimensions/` that:
    1. takes an implementation registry entry,
    2. runs that impl through a controlled scenario,
    3. inspects the resulting state-dir,
    4. returns a `DimensionResult` (status + notes + metrics).

The harness drives each (impl x dimension) combination and the report
renders the matrix as markdown.

Phase 2 covers the three fast dimensions: crash recovery (1), HITL gate (6),
replay (8). Phase 4 adds the slow / wall-clock dimensions.
"""
