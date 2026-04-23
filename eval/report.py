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

from eval.dimensions.base import DimensionStatus

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

    return "\n".join([
        *_header_block(summary, summary_path),
        *acc_lines,
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
