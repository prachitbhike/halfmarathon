"""Render the eval matrix as `results/eval-matrix.md`.

Reads `results/eval-summary.json` (produced by `make eval`) and emits a
markdown matrix organized as dimensions (rows) x implementations (columns).

Cells carry both a pass/fail tier (PASS/PARTIAL/FAIL/SKIPPED/ERROR) and a
0.00-1.00 numerical accuracy score derived from the metrics each dimension
collects. A composite row at the bottom averages a column's per-dim scores
(skipped/errored cells excluded) so impls can be ranked numerically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.cost_projection import (
    WORKLOAD_TIERS,
    project_at_scale,
    project_for_impl,
)
from eval.dimensions.base import DimensionStatus
from eval.footprint import compute_all as compute_footprints
from eval.profiles import PROFILES, score_all

ROOT = Path(__file__).resolve().parents[1]

# Compact pretty status. Avoid emoji per CLAUDE.md.
_STATUS_LABEL = {
    DimensionStatus.PASS: "PASS",
    DimensionStatus.PARTIAL: "PART",
    DimensionStatus.FAIL: "FAIL",
    DimensionStatus.SKIPPED: "skip",
    DimensionStatus.ERROR: "ERR ",
}


def _fmt_accuracy(acc: float | None) -> str:
    return "—" if acc is None else f"{acc:.2f}"


def _sum_cost(metrics: dict) -> float:
    """Sum estimated_cost_usd across whatever sub-summaries a dim stored."""
    total = 0.0
    for key in (
        "summary", "fresh_summary", "resumed_summary",
        "multi_summary", "phase_a_summary", "phase_b_summary",
        "run_a_summary", "run_b_summary",
    ):
        sub = metrics.get(key)
        if isinstance(sub, dict):
            total += float(sub.get("estimated_cost_usd") or 0.0)
            total += float(sub.get("tick_cost_usd") or 0.0)
    return total


def _published_count(metrics: dict) -> int:
    """Best-effort count of digests produced in a cell, for $/digest math."""
    for key in ("resumed_published", "multi_published", "published",
                "published_after_phase_b", "run_a_published"):
        v = metrics.get(key)
        if isinstance(v, list):
            return len(v)
    return 0


def _pivot(results: list[dict]) -> tuple[list[str], dict[int, dict]]:
    impls = sorted({r["impl_id"] for r in results})
    dims: dict[int, dict] = {}
    for r in results:
        dim_id = int(r["dimension_id"])
        dims.setdefault(dim_id, {"name": r["dimension_name"], "by_impl": {}})
        dims[dim_id]["by_impl"][r["impl_id"]] = r
    return impls, dims


def _header_block(summary: dict, summary_path: Path) -> list[str]:
    return [
        "# halfmarathon eval matrix",
        "",
        f"_Generated from `{summary_path.relative_to(ROOT)}` "
        f"({summary['started_at']} -> {summary['finished_at']})._",
        "",
        f"Impls run: {', '.join(summary['impls_run']) or '(none)'}.  "
        f"Skipped: {', '.join(summary['impls_skipped']) or '(none)'}.",
        "",
        "## Status legend",
        "",
        "- **PASS** - dimension exercised and behavior matched expectation",
        "- **PART** - partially passed; see notes for caveat",
        "- **FAIL** - exercised, expected behavior not observed",
        "- **skip** - impl not runnable in this environment (e.g. missing API key)",
        "- **ERR**  - the test harness itself crashed",
        "",
        "Each cell also carries a **0.00-1.00 accuracy score** derived from "
        "the dimension's metrics (see `accuracy_explanation` in the JSON "
        "summary for the exact formula per dimension). Accuracy is skipped "
        "for `skip` and `ERR` cells.",
        "",
    ]


def _accuracy_matrix(
    dims: dict[int, dict], impls: list[str],
) -> tuple[list[str], dict[str, list[float]], dict[str, float],
           dict[str, float], dict[str, int]]:
    """Build the accuracy matrix and accumulate cross-cut totals in one pass."""
    composite_acc: dict[str, list[float]] = {impl: [] for impl in impls}
    elapsed_by_impl: dict[str, float] = dict.fromkeys(impls, 0.0)
    cost_by_impl: dict[str, float] = dict.fromkeys(impls, 0.0)
    digests_by_impl: dict[str, int] = dict.fromkeys(impls, 0)

    header = ["Dimension", *(f"`{i}`" for i in impls)]
    sep = ["---"] * len(header)
    lines = [
        "## Accuracy matrix (0.00-1.00)",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]

    for dim_id in sorted(dims):
        info = dims[dim_id]
        row = [f"**{dim_id}. {info['name']}**"]
        for impl_id in impls:
            r = info["by_impl"].get(impl_id)
            if r is None:
                row.append("—")
                continue
            status = DimensionStatus(r["status"])
            label = _STATUS_LABEL.get(status, r["status"])
            acc = r.get("accuracy")
            if acc is not None and status not in (
                DimensionStatus.SKIPPED, DimensionStatus.ERROR,
            ):
                composite_acc[impl_id].append(float(acc))
            row.append(f"{_fmt_accuracy(acc)} ({label})")
            elapsed_by_impl[impl_id] += float(r.get("elapsed_s") or 0.0)
            cost_by_impl[impl_id] += _sum_cost(r.get("metrics") or {})
            digests_by_impl[impl_id] += _published_count(r.get("metrics") or {})
        lines.append("| " + " | ".join(row) + " |")

    comp_cells = ["**Composite (mean)**"]
    for impl_id in impls:
        scores = composite_acc[impl_id]
        comp_cells.append(
            "—" if not scores else f"**{sum(scores) / len(scores):.2f}**"
        )
    lines.append("| " + " | ".join(comp_cells) + " |")
    lines += [
        "",
        "_Composite is the arithmetic mean of a column's per-dimension accuracy "
        "scores; skipped/errored cells are excluded. Equal weights across all 8 "
        "dimensions - reweight yourself if recovery (1-3) or HITL (6) matter "
        "more for your use case._",
        "",
    ]
    return lines, composite_acc, elapsed_by_impl, cost_by_impl, digests_by_impl


def _status_matrix(dims: dict[int, dict], impls: list[str]) -> list[str]:
    header = ["Dimension", *(f"`{i}`" for i in impls)]
    sep = ["---"] * len(header)
    lines = [
        "## Status matrix (with notes)",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for dim_id in sorted(dims):
        info = dims[dim_id]
        cells = [f"**{dim_id}. {info['name']}**"]
        for impl_id in impls:
            r = info["by_impl"].get(impl_id)
            if r is None:
                cells.append("—")
                continue
            label = _STATUS_LABEL.get(DimensionStatus(r["status"]), r["status"])
            note = (r.get("notes") or "").replace("\n", " ").strip()
            cells.append(f"**{label}** - {note}")
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _components_block(dims: dict[int, dict], impls: list[str]) -> list[str]:
    lines = [
        "",
        "## Accuracy components (per dimension, per impl)",
        "",
        "_Sub-scores that combine into the single accuracy value. A single_ "
        "_number hides which part of the dimension failed - this table shows it._",
        "",
    ]
    for dim_id in sorted(dims):
        info = dims[dim_id]
        comp_keys: list[str] = []
        seen: set[str] = set()
        for impl_id in impls:
            r = info["by_impl"].get(impl_id) or {}
            for k in (r.get("accuracy_components") or {}):
                if k not in seen:
                    seen.add(k)
                    comp_keys.append(k)
        if not comp_keys:
            continue
        lines.append(f"### {dim_id}. {info['name']}")
        formula = next(
            (
                (info["by_impl"].get(i) or {}).get("accuracy_explanation", "")
                for i in impls
                if (info["by_impl"].get(i) or {}).get("accuracy_explanation")
            ),
            "",
        )
        if formula:
            lines.append(f"_Formula: `{formula}`_")
        lines.append("")
        sub_header = ["Component", *(f"`{i}`" for i in impls)]
        lines.append("| " + " | ".join(sub_header) + " |")
        lines.append("| " + " | ".join(["---"] * len(sub_header)) + " |")
        for k in comp_keys:
            row = [k]
            for impl_id in impls:
                r = info["by_impl"].get(impl_id) or {}
                v = (r.get("accuracy_components") or {}).get(k)
                row.append(f"{v:.2f}" if isinstance(v, int | float) else "—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
    return lines


def _elapsed_block(dims: dict[int, dict], impls: list[str]) -> list[str]:
    lines = [
        "## Per-cell elapsed time (seconds)",
        "",
        "| Dimension | " + " | ".join(impls) + " |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " |",
    ]
    for dim_id in sorted(dims):
        info = dims[dim_id]
        cells = [f"{dim_id}. {info['name']}"]
        for impl_id in impls:
            r = info["by_impl"].get(impl_id)
            cells.append(f"{r['elapsed_s']:.1f}" if r else "—")
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _cross_cut_block(
    impls: list[str],
    elapsed_by_impl: dict[str, float],
    cost_by_impl: dict[str, float],
    digests_by_impl: dict[str, int],
) -> list[str]:
    any_cost = any(c > 0 for c in cost_by_impl.values())
    lines = [
        "",
        "## Cross-cut metrics (per impl, summed across all dimensions)",
        "",
        "| Metric | " + " | ".join(f"`{i}`" for i in impls) + " |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " |",
        "| Total elapsed (s) | "
        + " | ".join(f"{elapsed_by_impl[i]:.1f}" for i in impls) + " |",
        "| Total digests published | "
        + " | ".join(str(digests_by_impl[i]) for i in impls) + " |",
        "| Seconds per digest | "
        + " | ".join(
            f"{elapsed_by_impl[i] / digests_by_impl[i]:.2f}"
            if digests_by_impl[i] else "—"
            for i in impls
        )
        + " |",
    ]
    if any_cost:
        lines.append(
            "| Estimated cost (USD) | "
            + " | ".join(f"${cost_by_impl[i]:.4f}" for i in impls) + " |"
        )
        lines.append(
            "| USD per digest | "
            + " | ".join(
                f"${cost_by_impl[i] / digests_by_impl[i]:.4f}"
                if digests_by_impl[i] else "—"
                for i in impls
            )
            + " |"
        )
    else:
        lines += [
            "",
            "_All runs in offline mock mode - no LLM cost incurred. Re-run "
            "with `HALFMARATHON_OFFLINE_LLM` unset to populate the cost ledger._",
        ]
    return lines


def _impl_dim_scores(
    dims: dict[int, dict], impls: list[str],
) -> dict[str, dict[int, float | None]]:
    """Build {impl_id: {dim_id: accuracy_or_None}} from the matrix."""
    out: dict[str, dict[int, float | None]] = {impl: {} for impl in impls}
    for dim_id, info in dims.items():
        for impl_id in impls:
            r = info["by_impl"].get(impl_id)
            if r is None:
                continue
            status = DimensionStatus(r["status"])
            acc = r.get("accuracy")
            if status in (DimensionStatus.SKIPPED, DimensionStatus.ERROR):
                out[impl_id][dim_id] = None
            else:
                out[impl_id][dim_id] = (
                    None if acc is None else float(acc)
                )
    return out


def _profile_composites_block(
    impls: list[str], scores_by_impl: dict[str, dict[int, float | None]],
) -> list[str]:
    """Render the use-case profile composite table."""
    profile_results = score_all(scores_by_impl)
    lines = [
        "",
        "## Composite by use-case profile",
        "",
        "Single composite scores hide which workload an impl is strong for. "
        "These rows re-weight the dims for different kinds of project. Each "
        "profile excludes impls that didn't run a critical mass of the "
        "weighted dims (shown as `—`).",
        "",
        "| Profile | " + " | ".join(f"`{i}`" for i in impls) + " | Boosted dims |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " | --- |",
    ]
    for prof in PROFILES:
        per_impl = {ps.impl_id: ps for ps in profile_results[prof.name]}
        cells = [f"**{prof.name}**"]
        # Find the best score for bolding the leader.
        candidates = [(ps.composite, ps.impl_id) for ps in per_impl.values()
                      if ps.composite is not None]
        best_score = max((c for c, _ in candidates), default=None)
        for impl_id in impls:
            ps = per_impl.get(impl_id)
            if ps is None or ps.composite is None:
                cells.append("—")
                continue
            tag = (
                f"**{ps.composite:.2f}**"
                if best_score is not None and abs(ps.composite - best_score) < 1e-9
                else f"{ps.composite:.2f}"
            )
            cells.append(tag)
        boosted = ", ".join(
            f"d{d}x{w:g}"
            for d, w in sorted(prof.weights.items())
            if w > prof.default_weight
        )
        cells.append(boosted)
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "_Bold = best in row. Profile descriptions:_",
        "",
    ]
    for prof in PROFILES:
        lines.append(f"- **{prof.name}** — {prof.description}")
    lines.append("")
    return lines


def _strengths_block(  # noqa: PLR0915
    impls: list[str],
    dims: dict[int, dict],
    scores_by_impl: dict[str, dict[int, float | None]],
    results: list[dict],
) -> list[str]:
    """Per-impl differentiating signals — where this impl notably DIFFERS
    from the others, not just where its own score is highest.

    Ceiling effects (most dims at 1.00 because the test is easy) make
    absolute-threshold "strengths" lists basically meaningless. Instead we
    compare each impl's score to the median of the OTHERS for that dim:
      - delta >= +0.10: surface as "Differentiating strength"
      - delta <= -0.10: surface as "Differentiating weakness"
      - otherwise: omit (this impl is in the pack on this dim)

    We also pull from footprint data to surface non-accuracy
    differentiators ("only impl with no offline mock", etc.) so impls that
    tie on accuracy still get separated.
    """
    dim_names = {d_id: info["name"] for d_id, info in dims.items()}
    fps = compute_footprints(impls, results)

    # For each (impl, dim), compute delta vs median of the other impls' scores
    # on that dim. None deltas mean either we have no score for this impl, or
    # there are no other impls with scores on this dim (no comparison possible).
    def _delta_vs_others(impl_id: str, dim_id: int) -> float | None:
        my = scores_by_impl[impl_id].get(dim_id)
        if my is None:
            return None
        others = [
            scores_by_impl[i].get(dim_id) for i in impls if i != impl_id
        ]
        others = [s for s in others if s is not None]
        if not others:
            return None
        median = sorted(others)[len(others) // 2]
        return my - median

    DIFF_THRESHOLD = 0.10

    # Cross-impl differentiators from footprint (computed once)
    loc_min = min(fps[i].lines_of_code for i in impls if fps[i].lines_of_code)
    loc_max = max(fps[i].lines_of_code for i in impls if fps[i].lines_of_code)
    elapsed_vals = [
        (i, fps[i].mean_elapsed_s) for i in impls
        if fps[i].mean_elapsed_s is not None
    ]
    elapsed_min = min((v for _, v in elapsed_vals), default=None)
    elapsed_max = max((v for _, v in elapsed_vals), default=None)
    services_min = min(fps[i].services_to_run for i in impls)
    services_max = max(fps[i].services_to_run for i in impls)

    def _footprint_callouts(impl_id: str) -> list[str]:
        notes: list[str] = []
        fp = fps[impl_id]
        if fp.lines_of_code == loc_min and loc_min < loc_max:
            notes.append(f"Lowest LOC ({fp.lines_of_code})")
        if fp.lines_of_code == loc_max and loc_min < loc_max:
            notes.append(f"Highest LOC ({fp.lines_of_code})")
        if fp.mean_elapsed_s is not None and elapsed_min is not None:
            is_min = abs(fp.mean_elapsed_s - elapsed_min) < 1e-6
            is_max = (
                elapsed_max is not None
                and abs(fp.mean_elapsed_s - elapsed_max) < 1e-6
            )
            spread = (elapsed_max or 0) > elapsed_min
            if is_min and spread:
                notes.append(
                    f"Fastest mean wall-clock ({fp.mean_elapsed_s:.1f}s/cell)"
                )
            if is_max and spread:
                notes.append(
                    f"Slowest mean wall-clock ({fp.mean_elapsed_s:.1f}s/cell)"
                )
        if fp.services_to_run == services_max and services_min < services_max:
            notes.append(f"Most services to run ({fp.services_to_run})")
        if fp.services_to_run == services_min and services_min < services_max:
            notes.append(f"Fewest services to run ({fp.services_to_run})")
        # Pick out the obvious 5/5 and 1-2/5 ratings as differentiators
        for key, label in _RATING_LABELS.items():
            r = fp.ratings.get(key)
            if r is None:
                continue
            score = r[0]
            others_scores = [
                fps[j].ratings.get(key, (0, ""))[0]
                for j in impls if j != impl_id
            ]
            others_max = max(others_scores) if others_scores else score
            others_min = min(others_scores) if others_scores else score
            if score >= 5 and score > others_max:
                notes.append(f"Best at {label.lower()} ({score}/5)")
            if score <= 2 and score < others_min:
                notes.append(f"Weakest at {label.lower()} ({score}/5)")
        return notes

    lines = [
        "",
        "## Per-impl differentiating signals",
        "",
        "_For each impl: dims where it notably differs from the others_ "
        "_(>=0.10 above or below the median of peers), plus footprint-level_ "
        "_callouts where this impl is uniquely highest/lowest. If an impl has_ "
        "_no entries here, it's in the pack on every measured axis._",
        "",
    ]

    for impl_id in impls:
        scored_dim_ids = [
            d for d in dims
            if scores_by_impl[impl_id].get(d) is not None
        ]
        n_run = len(scored_dim_ids)

        better: list[tuple[int, float, float]] = []   # (dim, score, delta)
        worse: list[tuple[int, float, float]] = []
        for d_id in scored_dim_ids:
            delta = _delta_vs_others(impl_id, d_id)
            if delta is None:
                continue
            if delta >= DIFF_THRESHOLD:
                better.append((d_id, scores_by_impl[impl_id][d_id], delta))
            elif delta <= -DIFF_THRESHOLD:
                worse.append((d_id, scores_by_impl[impl_id][d_id], delta))

        callouts = _footprint_callouts(impl_id)

        lines.append(f"### `{impl_id}`")
        lines.append("")
        lines.append(f"_Cells run: {n_run}/{len(dims)}._")
        lines.append("")

        if not better and not worse and not callouts:
            lines.append(
                "_In the pack on every measured axis. Distinguish via the "
                "footprint and ratings tables above._"
            )
            lines.append("")
            continue

        if better:
            lines.append("**Differentiating strengths** (≥0.10 above peer median):")
            for d_id, s, delta in sorted(better, key=lambda x: -x[2]):
                lines.append(
                    f"- dim {d_id} *{dim_names[d_id]}*: **{s:.2f}** "
                    f"(+{delta:.2f} vs median)"
                )
            lines.append("")
        if worse:
            lines.append("**Differentiating weaknesses** (≥0.10 below peer median):")
            for d_id, s, delta in sorted(worse, key=lambda x: x[2]):
                lines.append(
                    f"- dim {d_id} *{dim_names[d_id]}*: **{s:.2f}** "
                    f"({delta:+.2f} vs median)"
                )
            lines.append("")
        if callouts:
            lines.append("**Footprint-level callouts:**")
            for c in callouts:
                lines.append(f"- {c}")
            lines.append("")

    return lines


_RATING_LABELS = {
    "observability": "Observability / debug UX",
    "mental_model_simplicity": "Mental model simplicity",
    "production_scaling": "Production scaling",
    "multi_tenancy": "Multi-tenancy support",
    "type_safety": "Type safety / IDE support",
}


def _ratings_block(impls: list[str], results: list[dict]) -> list[str]:
    """Hand-curated 1-5 ratings for things accuracy scores can't measure.

    Observability, mental model load, scaling, multi-tenancy, type safety,
    vendor lock-in — what a developer actually weighs at adoption time.
    See IMPL_OPS_METADATA in eval/footprint.py for the rubric and
    rationale per cell.
    """
    fps = compute_footprints(impls, results)
    lines = [
        "",
        "## Beyond-accuracy ratings (what dim scores can't measure)",
        "",
        "Hand-curated 1-5 scores (5 = best) for the framework qualities "
        "developers weigh at adoption time but the dim accuracy can't "
        "capture. Ratings reflect honest builder experience from Phases 0-5; "
        "rationale lives below the table for auditability.",
        "",
        "| Capability | " + " | ".join(f"`{i}`" for i in impls) + " |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " |",
    ]

    rating_keys = list(_RATING_LABELS.keys())
    for key in rating_keys:
        cells = [_RATING_LABELS[key]]
        # Find the best score for this row (for bolding)
        scores: list[int] = []
        for i in impls:
            r = fps[i].ratings.get(key)
            if r is not None:
                scores.append(r[0])
        best = max(scores) if scores else None
        for i in impls:
            r = fps[i].ratings.get(key)
            if r is None:
                cells.append("—")
                continue
            score = r[0]
            cells.append(
                f"**{score}/5**" if score == best else f"{score}/5"
            )
        lines.append("| " + " | ".join(cells) + " |")

    # Vendor lock-in is a string, not a score — render as a separate row.
    cells = ["**Vendor lock-in**"]
    for i in impls:
        cells.append(fps[i].vendor_lockin or "—")
    lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "### Rating rationale", ""]
    for key in rating_keys:
        lines.append(f"**{_RATING_LABELS[key]}**")
        for i in impls:
            r = fps[i].ratings.get(key)
            if r is None:
                continue
            lines.append(f"- `{i}` ({r[0]}/5): {r[1]}")
        lines.append("")
    return lines


def _cost_projection_block(impls: list[str], results: list[dict]) -> list[str]:
    """Project per-cell costs into per-published-digest and at-scale numbers."""
    projections = {i: project_for_impl(i, results) for i in impls}
    has_any_real_cost = any(p.is_measured for p in projections.values())
    lines = [
        "",
        "## Cost projection (extrapolated to production scale)",
        "",
        ("Per-digest cost is **measured** for impls run with a real LLM, and "
         "**estimated** (~$0.24/digest) for impls run in offline mock mode "
         "based on typical token usage with Sonnet 4.6. The framework adds "
         "~zero cost beyond the model API."
         if has_any_real_cost else
         "All impls in this matrix were run in offline mock mode. The "
         "projection below estimates per-digest cost if the offline mock "
         "were swapped for Sonnet 4.6 — framework adds ~zero on top."),
        "",
        "| Workload | " + " | ".join(f"`{i}`" for i in impls) + " |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " |",
    ]

    # Per-digest row first
    cells = ["**$ per digest** (basis)"]
    for i in impls:
        p = projections[i]
        if p.cost_per_digest_usd is None:
            cells.append("—")
            continue
        basis = "measured" if p.is_measured else "estimated"
        cells.append(f"${p.cost_per_digest_usd:.2f} ({basis})")
    lines.append("| " + " | ".join(cells) + " |")

    # Per workload tier
    for label, n in WORKLOAD_TIERS:
        cells = [label]
        for i in impls:
            v = project_at_scale(projections[i], n)
            cells.append("—" if v is None else f"${v:,.2f}/wk")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "_Notes:_",
        "_- Measured numbers come from the actual API spend on this impl's_ "
        "_real-LLM run; estimated numbers assume `task/llm.py`'s relevance_ "
        "_scoring + summarization pattern at ~30k input + ~10k output tokens_ "
        "_per published digest._",
        "_- claude_sdk's per-digest cost is ~6x higher than the others_ "
        "_because the agent loop emits multiple LLM exchanges per tick (Read,_ "
        "_Write, Bash, summarize)  vs the offline pair's single batched_ "
        "_relevance call + N summarization calls._",
        "_- Real-world variance: ±2x depending on user interest count, source_ "
        "_event density, and cache hit rate._",
        "",
    ]
    return lines


def _footprint_block(impls: list[str], results: list[dict]) -> list[str]:
    """Cross-impl metrics that vary independently of LLM behavior.

    LOC, deps, mean wall-clock, services-to-run — these separate impls that
    score identically on the LLM-driven dimensions. Hand-curated ops_steps
    in eval/footprint.py back the "Setup steps" + "Prod storage" rows.
    """
    fps = compute_footprints(impls, results)
    lines = [
        "",
        "## Footprint comparison (what the impl IS, not what it does)",
        "",
        "Dim accuracy is largely an LLM property. These rows are framework "
        "properties — they're what differentiates impls that share the same "
        "LLM (e.g. langgraph and temporal_pydantic on the offline mock).",
        "",
        "| Metric | " + " | ".join(f"`{i}`" for i in impls) + " |",
        "| --- | " + " | ".join(["---"] * len(impls)) + " |",
        "| Impl source LOC (excl. tests/init) | "
        + " | ".join(str(fps[i].lines_of_code) for i in impls) + " |",
        "| Source files | "
        + " | ".join(str(fps[i].source_files) for i in impls) + " |",
        "| Direct pip deps | "
        + " | ".join(str(fps[i].direct_deps) for i in impls) + " |",
        "| Long-lived services | "
        + " | ".join(str(fps[i].services_to_run) for i in impls) + " |",
        "| Setup steps from clean machine | "
        + " | ".join(str(fps[i].setup_step_count) for i in impls) + " |",
        "| Mean wall-clock per cell (s) | "
        + " | ".join(
            f"{fps[i].mean_elapsed_s:.1f}" if fps[i].mean_elapsed_s is not None
            else "—"
            for i in impls
        )
        + " |",
        "| Production storage | "
        + " | ".join(
            f"{fps[i].prod_storage}" if fps[i].prod_storage else "—"
            for i in impls
        )
        + " |",
        "",
        "### Setup steps detail",
        "",
    ]
    for i in impls:
        lines.append(f"**`{i}`** — {fps[i].setup_step_count} step(s):")
        for n, step in enumerate(fps[i].ops_steps, 1):
            lines.append(f"  {n}. {step}")
        lines.append("")
    return lines


def _notes_block() -> list[str]:
    return [
        "",
        "## Notes",
        "",
        "- Dimensions 1, 6, 8 are deterministic and fully exercised offline.",
        "- Dimensions 2, 3, 4, 5, 7 are partially exercised in offline mode: ",
        "  they validate structure (no crash, output bounds, no double-publish, ",
        "  no off-topic in published, no stale references) but cannot evaluate ",
        "  LLM-dependent quality (compaction, recall, drift under adversarial ",
        "  pressure). The `findings.md` writeup calls out each gap explicitly.",
        "- Per-cell accuracy formulas are stored alongside each result in "
        "`eval-summary.json` under `accuracy_explanation` for auditability.",
        "",
    ]


def render(summary_path: Path) -> str:
    summary = json.loads(summary_path.read_text())
    impls, dims = _pivot(summary["results"])

    acc_lines, _, elapsed, cost, digests = _accuracy_matrix(dims, impls)
    scores_by_impl = _impl_dim_scores(dims, impls)

    return "\n".join([
        *_header_block(summary, summary_path),
        *acc_lines,
        *_profile_composites_block(impls, scores_by_impl),
        *_ratings_block(impls, summary["results"]),
        *_cost_projection_block(impls, summary["results"]),
        *_footprint_block(impls, summary["results"]),
        *_strengths_block(impls, dims, scores_by_impl, summary["results"]),
        *_status_matrix(dims, impls),
        *_components_block(dims, impls),
        *_elapsed_block(dims, impls),
        *_cross_cut_block(impls, elapsed, cost, digests),
        *_notes_block(),
    ])


def main() -> None:
    p = argparse.ArgumentParser(description="Render eval matrix")
    p.add_argument(
        "--summary", type=Path,
        default=ROOT / "results" / "eval-summary.json",
        help="Path to eval-summary.json",
    )
    p.add_argument(
        "--out", type=Path,
        default=ROOT / "results" / "eval-matrix.md",
        help="Output markdown path",
    )
    args = p.parse_args()

    if not args.summary.exists():
        raise SystemExit(
            f"summary not found at {args.summary}. Run `make eval` first."
        )
    md = render(args.summary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)
    print(f"wrote {args.out}")
    print()
    print(md)


if __name__ == "__main__":
    main()
