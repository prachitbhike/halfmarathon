"""Claude Agent SDK implementation entrypoint.

CLI:
    python -m implementations.claude_sdk.run \
        --config task/fixtures/user.json \
        --state-dir implementations/claude_sdk/state \
        --start-from 2026-04-01T00:00:00Z \
        --until    2026-04-15T00:00:00Z \
        --speed    86400

The agent has no memory across ticks — its only state is the files in
`--state-dir`. This is the file-as-memory ("Ralph loop") pattern from
Anthropic's harness paper, in its purest form.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from task.clock import DEFAULT_FIXTURE_START, FixtureClock

# These are also written to a progress.md preamble so the agent has them.
from task.event_log import EventLog
from task.types import UserProfile

log = logging.getLogger("halfmarathon.claude_sdk")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "task" / "fixtures"
SKILL_PATH = Path(__file__).resolve().parent / "agent_skill.md"

# Default model can be overridden by env. Use Sonnet — the agent makes
# many calls per tick, and the harness paper itself uses Sonnet for the
# coding-agent role.
DEFAULT_MODEL = os.environ.get("HALFMARATHON_AGENT_MODEL", "claude-sonnet-4-6")

# Cap per-tick spend so a runaway loop can't burn budget.
PER_TICK_BUDGET_USD = float(os.environ.get("HALFMARATHON_PER_TICK_BUDGET_USD", "0.50"))
PER_TICK_MAX_TURNS = int(os.environ.get("HALFMARATHON_PER_TICK_MAX_TURNS", "60"))

APPROVAL_POLL_INTERVAL = timedelta(hours=1)


# ============== file-state helpers =====================================


def _seed_progress(state_dir: Path, profile: UserProfile, fixture_start: datetime) -> None:
    """Write the initial progress.md so the agent has its instructions
    + interest list on first wake."""
    p = state_dir / "progress.md"
    if p.exists():
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    interests = "\n".join(f"  - {i}" for i in profile.interests)
    p.write_text(
        f"# Release Radar — progress log\n\n"
        f"_Initialized at fixture-time {fixture_start.isoformat()}._\n\n"
        f"User profile (locked for this run):\n"
        f"- user_id: {profile.user_id}\n"
        f"- max_items_per_digest: {profile.max_items_per_digest}\n"
        f"- tone: {profile.tone}\n"
        f"- interests:\n{interests}\n\n"
        f"## Tick log\n\n"
        f"_(no ticks yet)_\n"
    )
    # Initialize the KB as an empty array so the agent never has to handle
    # a missing-file edge case.
    kb = state_dir / "knowledge_base.json"
    if not kb.exists():
        kb.write_text("[]\n")
    (state_dir / "digests").mkdir(parents=True, exist_ok=True)


def _write_inbox(state_dir: Path, now_ts: datetime, events: list[dict[str, Any]]) -> Path:
    """Drop new events into inbox.json for the agent to consume this tick."""
    inbox = state_dir / "inbox.json"
    inbox.write_text(json.dumps({"now": now_ts.isoformat(), "events": events}, indent=2))
    return inbox


def _published_week_ids(state_dir: Path) -> set[str]:
    digests = state_dir / "digests"
    if not digests.exists():
        return set()
    return {
        p.stem.removeprefix("published-")
        for p in digests.glob("published-week-*.md")
    }


# Fetch cursor — kept separate from the agent-owned progress.md so the harness
# doesn't have to edit a model-written file. Persisting this means a crash
# restart won't dump the entire fixture into inbox.json on the first tick.
def _load_fetch_cursor(state_dir: Path) -> datetime | None:
    p = state_dir / "fetch_cursor.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        ts = data.get("last_fetch_ts")
        return datetime.fromisoformat(ts) if ts else None
    except (json.JSONDecodeError, ValueError):
        return None


def _save_fetch_cursor(state_dir: Path, ts: datetime) -> None:
    p = state_dir / "fetch_cursor.json"
    p.write_text(json.dumps({"last_fetch_ts": ts.isoformat()}))


# ============== per-tick query =========================================


def _build_options(state_dir: Path) -> ClaudeAgentOptions:
    skill = SKILL_PATH.read_text()
    return ClaudeAgentOptions(
        # Append our workflow instructions to the Claude Code preset so we
        # keep the default tool guidance.
        system_prompt={"type": "preset", "preset": "claude_code", "append": skill},
        cwd=str(state_dir),
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob"],
        permission_mode="bypassPermissions",
        max_turns=PER_TICK_MAX_TURNS,
        max_budget_usd=PER_TICK_BUDGET_USD,
        model=DEFAULT_MODEL,
        # Each tick is a fresh agent — file-as-memory means no resume.
        continue_conversation=False,
    )


async def _run_one_tick(
    state_dir: Path,
    *,
    now_ts: datetime,
    events_log: EventLog,
    new_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """One agentic tick. Returns a small summary dict for telemetry."""

    _write_inbox(state_dir, now_ts, new_events)
    events_log.append(
        "fetch", {"new_events": len(new_events), "inbox": "inbox.json"}, ts=now_ts
    )

    prompt = (
        f"It is now {now_ts.isoformat()} (fixture time). "
        f"Read progress.md to recall where you left off, "
        f"then read inbox.json (it has {len(new_events)} new events) "
        f"and continue per the workflow you know. Update progress.md "
        f"and delete inbox.json before you finish."
    )

    options = _build_options(state_dir)

    n_assistant = 0
    n_tool_use = 0
    n_tool_result = 0
    final_cost: float | None = None
    final_turns: int | None = None
    error: str | None = None

    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            n_assistant += 1
            for block in msg.content:
                if isinstance(block, TextBlock):
                    # Don't log the full text — would balloon events.jsonl.
                    events_log.append(
                        "llm_call",
                        {"role": "assistant", "n_chars": len(block.text)},
                        ts=now_ts,
                    )
                elif isinstance(block, ToolUseBlock):
                    n_tool_use += 1
                    events_log.append(
                        "tool_call",
                        {"tool": block.name, "input_keys": list(block.input.keys())},
                        ts=now_ts,
                    )
                elif isinstance(block, ToolResultBlock):
                    n_tool_result += 1
        elif isinstance(msg, UserMessage) and msg.tool_use_result is not None:
            n_tool_result += 1
        elif isinstance(msg, SystemMessage):
            # init / progress notifications — keep noise out of events.jsonl
            pass
        elif isinstance(msg, ResultMessage):
            final_cost = msg.total_cost_usd
            final_turns = msg.num_turns
            if msg.is_error:
                error = ", ".join(msg.errors or []) or "unknown"

    events_log.append(
        "summary",
        {
            "now": now_ts.isoformat(),
            "assistant_messages": n_assistant,
            "tool_calls": n_tool_use,
            "tool_results": n_tool_result,
            "turns": final_turns,
            "tick_cost_usd": final_cost,
            "error": error,
        },
        ts=now_ts,
    )

    if error:
        log.warning("tick at %s ended with error: %s", now_ts.isoformat(), error)

    return {
        "tool_uses": n_tool_use,
        "turns": final_turns,
        "tick_cost_usd": final_cost,
        "error": error,
    }


# ============== outer loop ============================================


def _next_wake(now_ts: datetime, has_pending_draft: bool) -> datetime:
    nxt_day = (now_ts + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if has_pending_draft:
        nxt_poll = (now_ts + APPROVAL_POLL_INTERVAL).replace(microsecond=0)
        return min(nxt_day, nxt_poll)
    return nxt_day


def _has_unpublished_draft(state_dir: Path) -> bool:
    digests = state_dir / "digests"
    if not digests.exists():
        return False
    pubs = {p.stem.removeprefix("published-") for p in digests.glob("published-week-*.md")}
    drafts = {p.stem.removeprefix("draft-") for p in digests.glob("draft-week-*.md")}
    return bool(drafts - pubs)


async def run_loop(
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float,
    fixtures_dir: Path | None = None,
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    events_log = EventLog(state_dir / "events.jsonl")
    _seed_progress(state_dir, profile, fixture_start)

    fx = fixtures_dir or FIXTURES_DIR
    clock = FixtureClock.from_fixtures(
        timeline_path=fx / "timeline.json",
        sources_path=fx / "sources.json",
        fixture_start=fixture_start,
        speed=speed,
    )
    clock.start()

    events_log.append("wake", {"reason": "boot"}, ts=clock.now())

    last_fetch_ts: datetime | None = _load_fetch_cursor(state_dir)
    total_cost_usd = 0.0
    total_ticks = 0
    last_error: str | None = None

    while clock.now() < until:
        now_ts = clock.now()
        events_log.append("wake", {"now": now_ts.isoformat()}, ts=now_ts)

        new_events_objs = clock.fetch_events_until(now_ts, since=last_fetch_ts)
        new_events_dicts = [e.model_dump(mode="json") for e in new_events_objs]
        last_fetch_ts = now_ts
        _save_fetch_cursor(state_dir, last_fetch_ts)

        result = await _run_one_tick(
            state_dir,
            now_ts=now_ts,
            events_log=events_log,
            new_events=new_events_dicts,
        )
        total_ticks += 1
        if result.get("tick_cost_usd"):
            total_cost_usd += result["tick_cost_usd"]
        if result.get("error"):
            last_error = result["error"]

        # If a draft is awaiting approval, the harness reports "approval" in
        # events.jsonl when the file appears. We poll between daily wakes
        # at fixture-hour cadence; the agent picks up the approval on the
        # next tick and publishes.
        wake_at = _next_wake(now_ts, _has_unpublished_draft(state_dir))
        if wake_at >= until:
            break
        await clock.sleep_until(wake_at)

    published = sorted(_published_week_ids(state_dir))
    events_log.append(
        "wake",
        {"reason": "exit", "published": published, "ticks": total_ticks,
         "cost_usd": total_cost_usd, "last_error": last_error},
        ts=clock.now(),
    )
    return {
        "published_weeks": published,
        "ticks": total_ticks,
        "estimated_cost_usd": round(total_cost_usd, 4),
        "last_error": last_error,
    }


# ============== CLI ====================================================


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _load_profile(path: Path) -> UserProfile:
    return UserProfile.model_validate(json.loads(path.read_text()))


def main() -> None:
    p = argparse.ArgumentParser(description="Claude Agent SDK Release Radar")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--start-from", type=_parse_iso, default=DEFAULT_FIXTURE_START)
    p.add_argument(
        "--until",
        type=_parse_iso,
        default=DEFAULT_FIXTURE_START + timedelta(days=15),
    )
    p.add_argument("--speed", type=float, default=86400.0)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s :: %(message)s",
    )
    profile = _load_profile(args.config)

    summary = asyncio.run(
        run_loop(
            profile=profile,
            state_dir=args.state_dir,
            fixture_start=args.start_from,
            until=args.until,
            speed=args.speed,
        )
    )
    print("\n=== run summary ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
