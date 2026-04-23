"""Dimension 4 — memory recall over time.

Spec: plant a fact in week 1, query in week 12; verify update propagates if
the fact is later corrected.

Phase 5 implementation: a *filing* check using the fixture-override
mechanism. We materialize a temp fixtures dir with the probe event added to
timeline.json and run the impl against it. Then verify the probe event
landed in the published digest (which is observable for every impl).

Real recall — asking the agent "do you remember X?" after weeks of unrelated
input — would need an additional impl-side "ask the agent" hook. Out of
scope for now; tracked as a known gap.

Score:
    - PASS: probe event referenced (by URL) in at least one published digest.
    - PARTIAL: probe filed in KB (where observable) but not in any digest.
    - FAIL: probe present in fixture but not anywhere in the impl's outputs.
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
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 4
DIM_NAME = "Memory recall (filing)"

ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "task" / "fixtures" / "memory-probes.json"


def _load_kb_for_impl(state_dir: Path) -> list[dict] | None:
    p = state_dir / "knowledge_base.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return []
    return None


def _digest_bodies(state_dir: Path) -> list[str]:
    digests = state_dir / "digests"
    if not digests.exists():
        return []
    return [
        p.read_text() for p in sorted(digests.glob("published-week-*.md"))
    ]


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
    if not PROBE_PATH.exists():
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.SKIPPED,
            notes=f"Skipped: probe fixture missing at {PROBE_PATH}.",
        )

    base = results_dir / "dim4" / spec.id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0
    expected = expected_week_ids(fixture_start, fixture_until)
    for wid in expected:
        write_approval_for(base, wid, received_at=fixture_start + timedelta(days=8))

    probe = json.loads(PROBE_PATH.read_text())
    probe_event = probe["plants"][0]
    probe_url = probe_event["url"]
    probe_id = probe_event["id"]

    fixtures_override_dir = base / "_fixtures_override"
    build_override(fixtures_override_dir, add_events=[probe_event])

    t0 = time.perf_counter()
    try:
        result = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim4",
            fixtures_dir=fixtures_override_dir,
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Run errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )
    elapsed = time.perf_counter() - t0

    kb = _load_kb_for_impl(base)
    digests = _digest_bodies(base)
    found_in_kb = (
        any(it.get("event_id") == probe_id for it in kb) if kb is not None
        else None
    )
    found_in_digest = any(probe_url in body for body in digests)

    # Graded surface: digest > KB-only > missing. KB score is only meaningful
    # when the impl exposes its KB on disk.
    surface_score = (
        1.0 if found_in_digest
        else 0.5 if found_in_kb is True
        else 0.0
    )
    accuracy = surface_score
    components = {"surface_score": surface_score}
    explanation = (
        "1.0 if probe in published digest, 0.5 if in KB only, 0.0 if neither"
    )

    metrics: dict[str, Any] = {
        "probe_event_id": probe_id,
        "probe_url": probe_url,
        "kb_observable": kb is not None,
        "found_in_kb": found_in_kb,
        "found_in_digest": found_in_digest,
        "summary": result,
    }

    def _result(status: DimensionStatus, notes: str) -> DimensionResult:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=status, notes=notes, metrics=metrics, elapsed_s=elapsed,
            accuracy=accuracy, accuracy_components=components,
            accuracy_explanation=explanation,
        )

    if found_in_digest:
        return _result(
            DimensionStatus.PASS,
            f"Probe event {probe_id} was filed and surfaced in a "
            f"published digest (URL match). The impl correctly captured "
            f"the planted fact.",
        )

    if found_in_kb is True:
        return _result(
            DimensionStatus.PARTIAL,
            f"Probe event filed in KB but did not make it into any "
            f"published digest. Probably outranked by other items "
            f"(max_items_per_digest={profile.max_items_per_digest}).",
        )

    return _result(
        DimensionStatus.FAIL,
        f"Probe event was injected via fixture override but did not "
        f"appear in KB (kb_observable={kb is not None}) or any digest. "
        f"The impl is dropping events it ought to keep.",
    )


if __name__ == "__main__":  # pragma: no cover
    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
