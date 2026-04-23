"""Claude Agent SDK implementation of Release Radar.

Architecture (Anthropic harness paper pattern):
    - File-as-memory: agent state lives in `progress.md`, `knowledge_base.json`,
      and the digests/ folder inside `state_dir`. No process-level memory; each
      tick is a fresh `query()` invocation that re-hydrates from files.
    - Tools: built-in Read / Write / Edit / Bash / Glob (no MCP, no custom tools).
    - Workflow instructions are appended to the Claude Code preset system prompt.
    - The harness fetches new fixture events between ticks and drops them into
      `inbox.json`; the agent consumes inbox.json each tick.
    - HITL: same shared file convention as the other impls. The harness writes
      `digests/draft-<week>.approval.json`; the agent picks it up next tick.

What this exercises:
    - The "Ralph loop" / file-based memory pattern from Anthropic's harness paper
    - The default Claude Code tool set under realistic agent guidance
    - End-to-end persistence with NO external orchestration framework
    - Real Anthropic API calls (no offline mock — the SDK requires the API)
"""
