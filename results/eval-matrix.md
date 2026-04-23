# halfmarathon eval matrix

_Generated from `results/eval-summary.json` (2026-04-23T05:36:37.658644+00:00 → 2026-04-23T05:43:24.767829+00:00)._

Impls run: langgraph, temporal_pydantic.  Skipped: letta, claude_sdk.

## Status legend

- **PASS** — dimension exercised and behavior matched expectation
- **PART** — partially passed; see notes for caveat
- **FAIL** — exercised, expected behavior not observed
- **skip** — impl not runnable in this environment (e.g. missing API key)
- **ERR**  — the test harness itself crashed

## Matrix

| Dimension | `langgraph` | `temporal_pydantic` |
| --- | --- | --- |
| **1. Crash recovery** | **PASS** — Resume-from-partial reaches the same end state as a fresh single pass: 2 digests published, KB=29 items, no duplicates. | **PASS** — Resume-from-partial reaches the same end state as a fresh single pass: 2 digests published, KB=38 items, no duplicates. |
| **2. Multi-day with sleeps + multi-restart** | **PASS** — Survived 3 restarts across the 2-week fixture; final state byte-equivalent to a single-pass run. | **PASS** — Survived 3 restarts across the 2-week fixture; final state byte-equivalent to a single-pass run. |
| **3. Cross-window continuity (structural)** | **PASS** — Structural invariants held: 2 digests published, each with <= 8 items, KB=29. (Compaction quality not exercised — see findings.) | **PASS** — Structural invariants held: 2 digests published, each with <= 8 items, KB=38. (Compaction quality not exercised — see findings.) |
| **4. Memory recall (filing)** | **PART** — Filing-only check: probe event isn't currently injected into the impl's input stream (no fixture-override mechanism yet). Score will rise to PASS once Phase 5 adds runtime fixture augmentation. Sanity counters: kb_observable=False, found_in_kb=False, found_in_digest=False. | **PART** — Filing-only check: probe event isn't currently injected into the impl's input stream (no fixture-override mechanism yet). Score will rise to PASS once Phase 5 adds runtime fixture augmentation. Sanity counters: kb_observable=False, found_in_kb=False, found_in_digest=False. |
| **5. Goal drift** | **PART** — 2/16 published items are off-topic (ratio=0.12). Below the 25% PASS threshold. | **PART** — 2/16 published items are off-topic (ratio=0.12). Below the 25% PASS threshold. |
| **6. HITL gate spanning hours** | **PASS** — Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W13 (no double-publish). | **PASS** — Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W13 (no double-publish). |
| **7. Stale external state** | **PART** — Impl completed, but 1 of 2 would-be-deleted events were referenced in published digests. None of the four impls re-checks source state after filing today; the right behavior would be to re-fetch on resume and drop or flag stale references. | **PART** — Impl completed, but 1 of 2 would-be-deleted events were referenced in published digests. None of the four impls re-checks source state after filing today; the right behavior would be to re-fetch on resume and drop or flag stale references. |
| **8. Replay determinism** | **PASS** — Two clean runs produced byte-identical published digests for all 2 weeks (deterministic in this configuration). | **PASS** — Two clean runs produced byte-identical published digests for all 2 weeks (deterministic in this configuration). |

## Per-cell elapsed time (seconds)

| Dimension | langgraph | temporal_pydantic |
| --- | --- | --- |
| 1. Crash recovery | 35.5 | 37.6 |
| 2. Multi-day with sleeps + multi-restart | 51.1 | 53.5 |
| 3. Cross-window continuity (structural) | 15.0 | 15.7 |
| 4. Memory recall (filing) | 15.1 | 15.7 |
| 5. Goal drift | 15.0 | 15.7 |
| 6. HITL gate spanning hours | 22.0 | 23.1 |
| 7. Stale external state | 15.0 | 15.7 |
| 8. Replay determinism | 30.0 | 31.4 |

_All runs in offline mock mode — no LLM cost incurred. Re-run with `HALFMARATHON_OFFLINE_LLM` unset to populate the cost ledger._

## Notes

- Dimensions 1, 6, 8 are deterministic and fully exercised offline.
- Dimensions 2, 3, 4, 5, 7 are partially exercised in offline mode: 
  they validate structure (no crash, output bounds, no double-publish, 
  no off-topic in published, no stale references) but cannot evaluate 
  LLM-dependent quality (compaction, recall, drift under adversarial 
  pressure). The `findings.md` writeup calls out each gap explicitly.
- Detailed per-cell metrics are in `eval-summary.json` next to this file.
