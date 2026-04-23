# Long-Running Agents: Landscape Survey

> Research compiled April 2026 to inform a comparative test application.
> "Long-running" means agents that persist across days/weeks — not single sessions of hours.

---

## 1. Why this is hard

A multi-day agent is a different beast from a chatbot. The properties that don't matter for a 30-second turn become load-bearing:

| Problem | Why it bites long-running agents |
|---|---|
| **Crash recovery** | A multi-day run will outlive its process — hardware fails, deploys ship, OOMs happen. State in RAM is lost. |
| **Context bloat / rot** | Even with 1M-token windows, [Chroma's 2025 study](https://research.trychroma.com/context-rot) showed measurable quality degradation past ~30K tokens. Multi-day agents generate orders of magnitude more text than fits — and stuffing it all in degrades reasoning even when it fits. |
| **Memory** | Compaction loses information. The agent needs durable, queryable memory beyond the live context (working / episodic / semantic / procedural). |
| **Cost** | A 5K-token system prompt × thousands of calls dwarfs the per-call cost. Without prompt caching and tiered models, week-long agents are wildly uneconomical. |
| **Goal drift** | [Chen et al. 2025](https://arxiv.org/abs/2505.02709) showed every frontier model drifts under long horizons + competing objectives + adversarial nudges. Drift usually manifests as *inaction*, not active misalignment. |
| **Human-in-the-loop** | Multi-day agents take consequential actions. Approval gates must not block threads or lose state. |
| **Wake-ups** | Don't poll every second; don't sleep through a webhook. |
| **Stale external state** | Agent slept 6 hours; the PR it was reviewing got merged, the file it cached got deleted. |
| **Determinism for replay** | LLMs are non-deterministic even at temp 0. Yet replay and debugging require reproducibility. |

A serious comparison must exercise all of these — see [§7 Test Dimensions](#7-test-dimensions).

---

## 2. The four schools of thought

Frameworks cluster into roughly four philosophies:

1. **Workflow engine + thin agent.** A real durable execution engine (Temporal, Restate, DBOS) handles persistence; the agent layer is just code. Maximum flexibility, maximum operational responsibility.
2. **Graph + checkpointer.** A state-machine library (LangGraph, LlamaIndex Workflows, Pydantic Graph) with pluggable persistence backends. Good defaults; abstraction tax.
3. **Stateful-by-design agent server.** The agent is a server-resident object with durable identity and a memory hierarchy (Letta). Persistence isn't bolted on — it's the architecture.
4. **Vendor-managed runtime.** SDK + hosted execution (Anthropic Managed Agents, OpenAI Agents SDK, Cloudflare Agents). You stop running infrastructure; you accept lock-in.

Most production "long-running agent" stories combine two: e.g. LangGraph + Postgres checkpointer (school 2), or Pydantic AI on Temporal (school 1+2), or Claude Agent SDK with file-as-memory in a Modal container (school 4 + 1).

---

## 3. Durable execution platforms

These provide the substrate. Persistence, replay, retries, scheduling — agent-agnostic primitives an agent layer sits on top of.

### Temporal
The reference workflow engine. Pure event sourcing — every step appends to a history; on crash, workers re-execute deterministic workflow code and short-circuit any step whose result is already recorded. **OpenAI Agents SDK integration GA March 23, 2026**: the `OpenAIAgentsPlugin` wraps each LLM call as a recorded activity, so replays don't re-spend tokens. Vercel AI SDK and Google ADK integrations also shipped early 2026. Polyglot (Go, Java, TS, Python, .NET, PHP, Ruby).

- **Self-host or Temporal Cloud.** Cloud: $100/mo Essentials → $200/mo Growth (1M actions included). Actions sliding $50/M → $25/M.
- **Gotchas:** strict workflow determinism is a real cognitive cost; ~50K event / ~50MB history limits force `continue_as_new` for long agent loops; activity payloads (LLM responses) live in history forever and inflate replay.

### Restate
Rust-based low-latency runtime, single binary or sidecar. **Virtual Objects** are the killer agent primitive: keyed by `session_id` / `user_id`, single-writer per key, with a transactional KV store — much cleaner mapping for chat threads / agent memory than Temporal's workflow-per-conversation. Official integrations with Vercel AI SDK (`durableCalls` middleware), OpenAI Agents SDK, Pydantic AI, Google ADK. SDKs in TS, Java/Kotlin, Python, Go, Rust.

- **Self-host (BSL) or Restate Cloud** (GA, free tier 50K actions/mo, no card).
- **Gotchas:** newer than Temporal, smaller community; single-writer Virtual Objects need sharding for high-throughput sessions; journal grows with conversation.

### DBOS
A *library*, not a service — turns your existing Postgres into a durable workflow engine. Decorator-based: `@DBOS.workflow`, `@DBOS.step`. On restart, the library queries Postgres for unfinished workflows and resumes from the last completed step. Native integrations with Pydantic AI, LlamaIndex, OpenAI Agents SDK. April 2026: cross-language interop, metadata-only mode, Databricks LakeBase partnership.

- **Self-host only by design.** DBOS Cloud exists but the value prop is that you *don't* need it.
- **Gotchas:** throughput ceiling = your Postgres' write throughput; not for high-fanout agent swarms; same workflow-determinism rules as Temporal.

### Convex
A whole reactive backend (DB + functions + scheduling) with a **Workflow component** for durable suspendable execution and an **Agent component** for thread/message/tool management. Reactive queries make streaming agent state to UI trivial — the client subscribes to a thread and rerenders as messages append. JS/TS only. **Pricing reset May 6, 2026**: free tier; Pro supports 300 concurrent deployments; egress reset to $0.12/GB.

- **Managed cloud or self-hosted single binary.**
- **Gotchas:** whole-stack commitment — adopting Convex *just* for agent durability is heavy; can't easily call native Python ML libs in-process.

### Inngest (with AgentKit)
Step-function durable workflows, with a TS-only multi-agent framework (AgentKit) on top. **`useAgent` React hook (Sept 2025)** is the differentiator — subscribes a browser to a running agent and resumes mid-stream after refresh; no other framework here has that frontend story. Hosted-only is the primary path; OSS dev server for local. Pricing per "step" (every tool call and LLM step counts).

### Hatchet
YC-backed Postgres-native workflow engine — "Temporal without the operational tax." MIT OSS, Cloud GA. SDKs in Python/TS/Go, all first-class. Agent-friendly but less prescriptive than AgentKit — you wire LLM call replay yourself by making each call a task. Smaller community than Temporal/Inngest.

### Trigger.dev v4 (GA Aug 2025)
TS-only managed background-jobs platform reframed around agents. **Waitpoint tokens** = first-class HITL primitive: pause indefinitely until completed via dashboard / webhook / timeout (zero compute while waiting). v4 added 100–300ms warm starts. OSS (Apache 2.0) but warm starts and autoscaling are Cloud-only.

**Honest take:** for a comparative test, the differentiated picks are **Temporal** (event-sourced reference), **Restate** (Virtual Objects / journal-based), **DBOS** (library-on-Postgres), **Convex** (reactive stack with components). Inngest/Hatchet/Trigger overlap heavily with one of those four.

---

## 4. Agent frameworks (open source)

These give you the *programming model* — graphs, crews, handoffs, memory. Most assume you'll bolt on persistence (or come with checkpointers built in).

### LangGraph (LangChain)
The de facto reference for "durable agents." First-class **checkpointer** abstraction: `InMemorySaver`, `SqliteSaver`, `PostgresSaver`, plus community Redis/DynamoDB/Couchbase/Aerospike. Three durability modes (`sync` / `async` / `exit`). Strongest HITL support: `interrupt()` + `Command(resume=...)`, time-travel to previous checkpoints, **scheduled cron jobs on LangGraph Platform**. Two memory layers: short-term per-thread state, long-term cross-thread "Store" (KV/vector). Python + TypeScript both first-class.

- **Self-host the OSS lib or LangGraph Platform** (managed deployment, scaling, Studio IDE, persistence APIs).
- **Gotchas:** state can balloon if you stash large payloads in graph state (externalize to Store/blob); re-entry semantics force you to mark non-deterministic ops as `task` or risk double execution; LangGraph Platform is opinionated.

### Letta (formerly MemGPT)
The only framework whose entire architecture is built around stateful agent identity. **Server-resident agents** — you run a Letta server (Postgres-backed), agents live there with persistent identity until deleted. Memory is the most opinionated in the field:

- **Memory blocks** (labeled, editable in-context units the LLM rewrites via tools)
- **Recall memory** (searchable conversation history)
- **Archival memory** (vector store, unbounded)
- **MemFS / context repositories (2026)** — memory projected into git-backed files manipulated by generic computer-use tools. Big architectural shift away from bespoke memory tools.

V1 agent loop (April 2026) drops MemGPT heartbeats for native model reasoning + explicit "continue / call tool / terminate" decisions per step. Python + TS clients. Self-host or Letta Cloud. **Memory is portable across model providers** — explicit "own your memory" pitch.

### Pydantic AI
Type-safe Pythonic agent framework. **Delegates durability to four officially supported engines: Temporal, DBOS, Prefect, Restate.** The team co-maintains these integrations and uses only public Pydantic AI APIs — they're reference implementations, not magic. Strongest correctness ergonomics (typed deps, typed outputs, structured tools). No built-in memory layer — you bring your own.

```python
from pydantic_ai.durable_exec.temporal import TemporalAgent
agent = Agent('openai:gpt-5.4', deps_type=MyDeps, output_type=Result)
durable = TemporalAgent(agent)  # safe inside a Temporal workflow
```

### Mastra
Modern TS-first framework from the Gatsby team. Hit 1.0 January 2026 (22k+ stars, 300k weekly npm). Unique three-tier memory: conversation history + working memory (structured user facts) + semantic recall, plus newer **observational memory** that spawns background agents to maintain a dense observation log *replacing* raw history as it grows — explicitly designed against context bloat. `suspend()`/`resume()` workflow API. Storage providers `@mastra/libsql`, `@mastra/pg`. TS only.

### CrewAI
Multi-agent orchestration (Crews + Flows). Massive user base. Flows added a `@persist` decorator that auto-checkpoints to SQLite, with pluggable `FlowPersistence`. Memory split into state (per-run) vs memory (across runs); types: short-term, long-term, entity, contextual. **Crew-level checkpointing is still thin** — long-running really means Flows. Python only.

### Microsoft Agent Framework (MAF)
**GA 1.0 April 2026**, merging AutoGen + Semantic Kernel. Workflows abstraction has built-in checkpointing, hydration, pause-and-resume. Process Framework (deterministic business-process orchestration with audit trails, HITL) Q2 2026 GA. **.NET and Python both first-class** — uniquely strong .NET story. Tightly integrated with Azure but multi-cloud.

> Note: AutoGen v0.7.x is maintenance mode; AG2 is a community fork. **For new work in 2026, use MAF.**

### LlamaIndex Workflows
Event-driven step-based execution in plain Python. Extracted into `workflows-py`. **DBOS integration (2026)** is the headline durability story — every step transition persists automatically. Pluggable durability otherwise (SQLite zero-dep mode). Less prescriptive than LangGraph; more freedom, more decisions.

### Agno (formerly Phidata)
Python framework bundling agent + DB + runtime + control plane (`AgentOS`). Three memory tiers (session, long-term user-learning, semantic via vector DBs). Built-in HITL flows (User Confirmation, User Input, External Tool Execution). Newer "OS" framing — rapid API churn.

### Smolagents (Hugging Face)
**No persistence by design.** Deliberately minimal (~1000 LOC), Python-only, focused on `CodeAgent` (writes Python as action format vs JSON tool calls). Excellent for prototyping or as a building block *inside* a durable orchestrator — wrong tool for week-long state on its own.

### Semantic Kernel
**Superseded by Microsoft Agent Framework for new agent work.** SK v1.x kept for security/critical bug fixes only. Only framework here with serious Java support — relevant if you have an existing SK codebase or need JVM.

**Honest take:** for a comparative test, the differentiated picks are **LangGraph** (reference standard, durable + HITL + cron), **Letta** (memory-first stateful identity), **Pydantic AI + Temporal/DBOS** (thin-agent-on-real-engine school), **Mastra** (TS-native + observational memory). CrewAI / AutoGen / SK / LlamaIndex Workflows / Agno / Smolagents are out of scope for this brief.

---

## 5. Vendor SDKs and managed runtimes

Vendor SDKs converge on a different default than open-source frameworks: **persistence and durability are first-class primitives** (sessions that resume, files that persist, containers that auto-recover) — sometimes with model-specific harness work behind them. The trade is concrete: less flexibility, more lock-in, but you stop reinventing the durability wheel.

### Claude Agent SDK + Managed Agents (Anthropic)
- **Open-source SDK (TS/Python).** Renamed from "Claude Code SDK" late 2025 to signal general agent runtime. Filesystem-first: reads `CLAUDE.md`, `.claude/skills/*/SKILL.md`, `.claude/commands/*.md`; persistence via files + git. Session continuity by ID (`resume=session_id`). Built-in tools (Read/Write/Edit/Bash/Glob/Grep/WebSearch/WebFetch/AskUserQuestion). Full MCP support. Hooks (PreToolUse, PostToolUse, Stop, etc.).
- **Managed Agents (launched April 8, 2026, beta).** Hosted agent execution: POST `/v1/agents` (system + tools + skills + MCP servers, max 50 tools / 64 skills / 20 MCP), then POST `/v1/sessions` with `environment_id` and credentials. Persistent isolated containers, built-in error recovery, agent spawning preview, automatic prompt refinement preview. **$0.08/runtime-hour + tokens.**
- **Anthropic's published harness research is the most coherent end-to-end story** — see [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents): initializer + coding agent split, `claude-progress.txt` + feature-list JSON, git as memory, sub-agents with fresh windows.

### OpenAI Agents SDK + Temporal
- Lightweight Python + TS framework: Agents, Handoffs, Guardrails, run loop, sessions, tracing dashboard. April 16, 2026 added "model-native harness" with resume bookkeeping and made `Runner` an abstract base so durable executors plug in.
- **Temporal integration GA March 2026** wraps every agent invocation as an Activity — crash recovery, automatic retry on rate-limit, no Activity declarations needed. This is the cleanest "thin agent + real engine" combo from a vendor.
- Built-in tracing PIIs prompts to OpenAI by default. Without Temporal you have no real durability.

### OpenAI Assistants API
**Deprecated. Sunset August 26, 2026.** Replacement is **Responses API + Conversations API** (no 30-day TTL). Skip for new builds.

### Google Vertex Agent Builder / ADK
ADK is the open-source Python framework; **Agent Engine** is the managed runtime; **Sessions + Memory Bank** are state. Pay-per-second of active runtime; idle = free. Memory Bank is GCP-only and proprietary; ADK itself is portable. Supports LangChain, LangGraph, LlamaIndex, AG2, custom.

### AWS Bedrock Agents + AgentCore
Two distinct things: **Bedrock Agents** (high-level: multi-agent collab, knowledge bases, action groups) and **Bedrock AgentCore** (lower-level primitives: Runtime, Gateway, Identity, Memory, Observability, Browser, Code Interpreter). Memory retained up to 365 days. **AgentCore Runtime hard ceiling: 8h per session** (`maxLifetime` 60–28800s) — anything longer needs your own resume-from-checkpoint logic. Framework-agnostic on top.

### Azure AI Foundry Agent Service
Threads/Runs/Messages on top of Microsoft Agent Framework. Threads stored in **your own Cosmos DB** (data portability). Up to 100K messages/thread but Microsoft's own docs warn of latency at scale and recommend new threads for long interactions — **a hint this isn't designed for week-long single sessions.** Python + .NET first-class.

### Cloudflare Agents (Project Think)
**The dark horse, and the most novel runtime model.** Each agent = one Durable Object with embedded SQLite. Project Think (announced April 13–17, 2026, in preview) added the durability primitives:

- `runFiber()` — register tasks in SQLite that survive Durable Object eviction
- `keepAlive()` — 30s alarm heartbeat preventing idle eviction
- `onFiberRecovered()` — hook fires after eviction so you re-`runFiber()`

Persistent sessions are **tree-structured** (forking, compaction, full-text search built in). Sub-agents get their own SQLite + typed RPC. JS/TS-only. **If your agents are I/O-bound and JS-friendly, this collapses an enormous amount of operational work into one primitive.**

### Anthropic "Background Tasks" / Computer Use
Not a separate product — a *pattern* using Claude Code's loop/cron, Dispatch, and Computer Use, hosted in a long-lived container (tmux/Modal/Docker+VNC). Real long-running deployment = combine these ingredients yourself, or fold into Managed Agents.

---

## 6. Memory libraries (cross-cutting)

Long-term memory is a separable concern. Most frameworks let you plug in one of these:

| Library | Model | Best for | Open source |
|---|---|---|---|
| **Letta** (in-built) | OS-inspired tiered (core in-context, recall = history, archival = vector). LLM decides what to page in via tool calls. | LLM-driven memory management; "own your memory" | Yes |
| **Mem0** | Managed cloud-first, three scopes (user/session/agent), hybrid vector + graph + KV | Fastest to production, weakest sovereignty | Partially |
| **Zep / Graphiti** | Temporal knowledge graph; tracks how facts change over time with full history | Multi-hop relational queries, temporal correctness | Partially |
| **LangMem SDK** | Models all four memory types (working, episodic, semantic, procedural). Procedural = self-editing system prompts. | LangGraph users; procedural memory experiments | Yes |
| **Cognee** | Knowledge graph + embeddings; sessionized memory tools for LangGraph | Open-source graph memory | Yes |

**Trade-off:** vector wins on semantic recall (Mem0), tiered wins on continuity (Letta), graph wins on multi-hop and temporal (Zep).

---

## 7. Test dimensions

To meaningfully differentiate frameworks, the test app must *force* each long-running capability to show up. Eight (+ two optional) concrete dimensions:

1. **Crash mid-tool-call.** Kill the process between an LLM decision and the tool's side effect. Did the tool execute zero or one times after recovery? *(Tests durability + idempotency.)*
2. **Multi-week wall-clock with sleeps.** Genuinely span ≥7 days with scheduled wake-ups (e.g., weekly RSS summary, alert on X). Measure infra cost/day and recovery from a 24h sleep across a deploy. *(Tests scheduled wake-ups, ambient triggers, idle-hour cost.)*
3. **Cross-window continuity.** Total context exceeds 5× the window — refactor a 200-feature codebase, or read 50 long PDFs and synthesize. Measure quality after compaction vs baseline. *(Tests compaction, note-taking, sub-agent delegation, file-as-memory.)*
4. **Memory recall over time.** Inject a fact in session 1; query for it in session 12 after 50K tokens of unrelated work. Update in session 7 — does session 12 return current or stale? *(Differentiates Mem0 vs Zep vs Letta.)*
5. **Goal drift under adversarial pressure.** Long task with off-goal sub-requests injected. Measure goal adherence over 100+ turns. *(Reproduces arXiv 2505.02709.)*
6. **Human approval gate spanning hours.** Agent proposes; humans approve 4 hours later. Verify state survives, no double-execution, context still coherent. *(Tests interrupt/resume across long pauses.)*
7. **Stale external state.** While agent sleeps, mutate the world it cares about (delete a file, merge a PR, change a row). Detect, refresh, or fail loudly? *(Tests JIT retrieval, optimistic concurrency.)*
8. **Replay from event log.** Replay a complete run against a different model (or prompt tweak) and diff. Reproduce a bug from a 3-day-old trace? *(Cleanest differentiator between Temporal/LangGraph and minimal frameworks.)*

Optional:

9. **Cost regression.** Run identical task with caching on/off; measure dollar delta.
10. **Procedural memory.** Does the agent measurably improve after 20 sessions of feedback, without prompt edits? Only LangMem and Letta make this first-class.

---

## 8. Honest recommendation: which to test

You can't fairly compare 15 frameworks. Pick a subset that spans **orthogonal philosophies**, not minor variants. Recommended four-way matrix:

| Approach | Representative | Why this slot |
|---|---|---|
| **Workflow engine + thin agent** | Pydantic AI on **Temporal** (Python) | The "let a real engine handle durability" school. Standard Anthropic against. |
| **Graph + checkpointer** | **LangGraph** + Postgres checkpointer | Reference standard for durable agents. Will be implicitly compared against everything else. |
| **Stateful-by-design agent server** | **Letta** | Only architecture where state isn't bolted on. Stress-tests the memory dimension. |
| **Vendor-managed harness** | **Claude Agent SDK** (file-based memory, Anthropic's harness pattern) | Most coherent end-to-end story for long-horizon coding/research; published research backing. |

**Reasoned omissions:**

- **Convex / Cloudflare Agents** — interesting but JS-only; if the test app is Python (more agent ecosystem maturity), they don't slot in cleanly. Add as a 5th if you commit to TS.
- **Mastra** — same reasoning; the right pick if going TS-first.
- **OpenAI Agents SDK + Temporal** — overlaps Pydantic AI + Temporal philosophically; pick one.
- **Anthropic Managed Agents** — interesting "skip the ops" tier, but at $0.08/runtime-hour for week-long runs across 4 implementations, the bill matters. Treat as a stretch goal.
- **CrewAI / AutoGen / Agno / Semantic Kernel / LlamaIndex Workflows / Smolagents** — out of scope for "long-running."
- **Vertex / Bedrock / Azure Foundry** — cloud-vendor lock-in plays first, SDKs second. Include only if the org is already deeply on that cloud.

---

## 9. Reference reading (most-load-bearing)

**Anthropic engineering blog** — the most concentrated cluster of long-running-agent thinking:
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Building effective agents](https://www.anthropic.com/research/building-effective-agents)
- [Long-running Claude for scientific computing](https://www.anthropic.com/research/long-running-Claude)

**12-Factor Agents** (Dex Horthy / HumanLayer) — [github.com/humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents). Most load-bearing principles for long-running: #3 own your context window, #6 launch/pause/resume APIs, #8 own your control flow, #9 compact errors, #11 trigger from anywhere, #12 stateless reducer.

**Simon Willison** — agent definition ("runs tools in a loop to achieve a goal"), "lethal trifecta" (private data + external comms + untrusted content), [Agentic Engineering Patterns](https://simonw.substack.com/p/agentic-engineering-patterns).

**Production case studies** — Replit Agent (manager + editor + verifier; code-as-tool DSL), Devin, Manus (parallel sub-task decomposition), Cursor Background Agents (worktrees + remote machines; reportedly 35% of Cursor's own merged PRs), GitHub Copilot agent mode (GA March 2026).

**Academic** — [Evaluating Goal Drift in LM Agents (2505.02709)](https://arxiv.org/abs/2505.02709) · [Get Experience from Practice: LLM Agents with Record & Replay (2505.17716)](https://arxiv.org/abs/2505.17716) · [MemGPT (2310.08560)](https://arxiv.org/abs/2310.08560) · [Context Rot — Chroma](https://research.trychroma.com/context-rot).
