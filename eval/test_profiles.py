"""Unit tests for the profile-weighting helpers in eval/profiles.py."""

from __future__ import annotations

from eval.profiles import (
    PROFILES,
    Profile,
    score_all,
    score_impl_under_profile,
)


def test_equal_weight_profile_equals_simple_mean() -> None:
    """A profile with no boosted dims = arithmetic mean across run dims."""
    prof = Profile(name="flat", description="", weights={})
    res = score_impl_under_profile(
        prof, impl_id="x",
        impl_dim_scores={1: 1.0, 2: 0.5, 3: 0.0},
    )
    assert res.composite == (1.0 + 0.5 + 0.0) / 3


def test_boost_pulls_score_toward_boosted_dim() -> None:
    prof = Profile(
        name="boosty", description="",
        weights={1: 4.0},  # heavily boost dim 1
    )
    res = score_impl_under_profile(
        prof, impl_id="x",
        impl_dim_scores={1: 1.0, 2: 0.0, 3: 0.0},
    )
    # (1.0*4 + 0.0*1 + 0.0*1) / (4+1+1) = 4/6 ≈ 0.667
    assert abs(res.composite - 4 / 6) < 1e-6


def test_skipped_cells_excluded_from_both_numerator_and_denominator() -> None:
    """A None score doesn't drag the composite down."""
    prof = Profile(name="flat", description="", weights={})
    full = score_impl_under_profile(
        prof, impl_id="full",
        impl_dim_scores={1: 1.0, 2: 1.0},
    )
    partial = score_impl_under_profile(
        prof, impl_id="partial",
        impl_dim_scores={1: 1.0, 2: None},
    )
    assert full.composite == 1.0
    assert partial.composite == 1.0  # not 0.5


def test_low_coverage_returns_none() -> None:
    """Impl that didn't run any of the boosted dims gets no composite."""
    prof = Profile(
        name="hitl", description="",
        weights={6: 3.0, 7: 2.0},
        min_coverage_pct=0.5,
    )
    res = score_impl_under_profile(
        prof, impl_id="claude",
        impl_dim_scores={1: 1.0, 8: 0.5},  # ran neither dim 6 nor 7
    )
    assert res.composite is None
    assert res.coverage_pct == 0.0


def test_partial_coverage_still_scores_if_above_threshold() -> None:
    prof = Profile(
        name="hitl", description="",
        weights={6: 3.0, 7: 2.0},
        min_coverage_pct=0.5,
    )
    res = score_impl_under_profile(
        prof, impl_id="claude",
        impl_dim_scores={6: 1.0, 7: None, 8: 0.5},  # ran 1/2 boosted
    )
    # Coverage = 1/2 = 0.5, threshold met. Composite uses dim 6 + dim 8.
    # (1.0*3 + 0.5*1) / (3+1) = 3.5/4 = 0.875
    assert res.composite is not None
    assert abs(res.composite - 0.875) < 1e-6


def test_score_all_returns_one_entry_per_impl_per_profile() -> None:
    out = score_all({
        "a": {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.5, 6: 1.0, 7: 0.5, 8: 1.0},
        "b": {6: 1.0, 8: 0.5},
    })
    for prof in PROFILES:
        assert prof.name in out
        ids = [ps.impl_id for ps in out[prof.name]]
        assert ids == ["a", "b"]


def test_realistic_three_impl_scoring() -> None:
    """End-to-end: three impls similar to what's in the matrix today."""
    scores = score_all({
        "claude_sdk": {6: 1.0, 8: 0.66},
        "langgraph": {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.69, 6: 1.0, 7: 0.50, 8: 1.0},
        "temporal_pydantic": {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0.69, 6: 1.0, 7: 0.50, 8: 1.0},
    })
    # claude_sdk should be excluded from "Production durability" (boosted
    # dims include 1, 2, 7 — none of which it ran).
    durability = {ps.impl_id: ps for ps in scores["Production durability"]}
    assert durability["claude_sdk"].composite is None
    assert durability["langgraph"].composite is not None
    assert durability["temporal_pydantic"].composite is not None
    # langgraph and temporal_pydantic tied because identical scores.
    assert (
        abs(durability["langgraph"].composite - durability["temporal_pydantic"].composite)
        < 1e-9
    )
