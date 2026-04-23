"""LangGraph implementation entrypoint.

CLI:
    python -m implementations.langgraph.run \
        --config task/fixtures/user.json \
        --state-dir implementations/langgraph/state \
        --start-from 2026-04-01T00:00:00Z \
        --until    2026-04-15T00:00:00Z \
        --speed    86400        # smoke: 1 fixture-day per wall-second
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from task.clock import DEFAULT_FIXTURE_START, FixtureClock
from task.digests import (
    items_from_kb,
    publish,
    read_approval,
    render_digest_md,
    week_id_for,
    write_draft,
)
from task.event_log import EventLog
from task.llm import Llm, total_cost
from task.types import (
    Approval,
    ApprovalStatus,
    Digest,
    DigestItem,
    DigestStatus,
    KnowledgeBaseItem,
    Source,
    UserProfile,
)

log = logging.getLogger("halfmarathon.langgraph")

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "task" / "fixtures"

# Once a digest is drafted, the outer loop polls for approval at this fixture
# cadence (1 fixture-hour ~= 41s wall at default speed; very cheap).
APPROVAL_POLL_INTERVAL = timedelta(hours=1)


# ============== state ====================================================


class PendingDigest(TypedDict):
    week_id: str
    week_start: str  # iso datetime
    week_end: str
    body_md: str
    items: list[dict[str, Any]]  # DigestItem.model_dump() entries


class State(TypedDict, total=False):
    last_fetch_ts: str | None                   # iso datetime, upper bound of last fetch
    kb: list[dict[str, Any]]                    # KnowledgeBaseItem.model_dump() entries
    pending: PendingDigest | None               # digest awaiting approval
    approval_in_hand: dict[str, Any] | None     # transient: set by await_approval node
    published_weeks: list[str]
    procedural_notes: list[str]                 # R5: lessons from past approvals


def _empty_state() -> State:
    return {
        "last_fetch_ts": None,
        "kb": [],
        "pending": None,
        "approval_in_hand": None,
        "published_weeks": [],
        "procedural_notes": [],
    }


# ============== graph ====================================================


@dataclass
class Deps:
    """Bundle of side-effecting collaborators handed to nodes via closure.

    Not part of graph state — these are not serialized into checkpoints.
    """

    clock: FixtureClock
    llm: Llm
    profile: UserProfile
    state_dir: Path
    events: EventLog
    source_name_by_id: dict[str, str]


def build_graph(  # noqa: PLR0915
    deps: Deps, saver: AsyncSqliteSaver,
) -> CompiledStateGraph[State, Any, State, State]:
    """Construct and compile the agent graph."""

    # ---- nodes ----------------------------------------------------------

    async def fetch_and_score(state: State) -> dict[str, Any]:
        now_ts = deps.clock.now()
        last = state.get("last_fetch_ts")
        since = datetime.fromisoformat(last) if last else None
        new_events = deps.clock.fetch_events_until(now_ts, since=since)
        deps.events.append("fetch", {"new_events": len(new_events)}, ts=now_ts)
        if not new_events:
            return {"last_fetch_ts": now_ts.isoformat()}

        scored = deps.llm.score_relevance(events=new_events, profile=deps.profile, ts=now_ts)
        deps.events.append("llm_call", {"purpose": "relevance", "n": len(new_events)}, ts=now_ts)

        score_by_id = {row.get("event_id"): float(row.get("relevance_score", 0)) for row in scored}
        kb_existing_ids = {it["event_id"] for it in state.get("kb", [])}
        new_items: list[dict[str, Any]] = []
        for evt in new_events:
            if evt.id in kb_existing_ids:
                continue
            score = score_by_id.get(evt.id, 0.0)
            # Threshold deliberately permissive: drift testing requires that
            # off-topic items still land in KB at low score, so the digest
            # filter (which sorts by score) does the actual selection.
            if score < 0.3:
                continue
            summary = deps.llm.summarize_for_digest(
                event=evt, profile=deps.profile, ts=now_ts
            )
            deps.events.append(
                "llm_call", {"purpose": "summarize", "event_id": evt.id}, ts=now_ts
            )
            kbi = KnowledgeBaseItem(
                event_id=evt.id,
                source_id=evt.source_id,
                fixture_timestamp=evt.fixture_timestamp,
                title=evt.title,
                url=evt.url,
                summary=summary,
                relevance_score=score,
            )
            new_items.append(kbi.model_dump(mode="json"))
            deps.events.append(
                "summary", {"event_id": evt.id, "score": score}, ts=now_ts
            )

        return {
            "last_fetch_ts": now_ts.isoformat(),
            "kb": [*state.get("kb", []), *new_items],
        }

    async def maybe_draft(state: State) -> dict[str, Any]:
        """Draft a digest covering the past 7 fixture-days when one is due."""
        if state.get("pending"):
            return {}
        now_ts = deps.clock.now()
        # Draft on Monday morning — the ISO week that just closed (Mon..Sun)
        # is now fully observable, so week_id_for(now_ts - 7d) is well-defined
        # and week_start..week_end lines up with one complete ISO week.
        if now_ts.weekday() != 0:  # Monday=0
            return {}

        week_start = (now_ts - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_end = now_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        wid = week_id_for(week_start)
        if wid in state.get("published_weeks", []):
            return {}

        kb_items = [KnowledgeBaseItem.model_validate(d) for d in state.get("kb", [])]
        items = items_from_kb(
            kb_items,
            source_name_by_id=deps.source_name_by_id,
            week_start=week_start,
            week_end=week_end,
            max_items=deps.profile.max_items_per_digest,
        )
        if not items:
            return {}

        body = render_digest_md(
            digest_id=wid, week_start=week_start, week_end=week_end, items=items,
        )
        digest = Digest(
            id=wid,
            week_start=week_start,
            week_end=week_end,
            items=items,
            body_md=body,
            status=DigestStatus.DRAFT,
            drafted_at=now_ts,
        )
        write_draft(deps.state_dir, digest)
        deps.events.append("digest_draft", {"week_id": wid, "items": len(items)}, ts=now_ts)

        pending: PendingDigest = {
            "week_id": wid,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "body_md": body,
            "items": [it.model_dump(mode="json") for it in items],
        }
        return {"pending": pending}

    async def await_approval(state: State) -> dict[str, Any]:
        """Pause the graph until the approval file appears.

        Uses LangGraph's interrupt(). The outer loop polls and resumes via
        Command(resume=appr_dict). On resume, this node re-runs from top —
        the file is now present so we short-circuit without interrupt().
        """
        pending = state.get("pending")
        assert pending is not None, "await_approval reached without pending"
        wid = pending["week_id"]
        existing = read_approval(deps.state_dir, wid)
        if existing is not None:
            deps.events.append(
                "approval",
                {"week_id": wid, "status": str(existing.status), "via": "short_circuit"},
                ts=deps.clock.now(),
            )
            return {"approval_in_hand": existing.model_dump(mode="json")}
        deps.events.append("approval_wait", {"week_id": wid}, ts=deps.clock.now())
        resumed = interrupt({"week_id": wid})
        # `resumed` is the dict passed in Command(resume=value)
        return {"approval_in_hand": resumed}

    async def publish_node(state: State) -> dict[str, Any]:
        pending = state["pending"]
        assert pending is not None
        appr_dict = state.get("approval_in_hand") or {}
        appr = Approval.model_validate(appr_dict) if appr_dict else None

        wid = pending["week_id"]
        items = [DigestItem.model_validate(d) for d in pending["items"]]
        week_start = datetime.fromisoformat(pending["week_start"])
        week_end = datetime.fromisoformat(pending["week_end"])

        if appr is None or appr.status != ApprovalStatus.APPROVED:
            note = f"[{wid}] rejected: {appr.feedback if appr else 'unknown'}"
            deps.events.append(
                "approval", {"week_id": wid, "status": "rejected"}, ts=deps.clock.now()
            )
            return {
                "pending": None,
                "approval_in_hand": None,
                "procedural_notes": [*state.get("procedural_notes", []), note],
            }

        digest = Digest(
            id=wid,
            week_start=week_start,
            week_end=week_end,
            items=items,
            body_md=pending["body_md"],
            status=DigestStatus.APPROVED,
            drafted_at=deps.clock.now(),
            approved_at=appr.received_at,
        )
        publish(deps.state_dir, digest, appr)
        deps.events.append("publish", {"week_id": wid}, ts=deps.clock.now())

        feedback_note = f"[{wid}] approved" + (
            f" with feedback: {appr.feedback}" if appr.feedback else ""
        )
        return {
            "pending": None,
            "approval_in_hand": None,
            "published_weeks": [*state.get("published_weeks", []), wid],
            "procedural_notes": [*state.get("procedural_notes", []), feedback_note],
        }

    # ---- routing -------------------------------------------------------

    def route_after_draft(state: State) -> str:
        return "await" if state.get("pending") else "end"

    # ---- assembly ------------------------------------------------------

    g: StateGraph[State, Any, State, State] = StateGraph(State)
    g.add_node("fetch_and_score", fetch_and_score)
    g.add_node("maybe_draft", maybe_draft)
    g.add_node("await_approval", await_approval)
    g.add_node("publish", publish_node)

    g.add_edge(START, "fetch_and_score")
    g.add_edge("fetch_and_score", "maybe_draft")
    g.add_conditional_edges(
        "maybe_draft", route_after_draft, {"await": "await_approval", "end": END}
    )
    g.add_edge("await_approval", "publish")
    g.add_edge("publish", END)

    return g.compile(checkpointer=saver)


# ============== outer loop ==============================================


def _next_wake(now_ts: datetime, has_pending: bool) -> datetime:
    """Daily fetch at midnight; while pending, also wake hourly to poll."""
    nxt_day = (now_ts + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if has_pending:
        nxt_poll = (now_ts + APPROVAL_POLL_INTERVAL).replace(microsecond=0)
        return min(nxt_day, nxt_poll)
    return nxt_day


def _extract_pending_interrupt_week_ids(snap_tasks: Any) -> list[str]:
    """Pull week_id values from any pending interrupts on a snapshot."""
    out: list[str] = []
    for task in snap_tasks or []:
        for itr in getattr(task, "interrupts", None) or []:
            value = getattr(itr, "value", None)
            if isinstance(value, dict) and "week_id" in value:
                out.append(value["week_id"])
    return out


async def run_loop(
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float,
    thread_id: str,
    fixtures_dir: Path | None = None,
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = state_dir / "checkpoints.sqlite"
    events_log = EventLog(state_dir / "events.jsonl")

    fx = fixtures_dir or FIXTURES_DIR
    clock = FixtureClock.from_fixtures(
        timeline_path=fx / "timeline.json",
        sources_path=fx / "sources.json",
        fixture_start=fixture_start,
        speed=speed,
    )
    clock.start()
    llm = Llm()

    sources = [
        Source.model_validate(s)
        for s in json.loads((fx / "sources.json").read_text())
    ]
    deps = Deps(
        clock=clock, llm=llm, profile=profile, state_dir=state_dir,
        events=events_log,
        source_name_by_id={s.id: s.name for s in sources},
    )
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

    async with AsyncSqliteSaver.from_conn_string(str(sqlite_path)) as saver:
        graph = build_graph(deps, saver)

        snap = await graph.aget_state(config)
        if snap.values:
            log.info("resumed from checkpoint; published=%s", snap.values.get("published_weeks"))
            # On resume, let LangGraph continue from the checkpoint (None input)
            # rather than starting a fresh run from START.
            next_input: State | Command[Any] | None = None
        else:
            next_input = _empty_state()
        events_log.append("wake", {"reason": "boot", "thread_id": thread_id}, ts=clock.now())

        while clock.now() < until:
            now_ts = clock.now()
            events_log.append("wake", {"now": now_ts.isoformat()}, ts=now_ts)

            await graph.ainvoke(next_input, config)
            # Subsequent ticks: trigger a fresh run from START; checkpoint
            # state (kb, pending, etc.) is already loaded by the saver.
            next_input = State()

            # If we paused on an interrupt, poll for approval and resume.
            snap = await graph.aget_state(config)
            pending_wids = _extract_pending_interrupt_week_ids(snap.tasks)
            while pending_wids and clock.now() < until:
                wid = pending_wids[0]
                appr = read_approval(state_dir, wid)
                if appr is not None:
                    events_log.append(
                        "approval", {"week_id": wid, "status": str(appr.status)},
                        ts=clock.now(),
                    )
                    await graph.ainvoke(
                        Command(resume=appr.model_dump(mode="json")), config
                    )
                    snap = await graph.aget_state(config)
                    pending_wids = _extract_pending_interrupt_week_ids(snap.tasks)
                else:
                    await clock.sleep_until(clock.now() + APPROVAL_POLL_INTERVAL)
                    snap = await graph.aget_state(config)
                    pending_wids = _extract_pending_interrupt_week_ids(snap.tasks)

            if clock.now() >= until:
                break

            cur: dict[str, Any] = snap.values or {}
            wake_at = _next_wake(clock.now(), bool(cur.get("pending")))
            if wake_at >= until:
                break
            await clock.sleep_until(wake_at)

        snap = await graph.aget_state(config)
        final: dict[str, Any] = snap.values or {}
        cost = total_cost(llm.ledger)
        events_log.append(
            "wake",
            {"reason": "exit", "published": final.get("published_weeks", []), "cost_usd": cost},
            ts=clock.now(),
        )
        return {
            "published_weeks": final.get("published_weeks", []),
            "kb_size": len(final.get("kb", [])),
            "procedural_notes": final.get("procedural_notes", []),
            "estimated_cost_usd": cost,
            "llm_calls": len(llm.ledger),
        }


# ============== CLI ======================================================


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
    p = argparse.ArgumentParser(description="LangGraph Release Radar")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--start-from", type=_parse_iso, default=DEFAULT_FIXTURE_START)
    p.add_argument(
        "--until",
        type=_parse_iso,
        default=DEFAULT_FIXTURE_START + timedelta(days=15),
    )
    p.add_argument("--speed", type=float, default=86400.0)
    p.add_argument(
        "--thread-id",
        default="release-radar-default",
        help="Resume by reusing the same thread_id; new id starts fresh",
    )
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
            thread_id=args.thread_id,
        )
    )
    print("\n=== run summary ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
