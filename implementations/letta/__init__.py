"""Letta implementation of Release Radar.

Architecture:
    - One Letta agent created at run-start with memory blocks for the user
      profile and a procedural-notes block. The agent has persistent identity
      on the Letta server — survives the harness process restart.
    - Per fixture-tick, the harness sends new events to the agent as a single
      message asking for relevance scores + brief summaries. The agent's
      response is parsed (JSON) and the harness writes KB items.
    - HITL, drafting, and publish flow live in the harness using the same
      file-based contract as the other impls.
    - Procedural learning: after every approval/rejection, the harness sends
      a "feedback" message to the agent so its conversation memory captures
      what was accepted/rejected and (per Letta's pitch) the agent can
      recall this in future scoring rounds.

Why this shape and not "let the Letta agent do everything":
    - Custom tools that touch the host filesystem need a self-hosted Letta
      server with very specific configuration. For an apples-to-apples
      cross-impl comparison, we keep the file I/O in the harness so the
      output paths and event log format are identical.
    - This still exercises Letta's distinguishing features: server-resident
      stateful identity, memory blocks, conversation recall across ticks.

Server requirement:
    LETTA_BASE_URL must point at a reachable Letta server. Defaults to
    http://localhost:8283 (the standard self-host port). Smoke skips if no
    server is reachable.
"""
