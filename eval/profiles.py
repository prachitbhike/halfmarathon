"""Use-case weighting profiles for the composite accuracy score.

A single equal-weighted composite hides which workload an impl is strong
for. Each profile re-weights the per-dimension accuracy scores to reflect
what a particular kind of project actually cares about, and `report.py`
emits one composite column per profile.

Adding a new profile is a one-liner — see `PROFILES` below.

Composite formula (per profile, per impl):
    composite = sum(score[d] * weight[d]) / sum(weight[d])
where the sum is over dimensions the impl actually ran (skipped/errored
cells are excluded from numerator AND denominator). If the impl ran fewer
than `min_coverage_pct` of the WEIGHTED dims, the composite is reported as
None ("—") for that profile rather than a misleadingly small subset score.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str             # short label for the matrix column
    description: str      # one-sentence "best for" framing
    weights: dict[int, float]  # dim_id -> weight; missing dims default to 1.0
    # Coverage threshold: an impl must have run at least this fraction of
    # the *weighted* dims (those with weight > default_weight) to get a
    # composite score in this profile. Prevents dim-6-only claude_sdk from
    # winning a profile that critically depends on dims 1-3.
    min_coverage_pct: float = 0.5
    default_weight: float = 1.0


# 8 dimensions, with their canonical weights = 1.0; profiles boost the dims
# that matter for that workload.
PROFILES: list[Profile] = [
    Profile(
        name="Production durability",
        description=(
            "Multi-day agents that survive crashes, deploys, and source "
            "mutations. Boosts crash recovery, multi-restart, and "
            "stale-state detection."
        ),
        weights={1: 3.0, 2: 3.0, 7: 2.0, 6: 1.5},
    ),
    Profile(
        name="Compliance / audit",
        description=(
            "Workloads where every action must be reproducible and "
            "auditable. Boosts replay determinism and crash-recovery "
            "fidelity."
        ),
        weights={8: 3.0, 1: 2.0, 2: 1.5, 7: 1.5},
    ),
    Profile(
        name="Quality-sensitive",
        description=(
            "Content-quality-critical use (digests / summaries / "
            "recommendations). Boosts goal-drift resistance and memory "
            "filing."
        ),
        weights={5: 3.0, 4: 2.0, 3: 1.5, 8: 1.5},
    ),
    Profile(
        name="HITL-critical",
        description=(
            "High-stakes flows that gate on human approval. Boosts the "
            "approval-gate dim and stale-state detection so the human "
            "isn't approving stale content."
        ),
        weights={6: 3.0, 7: 2.0, 1: 1.5},
    ),
    Profile(
        name="Memory-driven",
        description=(
            "Personal-assistant or research-radar patterns where memory "
            "across long horizons IS the value prop. Boosts memory recall "
            "and continuity."
        ),
        weights={4: 3.0, 3: 2.0, 2: 1.5, 7: 1.5},
    ),
]


@dataclass(frozen=True)
class ProfileScore:
    profile_name: str
    impl_id: str
    composite: float | None
    coverage_pct: float
    contributing_dims: list[int]


def score_impl_under_profile(
    profile: Profile,
    *,
    impl_id: str,
    impl_dim_scores: dict[int, float | None],
) -> ProfileScore:
    """Score one impl under one profile.

    `impl_dim_scores` maps dim_id -> accuracy (None if skipped/errored).
    """
    weighted_dim_ids = [d for d, w in profile.weights.items() if w > profile.default_weight]
    weighted_run = [d for d in weighted_dim_ids if impl_dim_scores.get(d) is not None]
    coverage = (len(weighted_run) / len(weighted_dim_ids)) if weighted_dim_ids else 1.0

    if coverage < profile.min_coverage_pct:
        return ProfileScore(
            profile_name=profile.name, impl_id=impl_id,
            composite=None, coverage_pct=coverage,
            contributing_dims=[],
        )

    num = 0.0
    den = 0.0
    contributing: list[int] = []
    for dim_id, score in impl_dim_scores.items():
        if score is None:
            continue
        weight = profile.weights.get(dim_id, profile.default_weight)
        num += score * weight
        den += weight
        contributing.append(dim_id)

    composite = num / den if den > 0 else None
    return ProfileScore(
        profile_name=profile.name, impl_id=impl_id,
        composite=composite, coverage_pct=coverage,
        contributing_dims=sorted(contributing),
    )


def score_all(
    impl_dim_scores_by_impl: dict[str, dict[int, float | None]],
) -> dict[str, list[ProfileScore]]:
    """Return {profile_name: [ProfileScore per impl]}."""
    out: dict[str, list[ProfileScore]] = {}
    for prof in PROFILES:
        out[prof.name] = [
            score_impl_under_profile(
                prof, impl_id=impl_id, impl_dim_scores=scores,
            )
            for impl_id, scores in sorted(impl_dim_scores_by_impl.items())
        ]
    return out


__all__ = [
    "PROFILES",
    "Profile",
    "ProfileScore",
    "score_all",
    "score_impl_under_profile",
]
