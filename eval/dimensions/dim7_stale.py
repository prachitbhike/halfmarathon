"""Dimension 7 — stale external state.

Spec: while the agent sleeps, mutate the world it cares about (delete an
event the agent has already seen). Verify the agent detects/refreshes/fails
loudly rather than carrying stale state forward into a published digest.

Phase 5 implementation: a true two-phase test.
    Phase A — run on the FULL canonical timeline through fixture-day-12.
              The agent fetches and files all events that have happened by
              then (including the to-be-deleted ones).
    Mid-test — switch to a mutated timeline where target events are removed.
    Phase B — resume the impl through fixture-day-15 with the mutated
              fixtures dir. The agent's `since` cursor only refetches
              new events; KB still contains the now-deleted ones.
              Right behavior: re-check + drop or flag stale entries.

Score:
    - PASS: published digests do NOT reference any deleted events.
    - PARTIAL: impl completed but stale references persist in published
      digests (this is what every impl will do today — they don't re-check
      source state after filing).
    - FAIL: impl errored on either phase.
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
from eval.fixtures_override import build_override
from eval.impls import ImplSpec
from eval.scoring import clamp01
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 7
DIM_NAME = "Stale external state"

ROOT = Path(__file__).resolve().parents[2]
TIMELINE_PATH = ROOT / "task" / "fixtures" / "timeline.json"
MUTATIONS_PATH = ROOT / "task" / "fixtures" / "mutations.json"


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

    base = results_dir / "dim7" / spec.id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_phase_a_end = DEFAULT_FIXTURE_START + timedelta(days=12)
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0
    expected = expected_week_ids(fixture_start, fixture_until)
    for wid in expected:
        write_approval_for(base, wid, received_at=fixture_start + timedelta(days=8))

    if not MUTATIONS_PATH.exists():
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.SKIPPED,
            notes=f"Skipped: mutations fixture missing at {MUTATIONS_PATH}.",
        )
    muts = json.loads(MUTATIONS_PATH.read_text()).get("mutations", [])
    delete_ids = [m["event_id"] for m in muts if m.get("kind") == "delete"]

    timeline = {evt["id"]: evt for evt in json.loads(TIMELINE_PATH.read_text())}
    deleted_urls = [
        timeline[eid].get("url", "")
        for eid in delete_ids if eid in timeline
    ]

    # Phase A: canonical timeline (no override).
    t0 = time.perf_counter()
    try:
        await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_phase_a_end,
            speed=speed, thread_id="dim7",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes=f"Phase A errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )

    # Apply mutations: build override dir with the deletions.
    fixtures_override_dir = base / "_fixtures_override"
    build_override(fixtures_override_dir, delete_event_ids=delete_ids)

    # Phase B: same state-dir, mutated fixtures.
    try:
        result = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim7",
            fixtures_dir=fixtures_override_dir,
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.FAIL,
            notes=f"Phase B errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )
    elapsed = time.perf_counter() - t0

    digests = base / "digests"
    pub_bodies = (
        [p.read_text() for p in sorted(digests.glob("published-week-*.md"))]
        if digests.exists() else []
    )
    references_to_deleted = [
        url for url in deleted_urls if any(url in body for body in pub_bodies)
    ]

    n_targets = len(deleted_urls)
    leak_rate = (len(references_to_deleted) / n_targets) if n_targets else 0.0
    freshness = 0.0 if not pub_bodies else clamp01(1.0 - leak_rate)
    accuracy = freshness
    components = {"freshness": freshness, "leak_rate": leak_rate}
    explanation = "1 - (leaked_deleted_urls / total_deletion_targets)"

    metrics: dict[str, Any] = {
        "deletion_targets": delete_ids,
        "phase_a_end": fixture_phase_a_end.isoformat(),
        "published_count": len(pub_bodies),
        "stale_references": references_to_deleted,
        "leak_rate": round(leak_rate, 3),
        "summary": result,
    }

    def _result(status: DimensionStatus, notes: str) -> DimensionResult:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=status, notes=notes, metrics=metrics, elapsed_s=elapsed,
            accuracy=accuracy, accuracy_components=components,
            accuracy_explanation=explanation,
        )

    if not pub_bodies:
        return _result(DimensionStatus.FAIL, "No published digests to inspect.")

    if not references_to_deleted:
        return _result(
            DimensionStatus.PASS,
            f"Impl completed both phases without referencing any "
            f"deleted events ({len(deleted_urls)} deletion targets, "
            f"{len(pub_bodies)} published digests inspected). "
            f"Genuine refresh detected on resume.",
        )

    return _result(
        DimensionStatus.PARTIAL,
        f"Impl completed, but {len(references_to_deleted)} of "
        f"{len(deleted_urls)} deleted events were referenced in "
        f"published digests after Phase B. The KB carried items "
        f"forward without re-checking the source. Right behavior: "
        f"re-fetch on resume and drop or flag stale references.",
    )


if __name__ == "__main__":  # pragma: no cover
    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
