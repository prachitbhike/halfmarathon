# halfmarathon eval matrix

_Generated from `results/eval-summary.json` (2026-04-23T20:51:55.529254+00:00 -> 2026-04-23T21:22:26.371599+00:00)._

Impls run: claude_sdk, langgraph, temporal_pydantic.  Skipped: letta.

## Status legend

- **PASS** - dimension exercised and behavior matched expectation
- **PART** - partially passed; see notes for caveat
- **FAIL** - exercised, expected behavior not observed
- **skip** - impl not runnable in this environment (e.g. missing API key)
- **ERR**  - the test harness itself crashed

Each cell also carries a **0.00-1.00 accuracy score** derived from the dimension's metrics (see `accuracy_explanation` in the JSON summary for the exact formula per dimension). Accuracy is skipped for `skip` and `ERR` cells.

## Accuracy matrix (0.00-1.00)

| Dimension | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| **1. Crash recovery** | — | 1.00 (PASS) | 1.00 (PASS) |
| **2. Multi-day with sleeps + multi-restart** | — | 1.00 (PASS) | 1.00 (PASS) |
| **3. Cross-window continuity (structural)** | — | 1.00 (PASS) | 1.00 (PASS) |
| **4. Memory recall (filing)** | — | 1.00 (PASS) | 1.00 (PASS) |
| **5. Goal drift** | — | 0.69 (FAIL) | 0.69 (FAIL) |
| **6. HITL gate spanning hours** | 1.00 (PASS) | 1.00 (PASS) | 1.00 (PASS) |
| **7. Stale external state** | — | 0.50 (PART) | 0.50 (PART) |
| **8. Replay determinism** | 0.66 (PART) | 1.00 (PASS) | 1.00 (PASS) |
| **Composite (mean)** | **0.83** | **0.90** | **0.90** |

_Composite is the arithmetic mean of a column's per-dimension accuracy scores; skipped/errored cells are excluded. Equal weights across all 8 dimensions - reweight yourself if recovery (1-3) or HITL (6) matter more for your use case._


## Composite by use-case profile

Single composite scores hide which workload an impl is strong for. These rows re-weight the dims for different kinds of project. Each profile excludes impls that didn't run a critical mass of the weighted dims (shown as `—`).

| Profile | `claude_sdk` | `langgraph` | `temporal_pydantic` | Boosted dims |
| --- | --- | --- | --- | --- |
| **Production durability** | — | **0.90** | **0.90** | d1x3, d2x3, d6x1.5, d7x2 |
| **Compliance / audit** | — | **0.91** | **0.91** | d1x2, d2x1.5, d7x1.5, d8x3 |
| **Quality-sensitive** | — | **0.88** | **0.88** | d3x1.5, d4x2, d5x3, d8x1.5 |
| **HITL-critical** | — | **0.89** | **0.89** | d1x1.5, d6x3, d7x2 |
| **Memory-driven** | — | **0.91** | **0.91** | d2x1.5, d3x2, d4x3, d7x1.5 |

_Bold = best in row. Profile descriptions:_

- **Production durability** — Multi-day agents that survive crashes, deploys, and source mutations. Boosts crash recovery, multi-restart, and stale-state detection.
- **Compliance / audit** — Workloads where every action must be reproducible and auditable. Boosts replay determinism and crash-recovery fidelity.
- **Quality-sensitive** — Content-quality-critical use (digests / summaries / recommendations). Boosts goal-drift resistance and memory filing.
- **HITL-critical** — High-stakes flows that gate on human approval. Boosts the approval-gate dim and stale-state detection so the human isn't approving stale content.
- **Memory-driven** — Personal-assistant or research-radar patterns where memory across long horizons IS the value prop. Boosts memory recall and continuity.


## Beyond-accuracy ratings (what dim scores can't measure)

Hand-curated 1-5 scores (5 = best) for the framework qualities developers weigh at adoption time but the dim accuracy can't capture. Ratings reflect honest builder experience from Phases 0-5; rationale lives below the table for auditability.

| Capability | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| Observability / debug UX | 3/5 | 4/5 | **5/5** |
| Mental model simplicity | **5/5** | 3/5 | 2/5 |
| Production scaling | 2/5 | 4/5 | **5/5** |
| Multi-tenancy support | 2/5 | **4/5** | **4/5** |
| Type safety / IDE support | 2/5 | 3/5 | **5/5** |
| **Vendor lock-in** | High — Anthropic API only. SDK is MIT, but the agent loop assumes Claude semantics. Bedrock/Vertex routing exists but you're still on Anthropic models. | Low — Apache 2.0; tied to LangChain ecosystem but model-provider-agnostic. | Low — Temporal is OSS Apache 2.0, polyglot (Go/Python/TS/Java/Ruby/PHP). Pydantic-AI is OSS MIT. Model provider swappable. |

### Rating rationale

**Observability / debug UX**
- `claude_sdk` (3/5): Hooks (PreToolUse, PostToolUse, Stop) + transcripts + git history. DIY observability.
- `langgraph` (4/5): LangGraph Studio + LangSmith integration; checkpoint state inspectable via API.
- `temporal_pydantic` (5/5): Temporal Web UI is the gold standard for workflow debugging: time-travel replay, event history, signal/query inspection.

**Mental model simplicity**
- `claude_sdk` (5/5): Agent reads files, runs in a loop. Almost trivial conceptually.
- `langgraph` (3/5): Graph + state + checkpointer; well-documented but you own routing.
- `temporal_pydantic` (2/5): Workflow/activity determinism contract is famously steep. pydantic-ai layer adds another concept on top.

**Production scaling**
- `claude_sdk` (2/5): Single process per agent. Multi-agent orchestration is your problem.
- `langgraph` (4/5): Stateless workers + checkpointer in shared db scales horizontally.
- `temporal_pydantic` (5/5): Designed for it. Workers scale horizontally, history is durable.

**Multi-tenancy support**
- `claude_sdk` (2/5): One CWD per agent — per-user means per-process or per-directory.
- `langgraph` (4/5): thread_id per user is the canonical pattern; well-supported.
- `temporal_pydantic` (4/5): workflow-per-user is the canonical pattern; well-supported with workflow IDs.

**Type safety / IDE support**
- `claude_sdk` (2/5): Plain Python. Types are what you write.
- `langgraph` (3/5): TypedDict state. Tools and edges are loose.
- `temporal_pydantic` (5/5): Pydantic models flow through Temporal payloads via pydantic_data_converter; workflow inputs typed.


## Cost projection (extrapolated to production scale)

Per-digest cost is **measured** for impls run with a real LLM, and **estimated** (~$0.24/digest) for impls run in offline mock mode based on typical token usage with Sonnet 4.6. The framework adds ~zero cost beyond the model API.

| Workload | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| **$ per digest** (basis) | $2.39 (measured) | $0.24 (estimated) | $0.24 (estimated) |
| Personal (1/wk) | $2.39/wk | $0.24/wk | $0.24/wk |
| Small team (10/wk) | $23.93/wk | $2.40/wk | $2.40/wk |
| Small SaaS (50/wk) | $119.66/wk | $12.00/wk | $12.00/wk |
| Large SaaS (5000/wk) | $11,966.25/wk | $1,200.00/wk | $1,200.00/wk |

_Notes:_
_- Measured numbers come from the actual API spend on this impl's_ _real-LLM run; estimated numbers assume `task/llm.py`'s relevance_ _scoring + summarization pattern at ~30k input + ~10k output tokens_ _per published digest._
_- claude_sdk's per-digest cost is ~6x higher than the others_ _because the agent loop emits multiple LLM exchanges per tick (Read,_ _Write, Bash, summarize)  vs the offline pair's single batched_ _relevance call + N summarization calls._
_- Real-world variance: ±2x depending on user interest count, source_ _event density, and cache hit rate._


## Footprint comparison (what the impl IS, not what it does)

Dim accuracy is largely an LLM property. These rows are framework properties — they're what differentiates impls that share the same LLM (e.g. langgraph and temporal_pydantic on the offline mock).

| Metric | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| Impl source LOC (excl. tests/init) | 317 | 419 | 1019 |
| Source files | 1 | 1 | 4 |
| Direct pip deps | 1 | 2 | 2 |
| Long-lived services | 0 | 0 | 1 |
| Setup steps from clean machine | 2 | 1 | 2 |
| Mean wall-clock per cell (s) | 1318.0 | 24.5 | 25.8 |
| Production storage | Filesystem (progress.md + knowledge_base.json + git) | SQLite file (or Postgres for HA) | Temporal history (Cassandra/Postgres in prod) |

### Setup steps detail

**`claude_sdk`** — 2 step(s):
  1. uv pip install claude-agent-sdk
  2. Anthropic API key (real LLM, no offline mock)

**`langgraph`** — 1 step(s):
  1. uv pip install langgraph langgraph-checkpoint-sqlite

**`temporal_pydantic`** — 2 step(s):
  1. uv pip install pydantic-ai-slim[temporal,anthropic] temporalio
  2. Temporal dev server: bundled in temporalio.testing — auto-spawn


## Per-impl differentiating signals

_For each impl: dims where it notably differs from the others_ _(>=0.10 above or below the median of peers), plus footprint-level_ _callouts where this impl is uniquely highest/lowest. If an impl has_ _no entries here, it's in the pack on every measured axis._

### `claude_sdk`

_Cells run: 2/8._

**Differentiating weaknesses** (≥0.10 below peer median):
- dim 8 *Replay determinism*: **0.66** (-0.34 vs median)

**Footprint-level callouts:**
- Lowest LOC (317)
- Slowest mean wall-clock (1318.0s/cell)
- Fewest services to run (0)
- Best at mental model simplicity (5/5)
- Weakest at production scaling (2/5)
- Weakest at multi-tenancy support (2/5)
- Weakest at type safety / ide support (2/5)

### `langgraph`

_Cells run: 8/8._

**Footprint-level callouts:**
- Fastest mean wall-clock (24.5s/cell)
- Fewest services to run (0)

### `temporal_pydantic`

_Cells run: 8/8._

**Footprint-level callouts:**
- Highest LOC (1019)
- Most services to run (1)
- Best at observability / debug ux (5/5)
- Weakest at mental model simplicity (2/5)
- Best at production scaling (5/5)
- Best at type safety / ide support (5/5)

## Status matrix (with notes)

| Dimension | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| **1. Crash recovery** | — | **PASS** - Resume-from-partial reaches the same end state as a fresh single pass: 2 digests published, KB=37 items, no duplicates. | **PASS** - Resume-from-partial reaches the same end state as a fresh single pass: 2 digests published, KB=38 items, no duplicates. |
| **2. Multi-day with sleeps + multi-restart** | — | **PASS** - Survived 3 restarts across the 2-week fixture; final state byte-equivalent to a single-pass run. | **PASS** - Survived 3 restarts across the 2-week fixture; final state byte-equivalent to a single-pass run. |
| **3. Cross-window continuity (structural)** | — | **PASS** - Structural invariants held: 2 digests published, each with <= 8 items, KB=37. (Compaction quality not exercised — see findings.) | **PASS** - Structural invariants held: 2 digests published, each with <= 8 items, KB=38. (Compaction quality not exercised — see findings.) |
| **4. Memory recall (filing)** | — | **PASS** - Probe event probe_0001 was filed and surfaced in a published digest (URL match). The impl correctly captured the planted fact. | **PASS** - Probe event probe_0001 was filed and surfaced in a published digest (URL match). The impl correctly captured the planted fact. |
| **5. Goal drift** | — | **FAIL** - 5/16 published items are off-topic (ratio=0.31); above the 25% threshold — significant drift. Adversarial overlay added 8 deliberately off-topic events; 15 total off-topic events in the merged timeline. | **FAIL** - 5/16 published items are off-topic (ratio=0.31); above the 25% threshold — significant drift. Adversarial overlay added 8 deliberately off-topic events; 15 total off-topic events in the merged timeline. |
| **6. HITL gate spanning hours** | **PASS** - Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W14 (no double-publish). | **PASS** - Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W14 (no double-publish). | **PASS** - Held-approval flow honored: drafted in Phase A without publishing, picked up the approval after Phase B and published week-2026-W14 (no double-publish). |
| **7. Stale external state** | — | **PART** - Impl completed, but 1 of 2 deleted events were referenced in published digests after Phase B. The KB carried items forward without re-checking the source. Right behavior: re-fetch on resume and drop or flag stale references. | **PART** - Impl completed, but 1 of 2 deleted events were referenced in published digests after Phase B. The KB carried items forward without re-checking the source. Right behavior: re-fetch on resume and drop or flag stale references. |
| **8. Replay determinism** | **PART** - Same digests published, but 1 of 1 have diverging body text (similarity=0.662; LLM stochasticity is the most likely cause). | **PASS** - Two clean runs produced byte-identical published digests for all 2 weeks (deterministic in this configuration). | **PASS** - Two clean runs produced byte-identical published digests for all 2 weeks (deterministic in this configuration). |

## Accuracy components (per dimension, per impl)

_Sub-scores that combine into the single accuracy value. A single_ _number hides which part of the dimension failed - this table shows it._

### 1. Crash recovery
_Formula: `mean(jaccard(resumed_weeks, expected), ratio_match(kb_resumed, kb_fresh))`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| week_recovery | — | 1.00 | 1.00 |
| kb_match | — | 1.00 | 1.00 |

### 2. Multi-day with sleeps + multi-restart
_Formula: `mean(jaccard(multi_weeks, expected), ratio_match(kb_multi, kb_fresh))`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| week_recovery | — | 1.00 | 1.00 |
| kb_match | — | 1.00 | 1.00 |

### 3. Cross-window continuity (structural)
_Formula: `mean(jaccard(published, expected), fraction of digests ≤ max_items, 1 if kb nonempty)`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| week_overlap | — | 1.00 | 1.00 |
| bound_rate | — | 1.00 | 1.00 |
| kb_nonempty | — | 1.00 | 1.00 |

### 4. Memory recall (filing)
_Formula: `1.0 if probe in published digest, 0.5 if in KB only, 0.0 if neither`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| surface_score | — | 1.00 | 1.00 |

### 5. Goal drift
_Formula: `1 - (off-topic items / published items)`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| on_topic_rate | — | 0.69 | 0.69 |

### 6. HITL gate spanning hours
_Formula: `mean(gate_held: no unauthorized publish in Phase A, drafted_before_approval, published_after_approval, published_nonempty)`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| gate_held | 1.00 | 1.00 | 1.00 |
| drafted_before_approval | 1.00 | 1.00 | 1.00 |
| published_after_approval | 1.00 | 1.00 | 1.00 |
| published_nonempty | 1.00 | 1.00 | 1.00 |

### 7. Stale external state
_Formula: `1 - (leaked_deleted_urls / total_deletion_targets)`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| freshness | — | 0.50 | 0.50 |
| leak_rate | — | 0.50 | 0.50 |

### 8. Replay determinism
_Formula: `jaccard(run_a_weeks, run_b_weeks) * mean(difflib ratio over shared weeks)`_

| Component | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| workflow_overlap | 1.00 | 1.00 | 1.00 |
| byte_similarity | 0.66 | 1.00 | 1.00 |

## Per-cell elapsed time (seconds)

| Dimension | claude_sdk | langgraph | temporal_pydantic |
| --- | --- | --- | --- |
| 1. Crash recovery | — | 30.4 | 33.1 |
| 2. Multi-day with sleeps + multi-restart | — | 49.1 | 51.6 |
| 3. Cross-window continuity (structural) | — | 14.0 | 14.7 |
| 4. Memory recall (filing) | — | 14.0 | 14.7 |
| 5. Goal drift | — | 14.0 | 14.6 |
| 6. HITL gate spanning hours | 1295.4 | 21.0 | 22.0 |
| 7. Stale external state | — | 25.0 | 26.3 |
| 8. Replay determinism | 1340.7 | 28.0 | 29.4 |

## Cross-cut metrics (per impl, summed across all dimensions)

| Metric | `claude_sdk` | `langgraph` | `temporal_pydantic` |
| --- | --- | --- | --- |
| Total elapsed (s) | 2636.1 | 195.6 | 206.5 |
| Total digests published | 2 | 10 | 10 |
| Seconds per digest | 1318.03 | 19.56 | 20.65 |
| Estimated cost (USD) | $4.7865 | $0.0000 | $0.0000 |
| USD per digest | $2.3933 | $0.0000 | $0.0000 |

## Notes

- Dimensions 1, 6, 8 are deterministic and fully exercised offline.
- Dimensions 2, 3, 4, 5, 7 are partially exercised in offline mode: 
  they validate structure (no crash, output bounds, no double-publish, 
  no off-topic in published, no stale references) but cannot evaluate 
  LLM-dependent quality (compaction, recall, drift under adversarial 
  pressure). The `findings.md` writeup calls out each gap explicitly.
- Per-cell accuracy formulas are stored alongside each result in `eval-summary.json` under `accuracy_explanation` for auditability.
