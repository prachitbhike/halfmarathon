# halfmarathon

A hands-on comparison of approaches to building **long-running AI agents** — agents that persist state across days/weeks, not single chat sessions.

One canonical task ("Release Radar"), four implementations spanning orthogonal philosophies, one shared evaluation harness.

## What this is

Most "agent framework comparison" content is a feature checklist or a vibes-based survey. This repo is the opposite: the **same task, run for 8 hours of compressed time, against four real implementations**, scored on 8 long-running-specific dimensions, with the cost ledger and findings published.

Read first:
- [research.md](research.md) — landscape survey of frameworks, vendor SDKs, and patterns for long-running agents
- [plan.md](plan.md) — what we're building and why

## The four implementations

| Approach | Stack | What it tests |
|---|---|---|
| Workflow engine + thin agent | Pydantic AI + Temporal | Event-sourced replay, polyglot durability |
| Graph + checkpointer | LangGraph + Postgres | First-class HITL `interrupt()`, time travel, cron |
| Stateful-by-design agent server | Letta (self-hosted) | Memory blocks, recall/archival, server-resident identity |
| Vendor-managed harness | Claude Agent SDK + file-based memory | Anthropic's harness pattern (`progress.md` + git + sub-agents) |

All four use Anthropic Claude (Opus 4.7 + Sonnet 4.6) so model quality is held constant.

## The task: Release Radar

A weekly research agent that:

1. Tracks 5–10 GitHub repos / RSS feeds / blogs.
2. Wakes on a schedule to fetch new items.
3. Maintains an evolving knowledge base of "what's interesting."
4. Drafts a weekly digest, gated on human approval.
5. Learns from approval/rejection feedback over time.

Spec: [task/spec.md](task/spec.md). Fixtures: [task/fixtures/](task/fixtures/).

## The 8 evaluation dimensions

1. **Crash recovery** — kill mid-tool-call; verify exactly-once after restart
2. **Multi-day with sleeps** — 8h compressed run replaying 2 weeks of events
3. **Cross-window continuity** — knowledge base past 5× context window
4. **Memory recall over time** — plant fact in week 1, query in week 4
5. **Goal drift** — adversarial off-topic items injected
6. **HITL gate spanning hours** — approval held 4h
7. **Stale external state** — sources mutated during sleep
8. **Replay from event log** — replay against modified prompt, diff outputs

Plus optional **(9) cost regression** and **(10) procedural memory**.

Score per dimension: pass / partial / fail / N/A with a one-paragraph note. Output is a markdown matrix, not a leaderboard — different frameworks win on different axes.

## Running it (Phase 0 — foundations only)

```bash
# requires Python 3.12+
make install        # uv sync (when pyproject lands in Phase 0)
make fixtures       # regenerate frozen source timeline (optional)
make clock-test     # verify the fixture clock works end-to-end
```

Implementations land in Phase 1+. See [plan.md §7 Phasing](plan.md).

## Layout

```
halfmarathon/
  research.md
  plan.md
  task/                    # canonical task spec, fixtures, shared types, fixture clock
    spec.md
    types.py
    clock.py
    fixtures/
  implementations/         # one subdir per approach (added in Phase 1+)
    langgraph/
    temporal-pydantic/
    letta/
    claude-sdk/
  eval/                    # dimension tests, scoring, report generator (Phase 2+)
    dimensions/
    harness.py
    report.py
  infra/e2b/               # sandbox templates per impl
    base/
    langgraph/
    temporal-pydantic/
    letta/
    claude-sdk/
  results/                 # eval output (gitignored except summary)
```

## Status

**Phase 0** — foundations. Task spec + fixtures + shared types + fixture clock + e2b sandbox skeletons. No implementations yet.

## License

[MIT](LICENSE).
