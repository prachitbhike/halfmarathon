"""Dimension 6 — HITL gate spanning hours.

Spec (plan.md §4): agent proposes a high-stakes action; humans approve N
hours later. Verify state survives, no double-execution, context still
coherent.

How it scores:
    - Phase A: run with start_from=T0, until=AFTER_FIRST_SUNDAY,
      with NO approval files pre-written. The agent should draft the digest
      for the past week and then be in a "waiting on approval" state.
    - Verify: at least one draft exists, zero published, the run terminated
      cleanly without crash.
    - Phase B: write approval file, then resume the impl with until=T_END.
      Verify exactly the expected digests get published, no duplicates.
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
from eval.scoring import mean
from task.clock import DEFAULT_FIXTURE_START
from task.digests import draft_path
from task.types import UserProfile

DIM_ID = 6
DIM_NAME = "HITL gate spanning hours"


async def run(  # noqa: PLR0911
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

    base = results_dir / "dim6" / spec.id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    # Wed -> Wed (covers first Sunday Apr 5)
    fixture_after_sunday = DEFAULT_FIXTURE_START + timedelta(days=7)
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0

    # Plan: only test the FIRST week's digest. We pre-approve the second
    # week so the second resume run completes cleanly without polling forever.
    expected_all = expected_week_ids(fixture_start, fixture_until)
    test_wid = expected_all[0]  # the one we hold approval on
    later_wids = expected_all[1:]
    for wid in later_wids:
        write_approval_for(base, wid, received_at=fixture_start + timedelta(days=10))

    t0 = time.perf_counter()
    try:
        # Phase A: run until just past the first Sunday with NO approval for it.
        first_run = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_after_sunday,
            speed=speed, thread_id="dim6",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Phase A errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )

    # State after Phase A: draft should exist, NOT published (no approval).
    pub_after_a = published_week_ids(base)
    drafted_test = draft_path(base, test_wid).exists()

    # Components, filled in as phases complete. Unknown signals stay 0.
    comp = {
        "gate_held": 1.0 if test_wid not in pub_after_a else 0.0,
        "drafted_before_approval": 1.0 if drafted_test else 0.0,
        "published_after_approval": 0.0,
        "published_nonempty": 0.0,
    }
    explanation = (
        "mean(gate_held: no unauthorized publish in Phase A, "
        "drafted_before_approval, published_after_approval, published_nonempty)"
    )

    metrics_so_far = {
        "expected_first_week": test_wid,
        "drafted_after_phase_a": drafted_test,
        "published_after_phase_a": pub_after_a,
        "phase_a_summary": first_run,
    }

    if test_wid in pub_after_a:
        # Bug: agent published without an approval signal.
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes=(
                f"Agent published {test_wid} during Phase A without an approval "
                f"file present. HITL gate is not honored."
            ),
            metrics=metrics_so_far,
            elapsed_s=time.perf_counter() - t0,
            accuracy=mean(comp.values()),
            accuracy_components=comp,
            accuracy_explanation=explanation,
        )
    if not drafted_test:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.PARTIAL,
            notes=(
                f"Phase A finished without drafting {test_wid}. The agent "
                f"may not have reached the Sunday wake before the until cap; "
                f"increase the window if so."
            ),
            metrics=metrics_so_far,
            elapsed_s=time.perf_counter() - t0,
            accuracy=mean(comp.values()),
            accuracy_components=comp,
            accuracy_explanation=explanation,
        )

    # Phase B: write the held approval, then resume.
    write_approval_for(
        base, test_wid,
        received_at=fixture_start + timedelta(days=6, hours=4),  # 4 fixture-hours after draft
        feedback="(eval delayed approval)",
    )

    try:
        second_run = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim6",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Phase B errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )

    elapsed = time.perf_counter() - t0
    pub_final = published_week_ids(base)
    pub_path = base / "digests" / f"published-{test_wid}.md"
    comp["published_after_approval"] = 1.0 if test_wid in pub_final else 0.0
    comp["published_nonempty"] = (
        1.0 if pub_path.exists() and pub_path.stat().st_size > 0 else 0.0
    )
    accuracy = mean(comp.values())

    metrics = {
        **metrics_so_far,
        "published_after_phase_b": pub_final,
        "phase_b_summary": second_run,
    }

    def _result(status: DimensionStatus, notes: str) -> DimensionResult:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=status, notes=notes, metrics=metrics, elapsed_s=elapsed,
            accuracy=accuracy, accuracy_components=comp,
            accuracy_explanation=explanation,
        )

    if test_wid not in pub_final:
        return _result(
            DimensionStatus.FAIL,
            f"After approval was written and the impl resumed, {test_wid} "
            f"was not published. Final published set: {pub_final}.",
        )

    if not pub_path.exists() or pub_path.stat().st_size == 0:
        return _result(
            DimensionStatus.FAIL,
            f"published-{test_wid}.md is missing or empty after resume.",
        )

    return _result(
        DimensionStatus.PASS,
        f"Held-approval flow honored: drafted in Phase A without "
        f"publishing, picked up the approval after Phase B and published "
        f"{test_wid} (no double-publish).",
    )


if __name__ == "__main__":  # pragma: no cover
    import json

    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
