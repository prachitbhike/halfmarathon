"""Pydantic AI + Temporal implementation of Release Radar.

Architecture:
    - Temporal workflow as the durable state machine: schedules wakes via
      workflow.sleep(), persists state via event-sourced replay automatically.
    - Activities for all side effects: fetch_events, score, summarize, draft,
      poll_approval, publish, write_event. Activities are the unit of
      durability — Temporal records inputs and outputs and re-runs deterministic
      workflow code over the recorded history on resume.
    - LLM calls use Pydantic AI's typed `Agent` for structured outputs, falling
      back to task/llm.py's offline mock when HALFMARATHON_OFFLINE_LLM=1.
    - HITL via the spec's file-based approval convention: a poll_approval
      activity reads digests/draft-<week>.approval.json from the state-dir.

What this exercises that LangGraph doesn't:
    - Event-sourced replay (vs. snapshot checkpointing)
    - Polyglot durability via a real workflow engine
    - Worker process lifecycle decoupled from the workflow
    - Pydantic-typed LLM outputs (the `Agent` typed-output pattern)
"""
