"""Orchestrator: drives every (dimension x impl) combination.

Output:
    results/eval-summary.json  — machine-readable results
    results/eval-matrix.md     — human-readable matrix (via report.py)

Phase 2: dimensions 1, 6, 8.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from eval.dimensions import (
    DimensionResult,
    DimensionStatus,
    base,
    dim1_crash,
    dim2_multiday,
    dim3_context,
    dim4_memory,
    dim5_drift,
    dim6_hitl,
    dim7_stale,
    dim8_replay,
)
from eval.impls import REGISTRY, ImplSpec, available_impls
from task.types import UserProfile

log = logging.getLogger("halfmarathon.eval")
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "task" / "fixtures"

# Dimension entry points. Ordering controls report output.
DIMENSIONS = [
    dim1_crash,
    dim2_multiday,
    dim3_context,
    dim4_memory,
    dim5_drift,
    dim6_hitl,
    dim7_stale,
    dim8_replay,
]


@dataclass
class EvalSummary:
    started_at: str
    finished_at: str
    impls_run: list[str]
    impls_skipped: list[str]
    dimensions_run: list[str]
    results: list[dict]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, default=str)


async def _run_one(
    spec: ImplSpec, dim_module, results_dir: Path, profile: UserProfile,
) -> DimensionResult:
    name = f"dim{dim_module.DIM_ID}/{spec.id}"
    log.info("running %s", name)
    try:
        res = await dim_module.run(spec, results_dir=results_dir, profile=profile)
    except Exception as exc:
        log.exception("dimension test crashed")
        res = DimensionResult(
            impl_id=spec.id,
            dimension_id=dim_module.DIM_ID,
            dimension_name=dim_module.DIM_NAME,
            status=DimensionStatus.ERROR,
            notes=f"Harness crashed: {type(exc).__name__}",
            error=str(exc),
        )
    log.info("→ %s: %s — %s", name, res.status, res.notes[:80])
    return res


async def run_all(
    results_dir: Path,
    *,
    profile: UserProfile,
    only_impls: list[str] | None = None,
    only_dims: list[int] | None = None,
) -> EvalSummary:
    started = datetime.now(UTC).isoformat()
    results_dir.mkdir(parents=True, exist_ok=True)

    all_impls = REGISTRY
    runnable = available_impls()
    skipped = [s.id for s in all_impls if s not in runnable]
    if only_impls:
        runnable = [s for s in runnable if s.id in only_impls]

    dims = DIMENSIONS
    if only_dims:
        dims = [d for d in dims if d.DIM_ID in only_dims]

    log.info(
        "harness: %d impls x %d dims = %d cells; skipping %s",
        len(runnable), len(dims), len(runnable) * len(dims), skipped,
    )

    cells: list[DimensionResult] = []
    # Sequential — keeps per-impl state-dirs isolated and avoids API rate limits.
    for spec in runnable:
        for dim in dims:
            cells.append(await _run_one(spec, dim, results_dir, profile))

    finished = datetime.now(UTC).isoformat()
    summary = EvalSummary(
        started_at=started,
        finished_at=finished,
        impls_run=[s.id for s in runnable],
        impls_skipped=skipped,
        dimensions_run=[f"{d.DIM_ID}:{d.DIM_NAME}" for d in dims],
        results=[r.to_dict() for r in cells],
    )
    (results_dir / "eval-summary.json").write_text(summary.to_json())
    log.info("wrote %s", results_dir / "eval-summary.json")
    return summary


def _load_profile(p: Path) -> UserProfile:
    return UserProfile.model_validate(json.loads(p.read_text()))


def main() -> None:
    p = argparse.ArgumentParser(description="halfmarathon eval harness")
    p.add_argument("--results-dir", type=Path, default=ROOT / "results")
    p.add_argument("--config", type=Path, default=FIXTURES / "user.json")
    p.add_argument(
        "--impls", nargs="*", default=None,
        help="Impl ids to run (default: all available). E.g. --impls langgraph",
    )
    p.add_argument(
        "--dims", nargs="*", type=int, default=None,
        help="Dimension ids to run. E.g. --dims 1 8",
    )
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s :: %(message)s",
    )
    profile = _load_profile(args.config)

    summary = asyncio.run(
        run_all(
            args.results_dir,
            profile=profile,
            only_impls=args.impls,
            only_dims=args.dims,
        )
    )
    n_pass = sum(1 for r in summary.results if r["status"] == "pass")
    n_partial = sum(1 for r in summary.results if r["status"] == "partial")
    n_fail = sum(1 for r in summary.results if r["status"] == "fail")
    n_error = sum(1 for r in summary.results if r["status"] == "error")
    n_skipped = sum(1 for r in summary.results if r["status"] == "skipped")
    print(
        f"\n=== eval done ===\n"
        f"pass={n_pass} partial={n_partial} fail={n_fail} "
        f"error={n_error} skipped={n_skipped}"
    )
    print(f"summary: {args.results_dir / 'eval-summary.json'}")
    # Defer to report.py for the matrix render — keep harness lean.
    print("To render: make report  (writes results/eval-matrix.md)")


# Touch base so unused-import lint doesn't trip.
_ = base
_ = os

if __name__ == "__main__":
    main()
