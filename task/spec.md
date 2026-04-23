# Task Spec: Release Radar

> Canonical task all four implementations must satisfy. Same spec, same fixtures, same evaluation.

## One-line description

A long-running agent that watches a configured set of sources (RSS feeds + GitHub repos), maintains an evolving knowledge base of "what's interesting," and once per fixture-week drafts a digest gated on human approval. Learns from approval feedback over time.

## Scope of "long-running"

The agent runs continuously across fixture-time. In the compressed test, 2 fixture-weeks elapse during ~8 wall-clock hours (42× speed). The agent must:

- Survive process restarts mid-run without losing state or duplicating side effects
- Sleep between scheduled wake-ups without burning compute or API tokens
- Detect external state changes that happened during sleep (deleted/edited items)
- Retain coherent memory across the full fixture-fortnight

## Inputs

### Sources

A frozen list of sources in [`task/fixtures/sources.json`](fixtures/sources.json). Each source has:

```json
{
  "id": "anthropic-blog",
  "type": "rss",
  "url": "https://www.anthropic.com/news/rss.xml",
  "name": "Anthropic Blog",
  "topics": ["llm", "anthropic", "research"]
}
```

`type` is `rss` or `github_releases`. `topics` are advisory tags the agent can use for relevance scoring.

### Event timeline

A frozen 2-week timeline of events in [`task/fixtures/timeline.json`](fixtures/timeline.json). Each event:

```json
{
  "id": "evt_0001",
  "source_id": "anthropic-blog",
  "fixture_timestamp": "2026-04-01T09:32:00Z",
  "kind": "post",
  "title": "...",
  "url": "...",
  "body_md": "...",
  "metadata": {"author": "...", "tags": ["..."]}
}
```

The agent fetches events through `task.clock.fetch_events(since: datetime, until: datetime)`. The fixture clock determines when each event becomes "visible" — the agent cannot peek at events whose `fixture_timestamp` is in the future.

### User profile (per-run config)

```json
{
  "user_id": "demo",
  "interests": [
    "long-running AI agents",
    "agent frameworks (LangGraph, Pydantic AI, Claude Agent SDK)",
    "durable execution (Temporal, DBOS, Restate)",
    "agent memory systems"
  ],
  "tone": "concise, technical, no marketing language",
  "max_items_per_digest": 8
}
```

Located at `task/fixtures/user.json`. Implementations may copy this into their own state on first run.

## Behaviour requirements

Every implementation must:

### R1. Schedule

- Wake **daily** (in fixture time) to fetch new events from each source.
- Wake **weekly** (in fixture time) to draft a digest covering the past 7 fixture-days.
- Sleep between wakes — no busy polling.

### R2. Process events

For each new event since the last wake:
- Decide if it's relevant to the user's interests.
- If yes, summarize and add to the running knowledge base with attribution (source, URL, timestamp).
- Idempotent: re-processing the same event must not create duplicates.

### R3. Draft a weekly digest

- Aggregate the past week's relevant items into a single Markdown digest.
- ≤ `max_items_per_digest` items, ranked by relevance.
- Each item: title, source, one-paragraph summary, link.
- Save the draft to `state/digests/draft-<week>.md`.

### R4. Human-in-the-loop approval

- After saving a draft, the agent **must wait** for an explicit approval signal before publishing.
- Approval arrives via the harness writing to `state/digests/draft-<week>.approval.json`:
  ```json
  {"status": "approved" | "rejected", "feedback": "...", "edits": "..." (optional)}
  ```
- The wait can be hours of wall-clock (4h is exercised in dim 6). The agent must not crash, not spin compute, and must resume cleanly when the file appears.
- On approval: copy/edit the draft to `state/digests/published-<week>.md` and proceed.
- On rejection: incorporate feedback into the next digest's drafting; the rejected draft is not published.

### R5. Procedural learning

- Approval/rejection feedback over time should measurably shift what the agent considers "relevant" without manual prompt edits.
- Each implementation can store this in whatever form is natural (LangGraph Store, files, etc.).

### R6. Crash safety

- At any point, the process can be killed and restarted. The agent must resume without:
  - Re-publishing an already-published digest
  - Losing items it had already filed in the knowledge base
  - Forgetting an in-flight approval wait

### R7. Replay

- Every implementation must write a structured event log to `state/events.jsonl` (one JSON object per line). Schema:
  ```json
  {"ts": "...", "kind": "wake|fetch|llm_call|tool_call|summary|digest_draft|approval|publish|error", "payload": {...}}
  ```
- The replay-eval (dim 8) re-runs the agent from this log.

## What implementations MAY differ on

- How they store state (Postgres, SQLite, files, server-managed)
- How they implement scheduling (Temporal timers, LangGraph cron, OS cron, asyncio sleep)
- How they implement memory (graph state + Store, memory blocks, files, etc.)
- How they implement the HITL wait (interrupt + resume, polling, signals, file watch)
- How they call the LLM (any tool-calling pattern is fine; recommended Anthropic SDK)

## What they MAY NOT differ on

- Source list, fixture timeline, user profile (all from `task/fixtures/`)
- Output paths and filenames (`state/digests/draft-<week>.md`, `state/events.jsonl`, etc.)
- Required fixture clock — all timers, sleeps, and wakes go through `task.clock`
- Model — Claude only (Opus 4.7 / Sonnet 4.6)
- The 8 evaluation dimensions

## Required interface

Each implementation exposes a single `run.py` with a CLI:

```bash
python -m implementations.<impl>.run --config task/fixtures/user.json \
                                      --state-dir implementations/<impl>/state \
                                      --start-from 2026-04-01T00:00:00Z \
                                      --until    2026-04-15T00:00:00Z
```

Plus a hook for the eval harness to send approval/rejection signals (in practice: writing the approval file).

## Success criteria

The implementation is "complete" when it can:

1. Run end-to-end against the fixtures and produce 2 published weekly digests.
2. Be killed at any point and restarted to completion.
3. Be replayed from `state/events.jsonl` and produce the same digests (or a documented diff).
4. Be evaluated by the harness across all 8 dimensions.

## Open spec questions (track as TODOs)

- **Tone of summaries** — leave to model + user-profile `tone`, or enforce a structural template?
  - Initial answer: leave to model. Add a structural template later if findings show drift across impls.
- **What counts as "duplicate"** — same `event.id`, same URL, or fuzzy?
  - Initial answer: same `event.id`. Sources rarely re-emit the same id.
- **Multi-source rate limits** — fixtures fetch is local, so N/A for now. Real implementation would need backoff.
