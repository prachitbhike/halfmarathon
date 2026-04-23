# halfmarathon eval matrix

_Generated from `results/eval-summary.json` (2026-04-23T05:02:19.913969+00:00 → 2026-04-23T05:03:47.604283+00:00)._

Impls run: langgraph.  Skipped: claude_sdk.

## Status legend

- **PASS** — dimension exercised and behavior matched expectation
- **PART** — partially passed; see notes for caveat
- **FAIL** — exercised, expected behavior not observed
- **skip** — impl not runnable in this environment (e.g. missing API key)
- **ERR**  — the test harness itself crashed

## Matrix

| Dimension | `langgraph` |
| --- | --- |
| **1. Crash recovery** | **PASS** — Resume-from-partial reaches the same end state as a fresh single pass: 2 digests published, KB=29 items, no duplicates. |
| **6. HITL gate spanning hours** | **PASS** — Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W13 (no double-publish). |
| **8. Replay determinism** | **PASS** — Two clean runs produced byte-identical published digests for all 2 weeks (deterministic in this configuration). |

## Per-cell elapsed time (seconds)

| Dimension | langgraph |
| --- | --- |
| 1. Crash recovery | 35.6 |
| 6. HITL gate spanning hours | 22.0 |
| 8. Replay determinism | 30.1 |

## Notes

- Phase 2 covers dimensions 1, 6, 8 (the deterministic, fast-to-run ones). 
- Dimensions 2, 3, 4, 5, 7 (and optional 9, 10) land in Phase 4 — they 
  require longer wall-clock runs in the e2b sandbox.
- Detailed per-cell metrics are in `eval-summary.json` next to this file.
