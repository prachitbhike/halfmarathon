"""Project per-cell costs into per-published-digest and at-scale numbers.

The matrix has measured cost data for impls run with a real LLM (currently
just claude_sdk). For impls run in offline mock mode, the "framework" adds
~zero cost beyond the model API itself — but a developer adopting them
WILL pay model token costs, so we surface an estimate based on the typical
per-week token usage observed in the fixtures.

The estimate is conservative — it counts: ~30 events scored per fixture-week
in batches (~1k input tokens per batch), ~20 events summarized at ~500
tokens each, against Sonnet 4.6 published prices. Real numbers will vary
~2x in either direction based on user interests and content density.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sonnet 4.6 published token prices (per million).
_SONNET_INPUT_USD_PER_M = 3.00
_SONNET_OUTPUT_USD_PER_M = 15.00

# Per-week typical token consumption when running with a real LLM, derived
# from observed event counts in the canonical fixtures (~30 events scored,
# ~10-20 surfaced for summarization).
_INPUT_TOKENS_PER_DIGEST = 30_000
_OUTPUT_TOKENS_PER_DIGEST = 10_000


def _estimated_real_llm_cost_per_digest() -> float:
    """Per-digest token cost for langgraph + temporal_pydantic if you swap
    the offline mock for the real LLM. Conservative central estimate."""
    return (
        _INPUT_TOKENS_PER_DIGEST * _SONNET_INPUT_USD_PER_M / 1_000_000
        + _OUTPUT_TOKENS_PER_DIGEST * _SONNET_OUTPUT_USD_PER_M / 1_000_000
    )


@dataclass(frozen=True)
class CostProjection:
    impl_id: str
    cost_per_digest_usd: float | None  # None if no published digests
    is_measured: bool        # True if from a real-LLM run; False if estimated
    note: str


def _digests_published(impl_id: str, results: list[dict]) -> int:
    """Best-effort sum of published digests across that impl's cells."""
    keys = (
        "resumed_published", "multi_published", "published",
        "published_after_phase_b", "run_a_published",
    )
    total = 0
    for r in results:
        if r.get("impl_id") != impl_id:
            continue
        metrics = r.get("metrics") or {}
        for key in keys:
            v = metrics.get(key)
            if isinstance(v, list):
                total += len(v)
                break  # only count one per cell, not per metric key
    return total


def _measured_cost(impl_id: str, results: list[dict]) -> float:
    """Sum of all estimated_cost_usd / tick_cost_usd for this impl."""
    total = 0.0
    for r in results:
        if r.get("impl_id") != impl_id:
            continue
        metrics = r.get("metrics") or {}
        for key in (
            "summary", "fresh_summary", "resumed_summary",
            "multi_summary", "phase_a_summary", "phase_b_summary",
            "run_a_summary", "run_b_summary",
        ):
            sub = metrics.get(key)
            if isinstance(sub, dict):
                total += float(sub.get("estimated_cost_usd") or 0.0)
                total += float(sub.get("tick_cost_usd") or 0.0)
    return total


def project_for_impl(impl_id: str, results: list[dict]) -> CostProjection:
    measured = _measured_cost(impl_id, results)
    published = _digests_published(impl_id, results)
    if measured > 0 and published > 0:
        return CostProjection(
            impl_id=impl_id,
            cost_per_digest_usd=measured / published,
            is_measured=True,
            note=f"Measured: ${measured:.2f} / {published} digest(s).",
        )
    # Offline mock or no digests measured — fall back to estimate.
    return CostProjection(
        impl_id=impl_id,
        cost_per_digest_usd=_estimated_real_llm_cost_per_digest(),
        is_measured=False,
        note="Estimated from typical token usage with Sonnet 4.6 (~30k input, "
             "~10k output per digest).",
    )


# Workload tiers that map to "1 user" → "small SaaS" → "large SaaS".
# digests/week framing is honest for the Release Radar task.
WORKLOAD_TIERS: list[tuple[str, int]] = [
    ("Personal (1/wk)", 1),
    ("Small team (10/wk)", 10),
    ("Small SaaS (50/wk)", 50),
    ("Large SaaS (5000/wk)", 5000),
]


def project_at_scale(
    cp: CostProjection, digests_per_week: int,
) -> float | None:
    if cp.cost_per_digest_usd is None:
        return None
    return cp.cost_per_digest_usd * digests_per_week
