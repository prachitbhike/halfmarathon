# Test Application Plan

> Goal: hands-on comparison of approaches to building **long-running** AI agents (days/weeks). Not a survey paper — a working test bed where the same task runs against multiple frameworks and the differences become concrete.

## Decisions (locked)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Language | **Python only** | Largest agent ecosystem; all four picks support it natively |
| 2 | Model | **Claude across all four** (Opus 4.7 + Sonnet 4.6 as appropriate) | Holds model quality constant — comparison is about the framework |
| 3 | Realism bar | **"What a builder would see"** | No bench-tuning. Default configs, default storage backends, default examples. If a framework needs hand-tuning to look good, that itself is a finding. |
| 4 | Task | **Release Radar** | Confirmed |
| 5 | Wall-clock budget | **8h compressed** instead of 7-day real-time | Use accelerated-time fixtures for the multi-week dimensions (replay 2 weeks of source events in 8h). See §6.1. |
| 6 | Hosting | **Hosted sandbox (e2b or equivalent)** for agent runtime | See §6.1 — e2b sandboxes for agent processes; supporting infra (Postgres, Temporal, Letta server) co-hosted or external. |
| 7 | Repo | **Public** | Findings + implementations open-sourced; no Anthropic-internal data in the repo. |

## Guiding principle: realistic, not flattering

Every implementation uses **the recommended setup from each framework's own docs** — default checkpointer, default memory store, default example structure. We do not optimize one against another. If a framework requires substantial bespoke work to handle a dimension, that's part of the finding, not something to engineer around.

---

## 1. The shape of the test app

**One canonical task, implemented N times.** Same inputs, same evaluation harness, four orthogonal implementations. The deliverable is a repo where you can:

```bash
make run-langgraph
make run-temporal
make run-letta
make run-claude-sdk

make eval        # scores all four against the 8 test dimensions
make report      # generates comparison matrix
```

Each implementation lives in its own subdirectory, shares a common `eval/` harness, and writes results to a shared `results/` directory.

```
halfmarathon/
  research.md
  plan.md
  README.md
  task/                    # the task spec + fixtures + ground truth
    spec.md
    fixtures/
  implementations/
    langgraph/
    temporal-pydantic/
    letta/
    claude-sdk/
  eval/                    # dimension tests, scoring, report generator
    dimensions/
    harness.py
    report.py
  infra/
    docker-compose.yml     # postgres, temporal, letta server, etc.
  results/
```

---

## 2. The canonical task

**"Release Radar"** — a weekly research agent that:

1. Tracks 5–10 GitHub repos / RSS feeds / blogs (configurable).
2. Wakes on a schedule (e.g. daily) to fetch new items.
3. Maintains a running, evolving knowledge base of "what's interesting" per topic, with attribution.
4. Once a week, drafts a digest. **Requires human approval** before "publishing" (writing to a `digests/` folder).
5. Learns from approval/rejection feedback — adjusts what it considers interesting over time.

### Why this task

| Requirement | How the task forces it |
|---|---|
| Crash recovery | Process killed mid-fetch must not double-publish or lose items |
| Multi-week horizon | Real wall-clock days; cron wake-ups; cheap to run idle |
| Cross-window continuity | Knowledge base grows beyond context across weeks |
| Memory recall | "Did I cover X last week?" "What did I conclude about Y in March?" |
| Goal drift | Sources occasionally publish off-topic content; the agent must stay focused |
| HITL approval | Weekly digest gate; approval can take hours |
| Stale state | Sources change between sleeps (PRs merged, posts edited, repos archived) |
| Replay | Every weekly run should be reproducible from the event log |

It also has the practical virtue of being a thing the user might *actually want* — meaning we can dogfood it.

### What we DON'T build

- Not a coding agent (overlaps too much with what each SDK already ships)
- Not a chatbot (single-session, defeats the point)
- Not multi-agent collaboration (orthogonal; introduces noise)

---

## 3. Implementations to build

Per the research, four orthogonal philosophies. Concrete picks:

| # | Approach | Stack | What we're testing |
|---|---|---|---|
| 1 | **Workflow engine + thin agent** | Pydantic AI + Temporal (Python; OSS server local) | Event-sourced replay, polyglot durability, deterministic orchestrator + non-deterministic activities |
| 2 | **Graph + checkpointer** | LangGraph + Postgres checkpointer | First-class HITL `interrupt()`, time-travel checkpoints, cron jobs |
| 3 | **Stateful-by-design agent server** | Letta (self-hosted, Postgres-backed) | Memory blocks, recall/archival memory, server-resident identity |
| 4 | **Vendor-managed harness pattern** | Claude Agent SDK with file-based memory (Anthropic's harness) | `claude-progress.txt` + git-as-memory + sub-agents pattern, no external orchestrator |

All four use Anthropic Claude (Opus 4.7 + Sonnet 4.6 as appropriate) so model quality is held constant.

### Reasoned omissions

- **Convex / Cloudflare Agents / Mastra** — would require a TS implementation; doable as a v2 but doubles surface area. Skip for now.
- **OpenAI Agents SDK + Temporal** — overlaps philosophically with Pydantic AI + Temporal. Pick one.
- **Anthropic Managed Agents** — $0.08/runtime-hour × 4 implementations × multi-week test = $$$. Maybe a single one-off run as a separate apples-to-oranges exhibit.
- **CrewAI / AutoGen / Agno / etc.** — out of scope (not built for long-running).

---

## 4. The 8 evaluation dimensions

Per `research.md` §7. Each becomes a script under `eval/dimensions/`.

| # | Dimension | How we measure |
|---|---|---|
| 1 | Crash recovery | Kill the process between an LLM decision and the side effect; verify exactly-once execution |
| 2 | Multi-day wall-clock with sleeps | Run for 7+ days in a Docker container; track infra cost/day, recovery from a 24h sleep across a config change |
| 3 | Cross-window continuity | Force the knowledge base past 5× context window; measure quality after compaction (held-out questions) |
| 4 | Memory recall over time | Plant facts in week 1; query in week 4; update in week 2 — verify current vs stale |
| 5 | Goal drift | Inject off-topic items into source feed; measure goal adherence over 100+ turns |
| 6 | HITL gate spanning hours | Hold approval 4h; verify state survival, no double-publish |
| 7 | Stale external state | Mutate sources during sleep (delete repo, edit post); verify detect / refresh / fail-loud |
| 8 | Replay from event log | Replay full week-1 trajectory against modified prompt; diff outputs |

Plus optional: (9) **cost regression** (caching on/off), (10) **procedural memory** (does the agent improve from feedback over 4 weeks).

Score per dimension: **pass / partial / fail / not applicable**, with a one-paragraph note. Output is a markdown matrix, not a leaderboard — different frameworks will win on different axes.

---

## 5. What "done" means

A successful test app produces:

1. **Working code** for all four implementations, runnable from one repo.
2. **Eval matrix** as a markdown table, scoring each on the 8 dimensions, with notes.
3. **Findings document** (`findings.md`) — the actual "honest take" after running the eval. Where each approach shines, where it breaks, what surprised us, what would I pick for what use case.
4. **Cost ledger** — actual $$ spent during the multi-day run, broken down by framework.
5. **Reproducible** — `make sandbox-<impl>` provisions the e2b sandbox for that implementation; `make eval` runs the harness; `make report` generates the matrix. Anyone can clone and re-run.

Explicit non-goals:
- Not a benchmark suite (sample size = 1 task)
- Not a tutorial / docs site
- Not exhaustive across every framework — four picks, deeply

---

## 6. Hosting + compressed-time strategy

### 6.1 Hosted sandbox

Each implementation runs as a process in its own **e2b sandbox** (or equivalent — Modal, Daytona, Codesandbox CDE all in scope; e2b is the default). Why hosted:

- Multi-day runs survive laptop sleep / wifi flakiness.
- Reproducible execution environment (any contributor can re-run).
- Realistic — most production agents run in something like e2b/Modal, not on a laptop.
- Sandbox lifecycle is part of the test: how each framework recovers when the sandbox is paused/resumed mirrors a real production restart.

**Architecture:**

```
┌─────────────────────────────────────────────────────┐
│  e2b sandbox per impl  (one of 4)                   │
│  ┌────────────────────────────────────────────────┐ │
│  │  Agent process (LangGraph / Pydantic AI / etc) │ │
│  │  + local Postgres/SQLite if needed             │ │
│  │  + local Temporal dev server (impl 2)          │ │
│  │  + local Letta server (impl 3)                 │ │
│  └────────────────────────────────────────────────┘ │
│           │ Anthropic API                           │
│           │ Source feeds (frozen fixtures)          │
└─────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│  Eval harness (separate sandbox or local)           │
│  - Reads results/ from each impl                    │
│  - Runs dimension tests                             │
│  - Generates comparison matrix                      │
└─────────────────────────────────────────────────────┘
```

**Open implementation question for phase 0:** e2b sandboxes have a max lifetime (24h by default, extendable). Two options for the 8h compressed test:

1. **Single sandbox per impl, 8h continuous** — simplest. Each framework runs uninterrupted in its own sandbox.
2. **Pause/resume the sandbox mid-run** — explicitly tests recovery. More realistic for "what a builder would see in prod" but adds setup work.

I'd start with (1) and add (2) as part of dimension 1 (crash recovery) — pause the sandbox, resume it, verify state.

### 6.2 Compressed time

Real long-running agents react to **events arriving over time**. We compress 2 weeks of source events into 8h:

- Frozen fixtures: 2 weeks of real RSS/GitHub releases pre-recorded as a JSON timeline.
- A "fixture clock" replays events at 42× real speed (1 fixture-day = ~34 fixture-minutes).
- The agent's *internal* timers (sleep, scheduled wake, cron) use the same clock — so a "daily" wake fires every ~34 minutes.
- Anthropic API calls are real-time (we're not faking model latency or cost).

This lets us actually exercise dimensions 2, 4, 5, 7 (sleeps, memory recall over weeks, drift, stale state) within a single 8h budget while keeping cost/recovery numbers honest.

**Caveat to flag in findings:** compressed time understates one real problem — Anthropic's prompt cache TTL is 5 minutes by default. A real "wake daily" cadence eats the cache miss every wake; in our compressed run, the same cadence often hits cache. We will explicitly call this out and run a small uncompressed comparison for the cost dimension (9).

## 7. Suggested phasing

I'd phase this so we get a working comparison quickly, then deepen.

### Phase 0 — Foundations (½ day)
- Repo scaffold, e2b sandbox templates (one per impl), `task/spec.md` finalized, frozen fixture timeline (2 weeks of RSS/GitHub events), common `task/types.py`, fixture clock library.

### Phase 1 — Two implementations, basic happy path (1–2 days each)
- LangGraph version (easiest baseline; first to validate the task spec end-to-end)
- Claude Agent SDK version (most architecturally distinct; first to validate the file-as-memory pattern)
- Both run end-to-end against the fixture set in their own e2b sandbox. No eval harness yet.

### Phase 2 — Eval harness, dimensions 1, 6, 8 (1 day)
- These three are deterministic and fast (crash, HITL, replay).
- First version of comparison matrix.

### Phase 3 — Two more implementations (1–2 days each)
- Pydantic AI + Temporal
- Letta

### Phase 4 — The 8h compressed run (1 day per impl, runs overnight in parallel)
- Run dimensions 2, 3, 4, 5, 7 against all four using the fixture clock from §6.2.
- All four sandboxes run in parallel; results land in shared `results/`.
- Cost ledger from real Anthropic API spend.

### Phase 5 — Findings + writeup (1 day)
- `findings.md`, README polish, public-repo polish (LICENSE, contributor notes).

**Total estimate:** ~2 weeks of focused work, with Phase 4 8h runs scheduled overnight.

---

## 8. Risks I want to flag now

- **Letta server operational tax.** It's a real Postgres-backed server; takes the most setup. If self-hosting in e2b is painful, fall back to Letta Cloud for parity (and call out the difference in findings).
- **Temporal in an e2b sandbox.** Temporal dev server runs fine in a single container; HA needs Temporal Cloud. For this comparison, dev server is enough — production-HA story goes in findings.
- **Cross-framework apples-to-apples is genuinely hard.** Each framework will want to interpret the task slightly differently. The `task/spec.md` discipline + shared `task/types.py` is what keeps it honest.
- **Compressed time hides cache TTL costs.** Documented in §6.2 — we'll run a small uncompressed cost comparison (dim 9) to surface this.
- **Claude Agent SDK is the odd one out.** It's not really a "framework" — it's an agent runtime assuming external orchestration. Fairest comparison wraps it in a thin scheduler (cron + state-file pattern from Anthropic's harness paper).
- **e2b sandbox lifetime.** 24h default; we're under that for the 8h test. Anything longer needs explicit `keep_alive` / persistent-sandbox setup.

---

## 9. What I'll build first (Phase 0)

1. `README.md` — project overview, links to research.md and plan.md.
2. `LICENSE` — MIT (public repo).
3. `task/spec.md` — full task definition, source list, evaluation criteria.
4. `task/fixtures/` — 2-week timeline of frozen RSS/GitHub events as JSON.
5. `task/clock.py` — fixture clock (replays events at 42×; agent timers wired to the same clock).
6. `task/types.py` — shared input/output schemas (Pydantic).
7. `infra/e2b/` — sandbox templates per impl (Dockerfile + e2b config).
8. `implementations/langgraph/` — bare-bones LangGraph version end-to-end on fixtures, no eval yet.

That's a one-day foundation. After Phase 0 lands I'll check in before moving to Phase 1.
