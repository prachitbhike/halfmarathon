"""End-to-end smoke for the Pydantic AI + Temporal implementation.

Defaults to offline LLM mock so the smoke is free. Uses
WorkflowEnvironment.start_local() under the hood, which requires the
`temporal` CLI binary on $PATH.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("HALFMARATHON_OFFLINE_LLM", "1")

from task.clock import DEFAULT_FIXTURE_START
from task.digests import (
    draft_path,
    published_path,
    week_id_for,
    write_approval,
)
from task.event_log import EventLog
from task.types import ApprovalStatus, UserProfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"
STATE_DIR = ROOT / "implementations" / "temporal_pydantic" / "state-smoke"


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
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    fixture_start = DEFAULT_FIXTURE_START
    fixture_until = DEFAULT_FIXTURE_START + timedelta(days=15)
    speed = 86400.0
    expected = _expected_week_ids(fixture_start, fixture_until)
    print(f"smoke: expected weekly digests = {expected}")
    for wid in expected:
        write_approval(
            STATE_DIR, wid,
            status=ApprovalStatus.APPROVED,
            feedback="(smoke auto-approved)",
            received_at=fixture_start + timedelta(days=8),
        )

    from implementations.temporal_pydantic.run import run_loop  # noqa: PLC0415
    profile = UserProfile.model_validate(
        json.loads((FIXTURES / "user.json").read_text())
    )
    summary = await run_loop(
        profile=profile, state_dir=STATE_DIR,
        fixture_start=fixture_start, until=fixture_until,
        speed=speed, thread_id="smoke",
    )
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2, default=str))

    failures: list[str] = []
    events_log = EventLog(STATE_DIR / "events.jsonl")
    entries = events_log.read_all()
    print(f"event log entries: {len(entries)}")
    kinds = {e.kind for e in entries}
    for required in (
        "wake", "fetch", "llm_call", "summary", "digest_draft", "publish", "approval",
    ):
        if required not in kinds:
            failures.append(f"missing event kind: {required}")
    for wid in expected:
        if not draft_path(STATE_DIR, wid).exists():
            failures.append(f"missing draft: {wid}")
        if not published_path(STATE_DIR, wid).exists():
            failures.append(f"missing published digest: {wid}")
    if set(summary.get("published_weeks", [])) != set(expected):
        failures.append(
            f"published_weeks mismatch: got {summary.get('published_weeks')}, "
            f"expected {expected}"
        )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nOK - all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
