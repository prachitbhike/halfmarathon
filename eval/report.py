"""Render the eval matrix as `results/eval-matrix.md`.

Reads `results/eval-summary.json` (produced by `make eval`) and emits a
markdown table organized as dimensions (rows) x implementations (columns).

Each cell is one of: PASS / PARTIAL / FAIL / SKIPPED / ERROR, with a one-line
note. Detailed metrics live in the summary JSON, not in the matrix.
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


def render(summary_path: Path) -> str:
    summary = json.loads(summary_path.read_text())
    results = summary["results"]

    # Pivot.
    impls = sorted({r["impl_id"] for r in results})
    dims: dict[int, dict] = {}
    for r in results:
        dim_id = int(r["dimension_id"])
        dims.setdefault(dim_id, {"name": r["dimension_name"], "by_impl": {}})
        dims[dim_id]["by_impl"][r["impl_id"]] = r

    lines: list[str] = [
        "# halfmarathon eval matrix",
        "",
        f"_Generated from `{summary_path.relative_to(ROOT)}` "
        f"({summary['started_at']} → {summary['finished_at']})._",
        "",
        f"Impls run: {', '.join(summary['impls_run']) or '(none)'}.  "
        f"Skipped: {', '.join(summary['impls_skipped']) or '(none)'}.",
        "",
        "## Status legend",
        "",
        "- **PASS** — dimension exercised and behavior matched expectation",
        "- **PART** — partially passed; see notes for caveat",
        "- **FAIL** — exercised, expected behavior not observed",
        "- **skip** — impl not runnable in this environment (e.g. missing API key)",
        "- **ERR**  — the test harness itself crashed",
        "",
        "## Matrix",
        "",
    ]

    # Header
    header = ["Dimension", *(_format_impl_header(i) for i in impls)]
    sep = ["---"] * len(header)
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(sep) + " |")

    for dim_id in sorted(dims):
        info = dims[dim_id]
        row_label = f"**{dim_id}. {info['name']}**"
        cells = [row_label]
        for impl_id in impls:
            r = info["by_impl"].get(impl_id)
            if r is None:
                cells.append("—")
                continue
            label = _STATUS_LABEL.get(DimensionStatus(r["status"]), r["status"])
            note = (r.get("notes") or "").replace("\n", " ").strip()
            cells.append(f"**{label}** — {note}")
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
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

    lines += [
        "",
        "## Notes",
        "",
        "- Phase 2 covers dimensions 1, 6, 8 (the deterministic, fast-to-run ones). ",
        "- Dimensions 2, 3, 4, 5, 7 (and optional 9, 10) land in Phase 4 — they ",
        "  require longer wall-clock runs in the e2b sandbox.",
        "- Detailed per-cell metrics are in `eval-summary.json` next to this file.",
        "",
    ]
    return "\n".join(lines)


def _format_impl_header(impl_id: str) -> str:
    # Pretty header but keep it short for table width.
    return f"`{impl_id}`"


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
