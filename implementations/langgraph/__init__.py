"""LangGraph implementation of Release Radar.

Architecture:
    - StateGraph compiled with AsyncSqliteSaver — checkpointed across crashes.
    - Single thread per agent run; resumes from last checkpoint after restart.
    - HITL via interrupt() in the await_approval node; outer loop polls the
      approval file and resumes via Command(resume=...).
    - No external scheduler — outer asyncio loop drives wake cadence using
      the fixture clock.

What this exercises from LangGraph:
    - Checkpointer (SqliteSaver / AsyncSqliteSaver) for crash safety (R6).
    - interrupt() + Command(resume) for the HITL gate (R4).
    - Conditional edges for routing.
    - State persistence between graph invocations (the KB grows turn by turn).
"""
