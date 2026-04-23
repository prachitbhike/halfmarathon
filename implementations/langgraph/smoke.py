"""End-to-end smoke for the LangGraph implementation.

What this verifies:
    - The graph wires correctly (no API errors, no schema mismatches).
    - Daily fetches and weekly drafts happen on the expected fixture days.
    - The HITL gate works: pre-writing approval files lets the agent publish
      both expected weekly digests within the run window.
    - events.jsonl is non-empty and contains the right kinds.
    - SqliteSaver persists state across the run.

How:
    Runs against the full 2026-04-01 → 2026-04-15 window at 86400x speed
    (~15 wall seconds + LLM time). Uses HALFMARATHON_OFFLINE_LLM=1 by
    default to avoid burning tokens during dev. Run with the env unset to
    exercise the real Anthropic API.

Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Default to offline mock so the smoke is free.
os.environ.setdefault("HALFMARATHON_OFFLINE_LLM", "1")

from task.clock import DEFAULT_FIXTURE_START
from task.digests import (
    approval_path,
    draft_path,
    published_path,
    week_id_for,
    write_approval,
)
from task.event_log import EventLog
from task.types import ApprovalStatus, UserProfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"
STATE_DIR = ROOT / "implementations" / "langgraph" / "state-smoke"


def _expected_week_ids(start: datetime, until: datetime) -> list[str]:
    """ISO week ids for each Monday in [start, until) — the draft trigger."""
    out: list[str] = []
    d = start
    while d < until:
        if d.weekday() == 0:
            week_start = (d - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            out.append(week_id_for(week_start))
        d += timedelta(days=1)
    return out


async def main() -> int:
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0  # 1 fixture-day per 1 wall-second

    # Pre-populate approval files for both expected weeks so the HITL path
    # publishes immediately when the digest is drafted. This exercises R3+R4
    # without forcing the smoke to wait wall-clock hours.
    expected = _expected_week_ids(fixture_start, fixture_until)
    print(f"smoke: expected weekly digests = {expected}")
    for wid in expected:
        write_approval(
            STATE_DIR, wid,
            status=ApprovalStatus.APPROVED,
            feedback="(smoke auto-approved)",
            received_at=fixture_start + timedelta(days=8),
        )

    # Import here so env vars are set first.
    from implementations.langgraph.run import run_loop  # noqa: PLC0415

    profile = UserProfile.model_validate(
        json.loads((FIXTURES / "user.json").read_text())
    )

    summary = await run_loop(
        profile=profile,
        state_dir=STATE_DIR,
        fixture_start=fixture_start,
        until=fixture_until,
        speed=speed,
        thread_id="smoke",
    )
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2, default=str))

    # ---- assertions --------------------------------------------------
    failures: list[str] = []

    if not (STATE_DIR / "checkpoints.sqlite").exists():
        failures.append("checkpoints.sqlite was not created")

    events_log = EventLog(STATE_DIR / "events.jsonl")
    entries = events_log.read_all()
    print(f"event log entries: {len(entries)}")
    kinds = {e.kind for e in entries}
    for required in ("wake", "fetch", "llm_call", "summary", "digest_draft", "publish", "approval"):
        if required not in kinds:
            failures.append(f"missing event kind: {required}")

    for wid in expected:
        if not draft_path(STATE_DIR, wid).exists():
            failures.append(f"missing draft: {wid}")
        if not approval_path(STATE_DIR, wid).exists():
            failures.append(f"missing approval (we wrote it): {wid}")
        if not published_path(STATE_DIR, wid).exists():
            failures.append(f"missing published digest: {wid}")

    if set(summary["published_weeks"]) != set(expected):
        failures.append(
            f"published_weeks mismatch: got {summary['published_weeks']}, expected {expected}"
        )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("\nOK — all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
