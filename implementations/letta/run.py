"""Letta implementation entrypoint.

CLI:
    python -m implementations.letta.run \
        --config task/fixtures/user.json \
        --state-dir implementations/letta/state \
        --start-from 2026-04-01T00:00:00Z \
        --until    2026-04-15T00:00:00Z \
        --speed    86400 \
        --letta-base-url http://localhost:8283

The Letta agent persists on the server. Re-running with the same `--agent-name`
re-uses the existing agent (so memory_blocks + recall accumulate across
restarts — the same kind of resume-by-identity LangGraph gets via thread_id).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from letta_client import Letta

from task.clock import DEFAULT_FIXTURE_START, FixtureClock
from task.digests import (
    items_from_kb,
    read_approval,
    render_digest_md,
    week_id_for,
    write_draft,
)
from task.digests import (
    publish as publish_digest,
)
from task.event_log import EventLog
from task.types import (
    Approval,
    ApprovalStatus,
    Digest,
    DigestItem,
    DigestStatus,
    KnowledgeBaseItem,
    Source,
    SourceEvent,
    UserProfile,
)

log = logging.getLogger("halfmarathon.letta")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "task" / "fixtures"

# Default to the standard self-host URL; user can override.
DEFAULT_LETTA_URL = os.environ.get("LETTA_BASE_URL", "http://localhost:8283")
APPROVAL_POLL_INTERVAL = timedelta(hours=1)
LETTA_MODEL = os.environ.get("LETTA_MODEL", "anthropic/claude-sonnet-4-6")
LETTA_EMBED = os.environ.get("LETTA_EMBED", "openai/text-embedding-3-small")


# ============== state files (mirror other impls) =======================


def _kb_path(state_dir: Path) -> Path:
    return state_dir / "knowledge_base.json"


def _load_kb(state_dir: Path) -> list[dict]:
    p = _kb_path(state_dir)
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _save_kb(state_dir: Path, items: list[dict]) -> None:
    _kb_path(state_dir).write_text(json.dumps(items, indent=2, default=str))


def _meta_path(state_dir: Path) -> Path:
    return state_dir / "letta_meta.json"


def _load_meta(state_dir: Path) -> dict[str, Any]:
    p = _meta_path(state_dir)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _save_meta(state_dir: Path, meta: dict[str, Any]) -> None:
    _meta_path(state_dir).write_text(json.dumps(meta, indent=2, default=str))


# ============== Letta server helpers ===================================


def _check_letta_reachable(base_url: str) -> bool:
    """True iff ``base_url`` answers a known Letta API route with 2xx/4xx.

    We hit ``/v1/agents/`` (the list endpoint) — 200 on a live server,
    404 only if the route genuinely doesn't exist. A generic HTTP server
    listening on the same port would typically 404 the ``/v1`` prefix or
    return HTML, so we additionally look at the ``content-type`` header
    to avoid false positives from unrelated services.
    """
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/v1/agents/", timeout=3.0)
    except httpx.HTTPError:
        return False
    if r.status_code >= 500:
        return False
    ctype = r.headers.get("content-type", "").lower()
    return "application/json" in ctype


def _get_or_create_agent(client: Letta, *, name: str, profile: UserProfile) -> str:
    """Look up an agent by name (resume) or create a new one."""
    existing = client.agents.list(name=name, limit=1)
    if existing:
        return existing[0].id

    interests = "\n".join(f"- {i}" for i in profile.interests)
    user_block = (
        f"User id: {profile.user_id}\n"
        f"Tone: {profile.tone}\n"
        f"Max items per digest: {profile.max_items_per_digest}\n"
        f"Interests:\n{interests}\n"
    )
    persona_block = (
        "You are a research-radar agent. Each turn you receive a JSON list "
        "of new items from blogs and GitHub release feeds. For each, decide "
        "if it is relevant to the user's interests, and if so write a short "
        "summary. Reply with a JSON array of objects:\n"
        '  [{"event_id": "...", "relevance_score": 0.0, "summary": "..."}]\n'
        "where relevance_score is in [0, 1]. Use score >= 0.3 for items "
        "worth keeping. Be conservative: most items are not relevant. "
        "ONLY reply with a JSON array — no prose, no preamble, no markdown "
        "fences."
    )
    procedural_block = "(no feedback yet)"
    agent = client.agents.create(
        name=name,
        model=LETTA_MODEL,
        embedding=LETTA_EMBED,
        memory_blocks=[
            {"label": "human", "value": user_block, "limit": 4000},
            {"label": "persona", "value": persona_block, "limit": 4000},
            {"label": "procedural_notes", "value": procedural_block, "limit": 4000},
        ],
        include_base_tools=True,
        message_buffer_autoclear=False,
    )
    return agent.id


def _read_procedural_block(client: Letta, agent_id: str) -> str | None:
    """Return the current value of the agent's procedural_notes memory block.

    Used after feedback messages to verify the agent actually mutated its
    own memory. Returns None on API failure so callers can tell the
    difference between "unchanged" and "could not read".
    """
    try:
        block = client.agents.blocks.retrieve(
            agent_id=agent_id, block_label="procedural_notes",
        )
    except Exception as exc:
        log.warning("could not read procedural_notes block: %s", exc)
        return None
    return getattr(block, "value", None)


# ============== response parsing =======================================


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Forgiving JSON-array extractor — Letta agents sometimes wrap or prose."""
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[: -3]
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _agent_reply_text(response: Any) -> str:
    """Pull the assistant's textual content out of a Letta response.

    We pass ``use_assistant_message=True`` on every create() call, so the
    server collapses ``send_message`` tool calls back into
    ``assistant_message`` entries. We also handle the raw tool-call form
    (``message_type == "tool_call_message"``, ``tool_call.name ==
    "send_message"``) so callers that don't set that flag still get their
    text — and so the fallback is easy to debug if Letta changes.
    """
    parts: list[str] = []
    for msg in getattr(response, "messages", []) or []:
        msg_type = getattr(msg, "message_type", "") or getattr(msg, "type", "")
        if msg_type == "assistant_message":
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif hasattr(c, "text"):
                        parts.append(c.text)
        elif msg_type == "tool_call_message":
            tc = getattr(msg, "tool_call", None)
            if tc is None or getattr(tc, "name", None) != "send_message":
                continue
            raw_args = getattr(tc, "arguments", None)
            if not raw_args:
                continue
            try:
                payload = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and "message" in payload:
                parts.append(str(payload["message"]))
    return "\n".join(parts).strip()


# ============== outer loop =============================================


def _next_wake(now_ts: datetime, has_pending: bool) -> datetime:
    nxt_day = (now_ts + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if has_pending:
        nxt_poll = (now_ts + APPROVAL_POLL_INTERVAL).replace(microsecond=0)
        return min(nxt_day, nxt_poll)
    return nxt_day


def _maybe_draft(
    *,
    state_dir: Path,
    now_ts: datetime,
    kb: list[dict],
    profile: UserProfile,
    sources: list[Source],
    published_weeks: list[str],
) -> dict | None:
    """If due, draft a weekly digest. Returns pending dict or None."""
    if now_ts.weekday() != 0:  # Monday-only draft trigger (ISO week alignment)
        return None
    week_start = (now_ts - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = now_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    wid = week_id_for(week_start)
    if wid in published_weeks:
        return None
    kb_items = [KnowledgeBaseItem.model_validate(d) for d in kb]
    items = items_from_kb(
        kb_items,
        source_name_by_id={s.id: s.name for s in sources},
        week_start=week_start,
        week_end=week_end,
        max_items=profile.max_items_per_digest,
    )
    if not items:
        return None
    body = render_digest_md(
        digest_id=wid, week_start=week_start, week_end=week_end, items=items,
    )
    digest = Digest(
        id=wid, week_start=week_start, week_end=week_end,
        items=items, body_md=body, status=DigestStatus.DRAFT,
        drafted_at=now_ts,
    )
    write_draft(state_dir, digest)
    return {
        "week_id": wid,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "body_md": body,
        "items": [it.model_dump(mode="json") for it in items],
    }


def _try_publish(
    *,
    state_dir: Path,
    pending: dict,
    now_ts: datetime,
    events_log: EventLog,
) -> tuple[bool, Approval | None]:
    """If approval present, publish (or record rejection). Returns (resolved, appr)."""
    appr = read_approval(state_dir, pending["week_id"])
    if appr is None:
        return False, None
    if appr.status != ApprovalStatus.APPROVED:
        events_log.append(
            "approval", {"week_id": pending["week_id"], "status": "rejected"}, ts=now_ts,
        )
        return True, appr
    items = [DigestItem.model_validate(d) for d in pending["items"]]
    digest = Digest(
        id=pending["week_id"],
        week_start=datetime.fromisoformat(pending["week_start"]),
        week_end=datetime.fromisoformat(pending["week_end"]),
        items=items,
        body_md=pending["body_md"],
        status=DigestStatus.APPROVED,
        drafted_at=now_ts, approved_at=appr.received_at,
    )
    publish_digest(state_dir, digest, appr)
    events_log.append("publish", {"week_id": pending["week_id"]}, ts=now_ts)
    events_log.append(
        "approval", {"week_id": pending["week_id"], "status": "approved"}, ts=now_ts,
    )
    return True, appr


async def run_loop(  # noqa: PLR0915, PLR0912
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float,
    letta_base_url: str = DEFAULT_LETTA_URL,
    agent_name: str = "release-radar",
    fixtures_dir: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not _check_letta_reachable(letta_base_url):
        raise RuntimeError(
            f"Letta server not reachable at {letta_base_url}. "
            "Set LETTA_BASE_URL or run a self-hosted server."
        )

    state_dir.mkdir(parents=True, exist_ok=True)
    events_log = EventLog(state_dir / "events.jsonl")
    fx = fixtures_dir or FIXTURES_DIR
    sources = [
        Source.model_validate(s)
        for s in json.loads((fx / "sources.json").read_text())
    ]

    clock = FixtureClock.from_fixtures(
        timeline_path=fx / "timeline.json",
        sources_path=fx / "sources.json",
        fixture_start=fixture_start,
        speed=speed,
    )
    clock.start()

    client = Letta(base_url=letta_base_url)
    agent_id = _get_or_create_agent(client, name=agent_name, profile=profile)
    events_log.append(
        "wake", {"reason": "boot", "letta_agent_id": agent_id}, ts=clock.now(),
    )

    meta = _load_meta(state_dir)
    last_fetch_iso: str | None = meta.get("last_fetch_iso")
    pending: dict | None = meta.get("pending")
    published_weeks: list[str] = meta.get("published_weeks", [])
    procedural_notes: list[str] = meta.get("procedural_notes", [])
    kb = _load_kb(state_dir)

    while clock.now() < until:
        now_ts = clock.now()
        events_log.append("wake", {"now": now_ts.isoformat()}, ts=now_ts)

        # 1. fetch new events
        since = datetime.fromisoformat(last_fetch_iso) if last_fetch_iso else None
        new_events: list[SourceEvent] = clock.fetch_events_until(now_ts, since=since)
        events_log.append("fetch", {"new_events": len(new_events)}, ts=now_ts)

        # 2. ask the Letta agent to score + summarize the new events
        if new_events:
            kb_existing = {it["event_id"] for it in kb}
            unseen = [e for e in new_events if e.id not in kb_existing]
            if unseen:
                prompt_payload = [
                    {
                        "event_id": e.id,
                        "title": e.title,
                        "source_id": e.source_id,
                        "body": e.body_md[:600],
                    }
                    for e in unseen
                ]
                msg = (
                    f"Today is {now_ts.isoformat()} (fixture time).\n"
                    f"New items to review (JSON):\n"
                    f"```json\n{json.dumps(prompt_payload, indent=2)}\n```\n"
                    "Reply with a JSON array of "
                    '{event_id, relevance_score, summary} for each item. '
                    "Skip items with score < 0.3 (i.e., omit them from the array)."
                )
                resp = client.agents.messages.create(
                    agent_id=agent_id,
                    messages=[{"role": "user", "content": msg}],
                    use_assistant_message=True,
                )
                events_log.append(
                    "llm_call", {"purpose": "score+summarize", "n": len(unseen)},
                    ts=now_ts,
                )
                rows = _extract_json_array(_agent_reply_text(resp))
                for row in rows:
                    eid = row.get("event_id")
                    score = float(row.get("relevance_score", 0))
                    summary = (row.get("summary") or "").strip()
                    if not eid or score < 0.3 or not summary:
                        continue
                    evt = next((e for e in unseen if e.id == eid), None)
                    if evt is None:
                        continue
                    kbi = KnowledgeBaseItem(
                        event_id=evt.id, source_id=evt.source_id,
                        fixture_timestamp=evt.fixture_timestamp,
                        title=evt.title, url=evt.url, summary=summary,
                        relevance_score=score,
                    )
                    kb.append(kbi.model_dump(mode="json"))
                    events_log.append(
                        "summary", {"event_id": evt.id, "score": score}, ts=now_ts,
                    )
                _save_kb(state_dir, kb)

        last_fetch_iso = now_ts.isoformat()

        # 3. maybe draft
        if pending is None:
            pending = _maybe_draft(
                state_dir=state_dir, now_ts=now_ts, kb=kb,
                profile=profile, sources=sources,
                published_weeks=published_weeks,
            )
            if pending is not None:
                events_log.append(
                    "digest_draft",
                    {"week_id": pending["week_id"], "items": len(pending["items"])},
                    ts=now_ts,
                )

        # 4. resolve any pending approval
        if pending is not None:
            resolved, appr = _try_publish(
                state_dir=state_dir, pending=pending, now_ts=now_ts,
                events_log=events_log,
            )
            if resolved and appr is not None:
                verdict = (
                    "approved" if appr.status == ApprovalStatus.APPROVED
                    else "rejected"
                )
                feedback_summary = (
                    f"Digest {pending['week_id']} {verdict}"
                    + (f" with feedback: {appr.feedback}" if appr.feedback else "")
                )
                if appr.status == ApprovalStatus.APPROVED:
                    published_weeks.append(pending["week_id"])
                procedural_notes.append(feedback_summary)
                # Drive the agent's stateful memory update, then read the
                # procedural_notes block back so we can tell whether the
                # agent actually mutated its memory. The resulting block
                # value (or a "(unchanged)" marker) is recorded in
                # procedural_notes so the eval can see what the agent's
                # identity now holds — this is what "stateful-by-design"
                # means to test.
                before_value = _read_procedural_block(client, agent_id)
                try:
                    client.agents.messages.create(
                        agent_id=agent_id,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"User feedback on the latest digest: {feedback_summary}. "
                                "Update your procedural_notes memory with what to keep "
                                "doing or change next time. Use the "
                                "core_memory_replace tool on the "
                                "procedural_notes block."
                            ),
                        }],
                        use_assistant_message=True,
                    )
                except Exception as exc:
                    log.warning("feedback message to Letta failed: %s", exc)
                after_value = _read_procedural_block(client, agent_id)
                if after_value is not None and after_value != before_value:
                    procedural_notes.append(
                        f"[agent-memory:{pending['week_id']}] {after_value}"
                    )
                else:
                    procedural_notes.append(
                        f"[agent-memory:{pending['week_id']}] (unchanged)"
                    )
                pending = None

        # persist meta + kb
        _save_meta(state_dir, {
            "last_fetch_iso": last_fetch_iso,
            "pending": pending,
            "published_weeks": published_weeks,
            "procedural_notes": procedural_notes,
            "agent_id": agent_id,
        })

        wake_at = _next_wake(now_ts, pending is not None)
        if wake_at >= until:
            break
        await clock.sleep_until(wake_at)

    events_log.append(
        "wake",
        {"reason": "exit", "published": published_weeks},
        ts=clock.now(),
    )
    return {
        "published_weeks": list(published_weeks),
        "kb_size": len(kb),
        "procedural_notes": list(procedural_notes),
        "letta_agent_id": agent_id,
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
    p = argparse.ArgumentParser(description="Letta Release Radar")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--start-from", type=_parse_iso, default=DEFAULT_FIXTURE_START)
    p.add_argument(
        "--until",
        type=_parse_iso,
        default=DEFAULT_FIXTURE_START + timedelta(days=15),
    )
    p.add_argument("--speed", type=float, default=86400.0)
    p.add_argument("--letta-base-url", default=DEFAULT_LETTA_URL)
    p.add_argument("--agent-name", default="release-radar")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s :: %(message)s",
    )
    profile = _load_profile(args.config)

    summary = asyncio.run(
        run_loop(
            profile=profile, state_dir=args.state_dir,
            fixture_start=args.start_from, until=args.until,
            speed=args.speed, letta_base_url=args.letta_base_url,
            agent_name=args.agent_name,
        )
    )
    print("\n=== run summary ===")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
