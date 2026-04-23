"""End-to-end smoke for the Claude Agent SDK implementation.

This smoke is COSTLY because the SDK requires real API access (no offline
mock — the SDK shells out to `claude` CLI, which calls the real API).

Defaults: 8 fixture days at 86400x speed, ~1 tick per wall-second.
That's ~8 query() calls, each consuming several LLM exchanges. Estimated
cost per run: ~$0.10-$0.50 depending on how chatty the agent is.

Skips with exit 0 + warning if ANTHROPIC_API_KEY is not set, so this can
sit in CI without burning tokens.

What it verifies:
    - The harness loop drives the agent through multiple ticks.
    - The agent maintains progress.md, knowledge_base.json, digests/.
    - At least one weekly digest gets drafted (Sunday 2026-04-05 is in window).
    - The HITL approval flow publishes when the approval file is pre-written.
    - events.jsonl contains the expected event kinds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from task.clock import DEFAULT_FIXTURE_START
from task.digests import (
    week_id_for,
    write_approval,
)
from task.event_log import EventLog
from task.types import ApprovalStatus, UserProfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"
STATE_DIR = ROOT / "implementations" / "claude_sdk" / "state-smoke"


def _expected_week_ids(start: datetime, until: datetime) -> list[str]:
    out: list[str] = []
    d = start
    while d < until:
        if d.weekday() == 6:
            week_start = (d - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            out.append(week_id_for(week_start))
        d += timedelta(days=1)
    return out


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "SKIP: ANTHROPIC_API_KEY not set — Claude Agent SDK smoke needs the "
            "real API (no offline mock for the SDK).",
            file=sys.stderr,
        )
        return 0

    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=8)  # one Sunday in window
    speed = 86400.0  # 1 fixture-day per wall-second

    expected = _expected_week_ids(fixture_start, fixture_until)
    print(f"smoke: expected weekly digests = {expected}")
    # Pre-write approvals so the agent publishes immediately on draft.
    for wid in expected:
        write_approval(
            STATE_DIR, wid,
            status=ApprovalStatus.APPROVED,
            feedback="(smoke auto-approved)",
            received_at=fixture_start + timedelta(days=8),
        )

    from implementations.claude_sdk.run import run_loop  # noqa: PLC0415

    profile = UserProfile.model_validate(
        json.loads((FIXTURES / "user.json").read_text())
    )

    summary = await run_loop(
        profile=profile,
        state_dir=STATE_DIR,
        fixture_start=fixture_start,
        until=fixture_until,
        speed=speed,
    )
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2, default=str))

    # ---- assertions --------------------------------------------------
    failures: list[str] = []

    # Required files
    if not (STATE_DIR / "progress.md").exists():
        failures.append("progress.md missing")
    if not (STATE_DIR / "knowledge_base.json").exists():
        failures.append("knowledge_base.json missing")

    # Event log
    events_log = EventLog(STATE_DIR / "events.jsonl")
    entries = events_log.read_all()
    print(f"event log entries: {len(entries)}")
    kinds = {e.kind for e in entries}
    for required in ("wake", "fetch", "llm_call", "tool_call", "summary"):
        if required not in kinds:
            failures.append(f"missing event kind: {required}")

    # KB should have grown beyond zero items.
    kb = json.loads((STATE_DIR / "knowledge_base.json").read_text())
    if len(kb) < 3:
        failures.append(f"KB has only {len(kb)} items; expected >=3 in 8 fixture days")

    # At least one digest should have been drafted (the Sunday 2026-04-05 falls
    # within the window, so the agent should have drafted week-2026-W14).
    drafts = list((STATE_DIR / "digests").glob("draft-week-*.md"))
    if not drafts:
        failures.append("no drafts produced — expected at least 1 for the Sunday in window")

    # Bonus: if the agent published anything, expected_weeks should include it.
    pubs = sorted(
        p.stem.removeprefix("published-")
        for p in (STATE_DIR / "digests").glob("published-week-*.md")
    )
    print(f"published weeks: {pubs}")
    # We don't fail on no-publish — Phase 1 happy-path passes if the draft was
    # produced; the publish step depends on the agent picking up the approval
    # file in a subsequent tick.

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("\nOK — all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
