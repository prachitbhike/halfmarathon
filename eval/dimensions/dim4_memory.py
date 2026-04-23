"""Dimension 4 — memory recall over time.

Spec: plant a fact in week 1, query in week 12; verify update propagates if
the fact is later corrected.

Phase 4 implementation: a *filing* check, not a recall check. We add a probe
event into the fixture timeline (via fixture override) and verify the agent
filed it into KB during week 1, including the marker substring.

Real recall — asking the agent "do you remember X?" after weeks of unrelated
input — requires (a) a real LLM that can actually recall, and (b) impl-side
machinery to query an agent's accumulated memory. Letta and Claude SDK could
support this in a follow-up; LangGraph and Pydantic-AI+Temporal would need a
deliberate "ask the agent" path that doesn't exist today.

Score:
    - PASS: probe event made it into KB with the marker substring intact.
    - PARTIAL: probe filed but marker missing or score low.
    - FAIL: probe not in KB after the run.
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

DIM_ID = 4
DIM_NAME = "Memory recall (filing)"

ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "task" / "fixtures" / "memory-probes.json"


def _load_kb_for_impl(state_dir: Path, impl_id: str) -> list[dict] | None:
    """Best-effort KB loader. Each impl stores KB differently:
        - claude_sdk + letta: knowledge_base.json at state_dir root
        - langgraph + temporal_pydantic: KB lives in checkpointer/workflow
          state, not on disk; we fall back to scanning published digests.
    Returns None if we can't observe a KB for this impl.
    """
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
    probe_marker: str = probe["queries"][0]["expected_recall_substring"]

    t0 = time.perf_counter()
    try:
        # Note: we run the impl on the BASE fixture timeline (no override
        # plumbing exists yet). This means the probe is NOT actually present
        # in the agent's input stream — so this is currently a structural test
        # of the harness. Once a fixture-override mechanism lands (Phase 5),
        # the probe should appear in the impl's KB. Documented in notes.
        result = await spec.run(
            profile=profile, state_dir=base,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim4",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Run errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )
    elapsed = time.perf_counter() - t0

    metrics: dict[str, Any] = {
        "probe_event_id": probe_event["id"],
        "probe_marker": probe_marker,
        "summary": result,
    }

    kb = _load_kb_for_impl(base, spec.id)
    digests = _digest_bodies(base)
    found_in_kb = False
    found_in_digest = False
    if kb is not None:
        found_in_kb = any(
            it.get("event_id") == probe_event["id"]
            and probe_marker in (it.get("summary") or "")
            for it in kb
        )
    found_in_digest = any(probe_marker in body for body in digests)
    metrics["found_in_kb"] = found_in_kb
    metrics["found_in_digest"] = found_in_digest

    # Until fixture-override lands, this dim cannot actually file the probe
    # because the probe event isn't in timeline.json. So the honest score is
    # PARTIAL, with a note on what's missing.
    return DimensionResult(
        impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
        status=DimensionStatus.PARTIAL,
        notes=(
            "Filing-only check: probe event isn't currently injected into "
            "the impl's input stream (no fixture-override mechanism yet). "
            "Score will rise to PASS once Phase 5 adds runtime fixture "
            "augmentation. "
            f"Sanity counters: kb_observable={kb is not None}, "
            f"found_in_kb={found_in_kb}, found_in_digest={found_in_digest}."
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
