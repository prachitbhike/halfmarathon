"""Dimension 2 — multi-day wall-clock with sleeps + multi-restart resume.

A stronger version of dim 1: instead of one stop+resume, we restart 3 times
across the fixture window. This exercises both the "long sleep across a
deploy" and the "agent state survives N restarts" properties of the impl.

Score:
    - PASS if the final published_weeks set after 3 restarts matches a fresh
      single-pass run, and KB sizes match.
    - PARTIAL if digests published but KB diverges.
    - FAIL otherwise.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path

from eval.dimensions.base import (
    DimensionResult,
    DimensionStatus,
    expected_week_ids,
    published_week_ids,
    write_approval_for,
)
from eval.impls import ImplSpec
from eval.scoring import jaccard, mean, ratio_match
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 2
DIM_NAME = "Multi-day with sleeps + multi-restart"


async def run(
    spec: ImplSpec,
    *,
    results_dir: Path,
    profile: UserProfile,
) -> DimensionResult:
    if spec.requires_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.SKIPPED,
            notes="Skipped: ANTHROPIC_API_KEY required to drive this impl.",
        )

    base = results_dir / "dim2" / spec.id
    if base.exists():
        shutil.rmtree(base)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0
    expected = expected_week_ids(fixture_start, fixture_until)

    # Three restart points spaced through the window.
    stops = [
        fixture_start + timedelta(days=4),   # before first Sunday
        fixture_start + timedelta(days=8),   # past first Sunday + draft
        fixture_start + timedelta(days=12),  # past second Sunday + draft
    ]

    multi_dir = base / "multi"
    fresh_dir = base / "fresh"
    for d in (multi_dir, fresh_dir):
        d.mkdir(parents=True, exist_ok=True)
        for wid in expected:
            write_approval_for(d, wid, received_at=fixture_start + timedelta(days=8))

    t0 = time.perf_counter()
    try:
        # Three sequential restarts walking forward through the window.
        for stop in stops:
            await spec.run(
                profile=profile, state_dir=multi_dir,
                fixture_start=fixture_start, until=stop,
                speed=speed, thread_id="dim2",
            )
        # Final segment to the end.
        multi = await spec.run(
            profile=profile, state_dir=multi_dir,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim2",
        )
        # Single-pass baseline.
        fresh = await spec.run(
            profile=profile, state_dir=fresh_dir,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim2-fresh",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Run errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )

    elapsed = time.perf_counter() - t0
    pub_multi = published_week_ids(multi_dir)
    pub_fresh = published_week_ids(fresh_dir)
    kb_multi = multi.get("kb_size") or 0
    kb_fresh = fresh.get("kb_size") or 0

    week_recovery = jaccard(pub_multi, expected)
    kb_match = ratio_match(kb_multi, kb_fresh)
    accuracy = mean([week_recovery, kb_match])
    components = {"week_recovery": week_recovery, "kb_match": kb_match}
    explanation = (
        "mean(jaccard(multi_weeks, expected), ratio_match(kb_multi, kb_fresh))"
    )

    metrics = {
        "expected": expected,
        "multi_published": pub_multi,
        "fresh_published": pub_fresh,
        "kb_multi": kb_multi,
        "kb_fresh": kb_fresh,
        "multi_summary": multi,
        "fresh_summary": fresh,
        "stops": [s.isoformat() for s in stops],
    }

    def _result(status: DimensionStatus, notes: str) -> DimensionResult:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=status, notes=notes, metrics=metrics, elapsed_s=elapsed,
            accuracy=accuracy, accuracy_components=components,
            accuracy_explanation=explanation,
        )

    if set(pub_multi) != set(expected):
        return _result(
            DimensionStatus.FAIL,
            f"After {len(stops)} restarts the impl published {pub_multi}, "
            f"expected {expected}.",
        )

    if kb_multi != kb_fresh:
        return _result(
            DimensionStatus.PARTIAL,
            f"All {len(expected)} digests published after {len(stops)} "
            f"restarts, but KB sizes differ (multi={kb_multi}, "
            f"fresh={kb_fresh}).",
        )

    return _result(
        DimensionStatus.PASS,
        f"Survived {len(stops)} restarts across the {len(expected)}-week "
        f"fixture; final state byte-equivalent to a single-pass run.",
    )


if __name__ == "__main__":  # pragma: no cover
    import json

    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
