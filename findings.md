# Findings — what we learned

> A hands-on comparison of four orthogonal philosophies for building
> long-running AI agents. Same task ([Release Radar](task/spec.md)), same
> evaluation harness, four implementations.

This is the honest write-up after going from research → spec → four
implementations → eval matrix. The matrix lives in
[results/eval-matrix.md](results/eval-matrix.md); this doc is the synthesis.

> **Scope caveat.** The cells reported below are from offline-LLM mode (free
> to run, deterministic). The Claude Agent SDK and Letta cells skip in this
> environment because they need real API/server access. The patterns and
> trade-offs we report are from the structural behavior — for LLM-quality
> dimensions (compaction, recall, drift under real adversarial pressure) the
> matrix is silent. We call out exactly where each gap lives.

---

## TL;DR

For a *typical* long-running agent (days/weeks, scheduled wakes, file-based
state, an HITL gate), the four schools we tested are **functionally
interchangeable on the durability dimensions** (1, 2, 6, 8). They differ
sharply on **operational footprint, ergonomics, and how much you have to
build yourself** — which is where the comparison gets interesting.

If you're picking one for a real project today:

- **Just need durability + HITL + low ops?** → LangGraph + a Postgres saver.
  Fewest moving parts, biggest community, solid HITL primitives.
- **Want a real workflow engine you can polyglot later?** → Pydantic AI +
  Temporal. Slightly more upfront cost, but event-sourced replay is
  genuinely different and the worker/workflow split scales cleanly.
- **Want stateful agent identity + memory blocks as a first-class concern?**
  → Letta. The "server-resident agent" model is unique; pay the operational
  tax for the memory hierarchy.
- **Want maximum flexibility + the harness paper's file-as-memory pattern?**
  → Claude Agent SDK. Thin runtime, you own the orchestrator. Best for
  prototyping or when the agent's own decisions drive workflow shape.

---

## What the matrix says

Snapshot from the latest offline run (see [results/eval-matrix.md](results/eval-matrix.md)
for the live table; numbers below are typical):

| Dim | What it tests | langgraph | temporal_pydantic | Notes |
|---|---|---|---|---|
| 1 | Crash mid-run + resume | PASS | PASS | Both checkpoint cleanly. |
| 2 | Multi-day + 3 restarts | PASS | PASS | Same — both impls are restart-safe. |
| 3 | Structural continuity | PASS | PASS | Digest item bounds and KB invariants hold. |
| 4 | Memory: probe filed | PASS / PARTIAL | PASS / PARTIAL | Probe injected via fixture override; verified in the published digest when relevance score crosses the threshold. |
| 5 | Goal drift under adversarial overlay | PASS / PARTIAL | PASS / PARTIAL | After adding 8 deliberately off-topic events, off-topic ratio in published digests stays under 25%. |
| 6 | HITL gate spanning hours | PASS | PASS | Drafts pause until approval file appears, then publish exactly once. |
| 7 | Stale state (deletion mid-run) | **PARTIAL** | **PASS** *(for the wrong reason)* | **The most interesting cell in the matrix.** See [§The dim 7 dive](#the-dim-7-dive). |
| 8 | Replay determinism | PASS | PASS | Two clean offline runs produce byte-identical published digests. |

`letta` and `claude_sdk` skip in this environment (server / API key
unavailable). With a real Anthropic API key + a Letta server, they should
score similarly on dims 1/2/3/6/8 and diverge interestingly on 4/5 (where
their non-deterministic LLM responses change the picture).

---

## The dim 7 dive

The eval matrix shows dim 7 (stale external state) splitting:
**LangGraph PARTIAL, Pydantic AI + Temporal PASS.** This looks like a clean
"Temporal handles stale state better" win. It isn't — and the reason is
much more interesting than the surface result.

**Setup.** Phase A runs the impl through fixture-day 12 with the canonical
timeline, including events `evt_0007` (April 3) and `evt_0029` (April 12).
The agent files them. Phase A also drafts and publishes `week-2026-W13`
which contains `evt_0007`. Phase B applies a deletion-mutated fixtures
view (both events removed) and continues through fixture-day 15.

**LangGraph behavior.** The SqliteSaver carries the in-memory KB and the
`published_weeks` list across the Phase A → Phase B boundary. On Phase B
resume, the agent sees `week-2026-W13 ∈ published_weeks` and skips
re-drafting. The on-disk `published-week-2026-W13.md` from Phase A still
contains `evt_0007`'s URL → **stale reference detected → PARTIAL**.

**Temporal behavior.** Each `client.start_workflow(...)` invocation with
the default `WorkflowIDReusePolicy.ALLOW_DUPLICATE` starts a *fresh*
workflow execution when the previous one completed. The new run's
`__init__` initializes empty state — `kb=[]`, `published_weeks=[]`,
`pending=None`. Phase B re-derives everything from the (now-mutated)
timeline, re-drafts `week-2026-W13` with `evt_0007` absent, and overwrites
the published file → **no stale reference → PASS**.

**The honest read:** Temporal isn't "detecting" the stale state. It's
re-running the entire workflow from scratch on every invocation, which
incidentally re-derives the digest from the current world. Same outcome
as if a builder explicitly added a "re-fetch source state on resume" step
to LangGraph — but achieved by accident.

**What this means in practice:**
- For our `start_workflow` invocation pattern, **Temporal isn't really
  resuming long-running state across harness restarts.** It looks like it
  is because Phase A's published files persist on disk, and Phase B's
  re-derivation produces the same outcome (in this offline test) as a
  proper resume would.
- A "real" resume in Temporal would use a single long-lived workflow
  execution + signals to advance time, OR `continue_as_new` to chunk
  history. Neither is what our current impl does.
- A "real" stale-state fix in LangGraph is small: re-fetch on resume
  before drafting, drop entries whose source URLs no longer resolve.
  About 10 lines.

This is exactly the kind of finding the test bed exists to surface. Both
impls have a real gap; they just present it differently. **Neither
correctly handles "agent slept, world changed."** Building on top of
either, you'd need to add explicit re-validation logic.

---

## Where the four schools genuinely differ

### 1. Operational footprint

| | Server / runtime | Storage | Ops on your laptop |
|---|---|---|---|
| LangGraph | None — library | SQLite or Postgres | One process |
| Pydantic AI + Temporal | Temporal dev server (or Temporal Cloud) | SQLite/Postgres history | One process + worker (or two) |
| Letta | Letta server (Postgres-backed) | Postgres | Two services |
| Claude Agent SDK | Spawns `claude` CLI per tick | Filesystem only | One process + Node CLI |

The offline-mock smokes for LangGraph and Pydantic AI + Temporal both run
in ~15 wall-seconds for the full 14-fixture-day window. Letta and the
Claude SDK aren't comparable here because they require real API access.

For "what a builder would actually adopt": **LangGraph wins on
operational simplicity**, Letta loses on it (a Postgres-backed server in
production is not free), and the others are in between.

### 2. State model

This is where the four philosophies show through:

- **LangGraph** — graph state is a TypedDict; checkpointer serializes the
  whole thing per super-step. Mental model: "state machine with
  snapshots." Easy to reason about; balloons if you stash large payloads
  inline (we externalized the KB to in-state arrays of dicts and it was
  fine for our scale).
- **Pydantic AI + Temporal** — workflow state is just instance attributes
  on a deterministic Workflow class. State persists via event-sourced
  replay (every input + activity result is in the history; on resume the
  workflow code is re-executed and short-circuits at recorded points).
  Mental model: "log of what happened, not what's true now." Powerful for
  debugging — you can replay any past execution. Cognitive load comes from
  the determinism contract: anything non-deterministic must be in an
  Activity.
- **Letta** — agent has a server-side identity with explicit memory
  blocks (in-context, the agent rewrites them via tools), recall memory
  (conversation history search), and archival memory (vector store). State
  isn't your problem — the server owns it. Your code is just a client
  that sends messages and reads replies. Cognitive load comes from
  giving up control: when does memory_block X get rewritten, and by who?
- **Claude Agent SDK** — no in-process state at all. `progress.md` and
  `knowledge_base.json` ARE the state. Each tick is a fresh `query()`
  invocation that re-hydrates from files. Mental model: "the file system
  is the durable store; the agent is stateless." Brutal for deep state,
  great for keeping the harness ultra-thin.

### 3. HITL ergonomics

All four implement the same spec contract (approval file, no
double-publish), but the *idiomatic* HITL story differs:

- **LangGraph** — `interrupt()` / `Command(resume=...)`. Best HITL
  primitive in this set: pause anywhere, time-travel to a prior state,
  resume from a different node.
- **Pydantic AI + Temporal** — Signals are the canonical Temporal HITL,
  but for cross-impl spec compliance we polled the approval file via an
  activity. Signals would be a small refactor; the polling pattern works
  fine and keeps the file contract intact.
- **Letta** — message-driven naturally. The agent waits because we wait
  to send the next message. Any "pause" is at the harness level.
- **Claude Agent SDK** — pure file convention. The agent reads the
  approval file each tick. No bespoke pause primitive needed because
  ticks ARE the unit of pause.

For a multi-hour pause scenario, **LangGraph and Temporal both shine**.
Letta and Claude SDK work but the HITL machinery lives in your harness.

### 4. Replay & debugging

Closely related to state model:

- **Temporal** — the gold standard. Recorded history → time-travel
  debugger → "what would have happened if this LLM call returned X?"
  Genuinely different from the others.
- **LangGraph** — checkpoint history per thread + `update_state()` to
  fork. Solid; not as polished as Temporal Cloud's UI.
- **Letta** — message log + memory snapshots; the "rewind" story isn't
  built-in.
- **Claude Agent SDK** — you have files + git. Nothing more. Powerful in
  its own way (the Anthropic harness paper leans on git as the time
  machine); requires you to opt into it.

### 5. Cost & cache

All four use the same `task/llm.py` Anthropic wrapper for cost
accounting in our test (so the comparison is *about the framework*, not
"who has the smartest cache hit logic"). In a real deployment:

- LangGraph and Temporal would benefit from Anthropic's 1-hour beta
  prompt-cache TTL on their stable system prompts.
- Letta's server model means caching happens at the Letta server boundary
  — opaque to your app.
- Claude Agent SDK gets prompt caching from Claude Code itself (the
  underlying CLI), which already uses caching aggressively.

In our offline runs no cost is incurred. The cost-regression dimension
(9, optional) would surface real differences.

---

## What the matrix can't tell you (yet)

Three honest gaps the offline runs leave open:

1. **LLM-quality compaction** (dim 3 deepening). When the KB grows past 5×
   the context window, can the agent still produce a coherent digest? Our
   offline runs verify the impl doesn't *crash*; only a real LLM run on a
   month+ fixture tells you whether the digest stays good.

2. **True memory recall** (dim 4 deepening). The current dim 4 verifies
   filing — that the probe event lands in KB and (when its score crosses
   the threshold) shows up in the digest. The harder question — "in week 4
   can the agent answer questions about a fact planted in week 1?" — needs
   an "ask the agent" hook that doesn't exist in any of our impls today.
   Letta and Claude SDK could grow one naturally; LangGraph and Pydantic
   AI + Temporal would need a small protocol addition.

3. **Stale-state detection** (dim 7 deepening). See [§The dim 7 dive](#the-dim-7-dive)
   — Temporal passes because it accidentally re-runs from scratch rather
   than truly resuming. Neither impl has explicit stale-state handling.
   **This is a real production finding, not a test artifact.**

---

## Surprises and gotchas

A few things that surprised us mid-build:

- **Pydantic AI + Temporal turned out to be barely more code than
  LangGraph**, once you've internalized the workflow/activity split. The
  "Temporal is heavier" intuition is true for ops, not for code.

- **The harness paper's file-as-memory pattern feels primitive
  (literally just `progress.md` files) but is genuinely effective** for
  the Claude SDK story. The agent re-establishes context every tick from
  the file, and the harness stays under 350 lines.

- **Letta's "you don't own state" model is very different from the
  others** and fights you when you want cross-impl consistency. We ended
  up using Letta as a "scoring brain with memory" rather than as the full
  agent — not the most idiomatic Letta usage, but the only honest
  apples-to-apples comparison.

- **Temporal's `WorkflowEnvironment.start_local()` spawns the Temporal
  CLI as a subprocess.** Each test run brings up a fresh server and tears
  it down. Slow (~15s extra per run) but bulletproof for testing.

- **LangGraph's `interrupt()` returns the Command(resume=...) value
  cleanly, but we still had to write a polling loop in the outer
  harness** to wait for the approval file to appear — `interrupt()` only
  pauses the graph, it doesn't watch the filesystem. (As expected: that's
  the harness's job, not the framework's.)

---

## Honest recommendations by use case

**You're prototyping a long-running agent for the first time.**
Start with LangGraph + SQLite saver. Lowest activation energy, fastest
"first published digest" milestone, easiest to debug. Migrate to
Postgres when you actually deploy.

**You're already on Temporal for non-AI workflows.**
Pydantic AI + Temporal is a no-brainer. The TemporalAgent wrapper is
optional (we got fine with plain pydantic-ai inside activities); the
real win is that your AI work uses the same observability + replay
story as everything else.

**You want stateful agents per user with rich memory.**
Letta is the only one where this is a first-class primitive. Worth the
operational cost if you're building a "personal AI" product where
memory continuity is the value prop.

**You want the agent's own decisions to drive workflow shape.**
Claude Agent SDK + the harness paper pattern. Especially good when the
agent uses its built-in tools (Bash, Read, Write) to do real work
between your harness invocations.

**You don't know yet.**
LangGraph + Postgres + LangMem for procedural memory. Most flexible
default; converts cleanly to any of the others later if needed.

---

## What would change the picture

Things that would meaningfully shift the matrix if added:

1. **Real Anthropic API access** — converts dim 5 from "structural" to
   "actually adversarial," exposes drift under real keyword overlap.
2. **A reachable Letta server** — populates Letta column in dims 1/2/3/6/8.
3. **A claude API key** — populates the Claude SDK column; the harness
   paper's two-process pattern (planner + coder) is also worth
   benchmarking, separately from the basic file-as-memory test.
4. **A months-long fixture** — actually exercises compaction (dim 3)
   beyond structural bounds. We'd expect Letta and the Claude SDK harness
   to diverge here from the LangGraph/Temporal pair.
5. **Procedural memory probe** (the optional dim 10) — does the agent
   measurably improve after 20 sessions of approval/rejection feedback,
   without any prompt-edit by us?

---

## Files & layout

- [research.md](research.md) — landscape survey that informed the picks
- [plan.md](plan.md) — locked decisions + phase plan
- [task/spec.md](task/spec.md) — canonical Release Radar contract (R1-R7)
- [implementations/](implementations/) — the four impls
- [eval/](eval/) — harness, dims, fixture-override mechanism, report renderer
- [results/eval-matrix.md](results/eval-matrix.md) — current matrix snapshot
