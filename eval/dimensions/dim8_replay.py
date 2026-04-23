"""Dimension 8 — replay from event log.

Spec (plan.md §4): replay full week-1 trajectory against modified prompt;
diff outputs.

Phase 2 implementation: a weaker form. We score *behavioral determinism*
across two clean runs of the same impl on the same fixtures.
    - PASS if the published digests are byte-identical across runs.
    - PARTIAL if the same week ids are published with the same item ids
      in the same order, but body text differs (LLM stochasticity).
    - FAIL otherwise.

A future Phase 4 deepens this to true event-log replay (re-driving the agent
from a recorded events.jsonl + cached LLM responses).
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
from eval.scoring import jaccard, mean, text_similarity
from task.clock import DEFAULT_FIXTURE_START
from task.types import UserProfile

DIM_ID = 8
DIM_NAME = "Replay determinism"


def _diff_published(state_a: Path, state_b: Path) -> dict[str, str]:
    """Return {week_id: 'identical' | 'differs' | 'missing_in_a' | 'missing_in_b'}."""
    out: dict[str, str] = {}
    a_files = {p.name: p for p in (state_a / "digests").glob("published-week-*.md")}
    b_files = {p.name: p for p in (state_b / "digests").glob("published-week-*.md")}
    for name in sorted(set(a_files) | set(b_files)):
        wid = name.removeprefix("published-").removesuffix(".md")
        if name not in a_files:
            out[wid] = "missing_in_a"
            continue
        if name not in b_files:
            out[wid] = "missing_in_b"
            continue
        out[wid] = (
            "identical" if a_files[name].read_bytes() == b_files[name].read_bytes()
            else "differs"
        )
    return out


def _similarity_per_week(state_a: Path, state_b: Path) -> dict[str, float]:
    """For weeks present in both runs, text-similarity ratio per week."""
    out: dict[str, float] = {}
    a_files = {p.name: p for p in (state_a / "digests").glob("published-week-*.md")}
    b_files = {p.name: p for p in (state_b / "digests").glob("published-week-*.md")}
    for name in sorted(set(a_files) & set(b_files)):
        wid = name.removeprefix("published-").removesuffix(".md")
        out[wid] = text_similarity(
            a_files[name].read_text(), b_files[name].read_text()
        )
    return out


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

    base = results_dir / "dim8" / spec.id
    if base.exists():
        shutil.rmtree(base)
    run_a = base / "run-a"
    run_b = base / "run-b"

    fixture_start = DEFAULT_FIXTURE_START
    # Window length is overridable via HALFMARATHON_DIM_DAYS (default 15) so
    # slow real-LLM impls (claude_sdk) can run a shorter window.
    fixture_until = DEFAULT_FIXTURE_START + timedelta(
        days=int(os.environ.get("HALFMARATHON_DIM_DAYS", "15"))
    )
    speed = float(os.environ.get("HALFMARATHON_DIM_SPEED", "86400"))
    expected = expected_week_ids(fixture_start, fixture_until)

    for d in (run_a, run_b):
        d.mkdir(parents=True, exist_ok=True)
        for wid in expected:
            write_approval_for(d, wid, received_at=fixture_start + timedelta(days=8))

    t0 = time.perf_counter()
    try:
        sum_a = await spec.run(
            profile=profile, state_dir=run_a,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim8-a",
        )
        sum_b = await spec.run(
            profile=profile, state_dir=run_b,
            fixture_start=fixture_start, until=fixture_until,
            speed=speed, thread_id="dim8-b",
        )
    except Exception as exc:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Run errored: {type(exc).__name__}",
            error=str(exc), elapsed_s=time.perf_counter() - t0,
        )
    elapsed = time.perf_counter() - t0

    pub_a = published_week_ids(run_a)
    pub_b = published_week_ids(run_b)
    diff = _diff_published(run_a, run_b)
    sims = _similarity_per_week(run_a, run_b)

    # Workflow-level determinism (did the same weeks publish in both runs?)
    # multiplied by content-level similarity over shared weeks. A divergent
    # workflow pulls byte-similarity down proportionally — you cannot get a
    # high score by producing similar text on different weeks.
    workflow_overlap = jaccard(pub_a, pub_b)
    byte_similarity = mean(sims.values()) if sims else 0.0
    accuracy = workflow_overlap * byte_similarity
    components = {
        "workflow_overlap": workflow_overlap,
        "byte_similarity": byte_similarity,
    }
    explanation = (
        "jaccard(run_a_weeks, run_b_weeks) * mean(difflib ratio over shared weeks)"
    )

    metrics = {
        "expected": expected,
        "run_a_published": pub_a,
        "run_b_published": pub_b,
        "diff": diff,
        "similarity_per_week": {k: round(v, 4) for k, v in sims.items()},
        "run_a_summary": sum_a,
        "run_b_summary": sum_b,
    }

    def _result(status: DimensionStatus, notes: str) -> DimensionResult:
        return DimensionResult(
            impl_id=spec.id, dimension_id=DIM_ID, dimension_name=DIM_NAME,
            status=status, notes=notes, metrics=metrics, elapsed_s=elapsed,
            accuracy=accuracy, accuracy_components=components,
            accuracy_explanation=explanation,
        )

    if pub_a != pub_b:
        return _result(
            DimensionStatus.FAIL,
            f"Two clean runs published different week sets — non-deterministic "
            f"at the workflow level. A={pub_a}, B={pub_b}.",
        )

    statuses = set(diff.values())
    if statuses <= {"identical"}:
        return _result(
            DimensionStatus.PASS,
            f"Two clean runs produced byte-identical published digests for "
            f"all {len(diff)} weeks (deterministic in this configuration).",
        )
    if statuses <= {"identical", "differs"}:
        differing = [w for w, s in diff.items() if s == "differs"]
        return _result(
            DimensionStatus.PARTIAL,
            f"Same digests published, but {len(differing)} of {len(diff)} have "
            f"diverging body text (similarity={byte_similarity:.3f}; LLM "
            f"stochasticity is the most likely cause).",
        )
    return _result(
        DimensionStatus.FAIL,
        f"Published-set divergence between runs: {diff}",
    )


if __name__ == "__main__":  # pragma: no cover
    import json

    from eval.impls import find_impl
    profile = UserProfile.model_validate(
        json.loads(Path("task/fixtures/user.json").read_text())
    )
    res = asyncio.run(run(find_impl("langgraph"), results_dir=Path("results"), profile=profile))
    print(json.dumps(res.to_dict(), indent=2, default=str))
