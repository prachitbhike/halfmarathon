"""Unit tests for eval/cost_projection.py."""

from __future__ import annotations

from eval.cost_projection import (
    WORKLOAD_TIERS,
    project_at_scale,
    project_for_impl,
)


def test_measured_cost_used_when_present() -> None:
    """If a real-LLM run produced cost data, use it; don't fall back to estimate."""
    results = [
        {
            "impl_id": "claude_sdk",
            "metrics": {
                "summary": {"tick_cost_usd": 1.43},
                "phase_b_summary": {"published_after_phase_b": ["W14"]},
                "published_after_phase_b": ["W14"],
            },
        },
    ]
    cp = project_for_impl("claude_sdk", results)
    assert cp.is_measured
    assert cp.cost_per_digest_usd is not None
    assert cp.cost_per_digest_usd > 0


def test_estimated_cost_for_offline_impls() -> None:
    """Offline runs (cost=0) get the conservative real-LLM estimate."""
    results = [{"impl_id": "langgraph", "metrics": {"summary": {"estimated_cost_usd": 0}}}]
    cp = project_for_impl("langgraph", results)
    assert not cp.is_measured
    # Should be close to ($3 * 30k + $15 * 10k) / 1M = 0.24
    assert 0.20 < (cp.cost_per_digest_usd or 0) < 0.30


def test_project_at_scale_multiplies_linearly() -> None:
    cp = project_for_impl("langgraph", [])
    assert cp.cost_per_digest_usd is not None
    one = project_at_scale(cp, 1)
    fifty = project_at_scale(cp, 50)
    assert one is not None and fifty is not None
    assert abs(fifty - 50 * one) < 1e-9


def test_workload_tiers_cover_personal_and_saas_scales() -> None:
    labels = [name for name, _ in WORKLOAD_TIERS]
    digests = [n for _, n in WORKLOAD_TIERS]
    assert any("Personal" in lab for lab in labels)
    assert any("SaaS" in lab for lab in labels)
    assert digests == sorted(digests)  # ascending
    assert digests[0] == 1 and digests[-1] >= 1000


def test_digest_counting_uses_at_most_one_key_per_cell() -> None:
    """A cell with both 'published' and 'multi_published' shouldn't double-count."""
    results = [{
        "impl_id": "lg",
        "metrics": {
            "summary": {"estimated_cost_usd": 1.0},
            "published": ["W1", "W2"],
            "multi_published": ["W1", "W2"],
        },
    }]
    cp = project_for_impl("lg", results)
    # 1.0 measured / 2 digests = 0.50; not 1.0/4
    assert cp.is_measured
    assert cp.cost_per_digest_usd is not None
    assert abs(cp.cost_per_digest_usd - 0.5) < 1e-6


def test_no_cost_no_digests_returns_estimate_anyway() -> None:
    """Skip-only impls should still get an estimated per-digest cost so the
    matrix isn't full of dashes."""
    cp = project_for_impl("ghost_impl", [])
    assert not cp.is_measured
    assert cp.cost_per_digest_usd is not None
