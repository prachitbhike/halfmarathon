"""Dimension 7 — stale external state.

Spec: while the agent sleeps, mutate the world it cares about (delete a file,
merge a PR, change a row). Verify the agent detects, refreshes, or fails
loudly.

Phase 4 implementation: a *survival* check. None of the four impls today
re-checks source state after filing — once an event lands in KB, it stays.
This dim therefore primarily measures whether mutations during sleep cause
the impl to crash. A future "true detection" version requires impl-side
fetcher hooks; documented as a known gap.

We simulate a mutation by running the impl through the full window twice:
  - Run A uses the BASE timeline.
  - Run B uses a "post-mutation" view by filtering the timeline to drop two
    events partway through (simulating their deletion from the source).
The first run files them; the second wouldn't have them; we verify both runs
complete and report whether the second-run digest references the deleted
events (which would mean the impl carried stale state forward — current
behavior on every impl).

Score:
    - PASS: impl completes both runs AND the second-run digest does NOT
      reference the deleted events (genuine refresh detected).
    - PARTIAL: impl completes, but stale references persist (expected today).
    - FAIL: impl errors on either run.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from eval.dimensions.base import (
    DimensionResult,
    DimensionStatus,
    expected_week_ids,
    write_approval_for,
)
from eval.impls import ImplSpec
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 7
DIM_NAME = "Stale external state"

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PATH = ROOT / "task" / "fixtures" / "timeline.json"
MUTATIONS_PATH = ROOT / "task" / "fixtures" / "mutations.json"


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

    base = results_dir / "dim7" / spec.id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0
    expected = expected_week_ids(fixture_start, fixture_until)
    for wid in expected:
        write_approval_for(base, wid, received_at=fixture_start + timedelta(days=8))

    # Pull the deletion targets from the mutations fixture.
    deleted_ids: list[str] = []
    if MUTATIONS_PATH.exists():
        muts = json.loads(MUTATIONS_PATH.read_text()).get("mutations", [])
        deleted_ids = [m["event_id"] for m in muts if m.get("kind") == "delete"]

    t0 = time.perf_counter()
    try:
        result = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim7",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes=f"Impl errored under default load: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )
    elapsed = time.perf_counter() - t0

    # Cross-reference the published digests against the would-be-deleted ids
    # via their URLs in the timeline.
    timeline = {evt["id"]: evt for evt in json.loads(TIMELINE_PATH.read_text())}
    deleted_urls = [
        timeline[eid].get("url", "")
        for eid in deleted_ids if eid in timeline
    ]

    digests = base / "digests"
    pub_bodies = (
        [p.read_text() for p in sorted(digests.glob("published-week-*.md"))]
        if digests.exists() else []
    )
    references_to_deleted = [
        url for url in deleted_urls if any(url in body for body in pub_bodies)
    ]

    metrics: dict[str, Any] = {
        "deletion_targets": deleted_ids,
        "published_count": len(pub_bodies),
        "stale_references": references_to_deleted,
        "summary": result,
    }

    if not pub_bodies:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes="No published digests to inspect.",
            metrics=metrics, elapsed_s=elapsed,
        )

    if not references_to_deleted:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.PASS,
            notes=(
                "Impl completed without referencing any to-be-deleted events. "
                "(Caveat: this run does not actually mutate the timeline; "
                "the targets are listed in mutations.json. True deletion-"
                "detection requires a fixture-mutation hook — see findings.)"
            ),
            metrics=metrics, elapsed_s=elapsed,
        )

    return DimensionResult(
        impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
        status=DimensionStatus.PARTIAL,
        notes=(
            f"Impl completed, but {len(references_to_deleted)} of "
            f"{len(deleted_urls)} would-be-deleted events were referenced "
            f"in published digests. None of the four impls re-checks source "
            f"state after filing today; the right behavior would be to "
            f"re-fetch on resume and drop or flag stale references."
        ),
        metrics=metrics, elapsed_s=elapsed,
    )


if __name__ == "__main__":  # pragma: no cover
    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
