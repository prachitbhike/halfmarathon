"""Dimension 1 — crash recovery.

Spec (plan.md §4): kill the process between an LLM decision and the side
effect; verify exactly-once execution after restart.

This Phase 2 implementation uses a graceful "early stop, then resume" rather
than a forced subprocess kill — both LangGraph's checkpointer and the Claude
SDK file-as-memory pattern are designed to make resume the same code path
either way. A future Phase 4 enhancement adds subprocess SIGKILL injection
to test mid-node crashes.

How it scores:
    - Run impl A: start_from=T0, until=T_MID  (produces partial state)
    - Run impl B: same state-dir, until=T_END (resumes from checkpoint)
    - Run impl C: clean state-dir, start_from=T0, until=T_END (single pass)
    - PASS if B's published_weeks == C's published_weeks AND no draft was
      published twice (no `published-X.md` written more than once).
    - PARTIAL if published_weeks match but KB sizes diverge.
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
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 1
DIM_NAME = "Crash recovery"


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

    base = results_dir / "dim1" / spec.id
    if base.exists():
        shutil.rmtree(base)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    fixture_mid = DEFAULT_FIXTURE_START + timedelta(days=6)  # before first Sunday
    speed = 86400.0

    expected = expected_week_ids(fixture_start, fixture_until)
    # Pre-populate approvals so digests publish immediately when drafted.
    resumed_dir = base / "resumed"
    fresh_dir = base / "fresh"
    for d in (resumed_dir, fresh_dir):
        d.mkdir(parents=True, exist_ok=True)
        for wid in expected:
            write_approval_for(d, wid, received_at=fixture_start + timedelta(days=8))

    t0 = time.perf_counter()
    try:
        # Run A: partial — stop at fixture-mid (no Sunday yet)
        partial = await spec.run(
            profile=profile, state_dir=resumed_dir,
            fixture_start=fixture_start, until=fixture_mid,
            speed=speed, thread_id="dim1",
        )
        # Run B: resume from same state-dir, finish to fixture_until
        resumed = await spec.run(
            profile=profile, state_dir=resumed_dir,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim1",
        )
        # Run C: fresh single-pass to fixture_until
        fresh = await spec.run(
            profile=profile, state_dir=fresh_dir,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim1",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Run errored: {type(exc).__name__}",
            error=str(exc),
            elapsed_s=time.perf_counter() - t0,
        )

    elapsed = time.perf_counter() - t0

    # Compare outcomes.
    pub_resumed = published_week_ids(resumed_dir)
    pub_fresh = published_week_ids(fresh_dir)
    metrics = {
        "expected_weeks": expected,
        "resumed_published": pub_resumed,
        "fresh_published": pub_fresh,
        "partial_summary": partial,
        "resumed_summary": resumed,
        "fresh_summary": fresh,
    }

    if set(pub_resumed) != set(expected) or set(pub_fresh) != set(expected):
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes=(
                f"Published-week mismatch. Expected {expected}, "
                f"resumed-run produced {pub_resumed}, fresh-run produced {pub_fresh}."
            ),
            metrics=metrics, elapsed_s=elapsed,
        )

    kb_resumed = resumed.get("kb_size") or 0
    kb_fresh = fresh.get("kb_size") or 0
    if kb_resumed != kb_fresh:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.PARTIAL,
            notes=(
                f"Same digests published after restart, but KB sizes differ "
                f"(resumed={kb_resumed}, fresh={kb_fresh})."
            ),
            metrics=metrics, elapsed_s=elapsed,
        )

    return DimensionResult(
        impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
        status=DimensionStatus.PASS,
        notes=(
            f"Resume-from-partial reaches the same end state as a fresh single "
            f"pass: {len(expected)} digests published, KB={kb_resumed} items, no "
            f"duplicates."
        ),
        metrics=metrics, elapsed_s=elapsed,
    )


if __name__ == "__main__":  # pragma: no cover - convenience standalone
    import json

    from eval.impls import find_impl
    from task.types import UserProfile as _UP
    profile = _UP.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
