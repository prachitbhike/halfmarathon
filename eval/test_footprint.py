"""Unit tests for eval/footprint.py."""

from __future__ import annotations

from eval.footprint import (
    IMPL_OPS_METADATA,
    compute,
    compute_all,
)


def test_all_known_impls_have_ops_metadata() -> None:
    """Every impl id used in the matrix should have hand-curated metadata."""
    expected = {"langgraph", "temporal_pydantic", "letta", "claude_sdk"}
    assert expected <= set(IMPL_OPS_METADATA.keys())


def test_compute_returns_zero_for_unknown_impl() -> None:
    """Missing impls return zeroed footprint, not crash."""
    fp = compute("nonexistent", results=[])
    assert fp.lines_of_code == 0
    assert fp.source_files == 0
    assert fp.direct_deps == 0
    assert fp.mean_elapsed_s is None
    assert fp.services_to_run == 0
    assert fp.ops_steps == []


def test_mean_elapsed_excludes_zero_and_other_impls() -> None:
    fp = compute(
        "langgraph",
        results=[
            {"impl_id": "langgraph", "elapsed_s": 10.0},
            {"impl_id": "langgraph", "elapsed_s": 30.0},
            {"impl_id": "langgraph", "elapsed_s": 0.0},  # excluded (skipped)
            {"impl_id": "temporal_pydantic", "elapsed_s": 99.0},  # other impl
        ],
    )
    assert fp.mean_elapsed_s == 20.0


def test_mean_elapsed_none_when_no_runs() -> None:
    fp = compute(
        "claude_sdk",
        results=[{"impl_id": "claude_sdk", "elapsed_s": 0.0}],
    )
    assert fp.mean_elapsed_s is None


def test_actual_repo_loc_is_nonzero_for_real_impls() -> None:
    """Smoke against the live repo. Catches accidental dir renames."""
    fps = compute_all(
        ["langgraph", "temporal_pydantic", "claude_sdk"],
        results=[],
    )
    for fp in fps.values():
        assert fp.lines_of_code > 0, f"{fp.impl_id} had 0 LOC"
        assert fp.source_files > 0, f"{fp.impl_id} had 0 source files"
        assert fp.direct_deps > 0, f"{fp.impl_id} had 0 direct deps"


def test_setup_step_count_matches_ops_steps_length() -> None:
    fp = compute("langgraph", results=[])
    assert fp.setup_step_count == len(fp.ops_steps)
