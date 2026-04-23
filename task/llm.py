"""Anthropic client wrapper with prompt caching defaults.

Why this is in task/ and not per-impl:
    All four implementations are required to use the same model and the same
    cache discipline (see realism principle in plan.md). Pulling this into
    one helper means the cost-regression dimension actually compares
    architectures, not bespoke caching strategies.

What it provides:
    - `Llm` class with `complete()` (one-shot) and `score_relevance()` and
      `summarize()` helpers covering the calls the agent loop makes.
    - Automatic `cache_control` markers on the system prompt + tool defs
      (1-hour beta TTL, since long-running agents wake outside the 5-min TTL).
    - Token + cost accounting per call into a `LedgerEntry`, surfaced for
      the per-impl ledger and the dim 9 cost-regression eval.
    - Offline mock mode (set `HALFMARATHON_OFFLINE_LLM=1`) for CI / dev where
      no API key is configured. Returns deterministic stub answers so the
      agent loop, file layout, and event log are still exercised end-to-end.

Model selection:
    Sonnet 4.6 for relevance scoring (cheap, fast, ~OK at classification).
    Opus 4.7 for digest synthesis (the one place we want quality).
    Override via env vars (HALFMARATHON_MODEL_FAST, HALFMARATHON_MODEL_SMART).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from anthropic import Anthropic
from anthropic.types import (
    CacheControlEphemeralParam,
    TextBlock,
    TextBlockParam,
)

from task.types import SourceEvent, UserProfile

# Defaults the research recommends.
MODEL_FAST = os.environ.get("HALFMARATHON_MODEL_FAST", "claude-sonnet-4-6")
MODEL_SMART = os.environ.get("HALFMARATHON_MODEL_SMART", "claude-opus-4-7")
OFFLINE = os.environ.get("HALFMARATHON_OFFLINE_LLM") == "1"

# Pricing (per 1M tokens). Update if Anthropic changes prices.
# These are used for the ledger only — the API bill is the source of truth.
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":       {"in": 3.00,  "in_cached": 0.30, "in_write": 3.75, "out": 15.00},
    "claude-opus-4-7":         {"in": 15.00, "in_cached": 1.50, "in_write": 18.75, "out": 75.00},
    "claude-haiku-4-5-20251001": {"in": 0.80, "in_cached": 0.08, "in_write": 1.00, "out": 4.00},
}


@dataclass
class LedgerEntry:
    ts: datetime
    purpose: str  # "relevance" | "summarize" | "digest" | etc.
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class Llm:
    """Tiny wrapper. Holds the client + an in-memory cost ledger."""

    api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    fast_model: str = MODEL_FAST
    smart_model: str = MODEL_SMART
    ledger: list[LedgerEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not OFFLINE and not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY missing. Set the env var or "
                "set HALFMARATHON_OFFLINE_LLM=1 for the deterministic mock."
            )
        self._client: Anthropic | None = None if OFFLINE else Anthropic(api_key=self.api_key)

    # ---------- core call ------------------------------------------------

    def complete(
        self,
        *,
        purpose: str,
        system: str,
        user_message: str,
        ts: datetime,
        smart: bool = False,
        max_tokens: int = 1024,
        cache_system: bool = True,
    ) -> str:
        model = self.smart_model if smart else self.fast_model
        if OFFLINE or self._client is None:
            return self._offline_complete(purpose=purpose, user_message=user_message)

        sys_blocks: list[TextBlockParam] = [{"type": "text", "text": system}]
        if cache_system:
            sys_blocks[0]["cache_control"] = CacheControlEphemeralParam(
                type="ephemeral", ttl="1h",
            )
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=sys_blocks,
            messages=[{"role": "user", "content": user_message}],
            extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
        )
        text = "".join(b.text for b in resp.content if isinstance(b, TextBlock))

        usage = resp.usage
        self._record(
            ts=ts,
            purpose=purpose,
            model=model,
            input_tokens=usage.input_tokens,
            cached_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            output_tokens=usage.output_tokens,
        )
        return text

    def _record(self, **kwargs: Any) -> None:
        cost = _estimate_cost(
            kwargs["model"],
            kwargs["input_tokens"],
            kwargs["cached_input_tokens"],
            kwargs["cache_creation_tokens"],
            kwargs["output_tokens"],
        )
        self.ledger.append(LedgerEntry(estimated_cost_usd=cost, **kwargs))

    # ---------- task-shaped helpers -------------------------------------

    def score_relevance(
        self,
        *,
        events: list[SourceEvent],
        profile: UserProfile,
        ts: datetime,
    ) -> list[dict[str, Any]]:
        """Score a batch of events for relevance. Returns one dict per event,
        in input order: {event_id, relevance_score (0-1), one_line_reason}.
        Uses the FAST model.
        """
        if not events:
            return []
        system = _RELEVANCE_SYSTEM
        user_msg = _format_relevance_user(events, profile)
        raw = self.complete(
            purpose="relevance",
            system=system,
            user_message=user_msg,
            ts=ts,
            smart=False,
            max_tokens=1024,
        )
        parsed = _parse_relevance_json(raw, fallback_event_ids=[e.id for e in events])
        return parsed

    def summarize_for_digest(
        self,
        *,
        event: SourceEvent,
        profile: UserProfile,
        ts: datetime,
    ) -> str:
        """Produce a 1-paragraph summary of one event suitable for the digest."""
        system = _SUMMARY_SYSTEM
        user_msg = _format_summary_user(event, profile)
        return self.complete(
            purpose="summarize",
            system=system,
            user_message=user_msg,
            ts=ts,
            smart=False,
            max_tokens=400,
        ).strip()

    # ---------- offline mock --------------------------------------------

    def _offline_complete(self, *, purpose: str, user_message: str) -> str:
        """Deterministic stub responses keyed by purpose. Crude but enough to
        exercise the loop without hitting the API."""
        if purpose == "relevance":
            # Naive: any event whose body mentions any of the user's interest
            # keywords gets 0.8, everything else 0.2. Look at the user_message
            # which contains the events serialized.
            try:
                payload = json.loads(_extract_json_block(user_message))
            except Exception:
                return "[]"
            scores: list[dict[str, Any]] = []
            interests = [s.lower() for s in payload.get("interests", [])]
            for evt in payload.get("events", []):
                blob = (evt.get("title", "") + " " + evt.get("body", "")).lower()
                hit = any(any(tok in blob for tok in interest.split()) for interest in interests)
                scores.append({
                    "event_id": evt.get("id"),
                    "relevance_score": 0.8 if hit else 0.2,
                    "one_line_reason": "keyword match" if hit else "no keyword match",
                })
            return json.dumps(scores)
        if purpose == "summarize":
            return "(offline mock summary) " + user_message[:160]
        if purpose == "digest":
            return "(offline mock digest body)"
        return "(offline mock response)"


# ============== prompt scaffolding =====================================

_RELEVANCE_SYSTEM = """\
You are a relevance scorer for a research-radar agent.

You receive a list of recent items from blogs and GitHub release feeds, plus
a user's stated interests. For each item, output a relevance score in [0, 1]
and a one-line reason.

Be honest: most items are not relevant to most users. Score conservatively.
0.0 = irrelevant; 0.5 = tangential; 0.8 = directly on-topic; 1.0 = exact match.

Reply with a JSON array, one object per input event, in the same order:
  [{"event_id": "...", "relevance_score": 0.0, "one_line_reason": "..."}, ...]

Reply ONLY with valid JSON. No prose, no markdown fences.
"""

_SUMMARY_SYSTEM = """\
You write tight, technical one-paragraph summaries of items for a personal
research digest. The user's tone preference is provided. Keep it to 2-3
sentences. Lead with the substantive change or claim. No marketing language.
No fluff. Output the summary text only — no markdown headings, no preamble.
"""


def _format_relevance_user(events: list[SourceEvent], profile: UserProfile) -> str:
    payload = {
        "interests": profile.interests,
        "events": [
            {
                "id": e.id,
                "source_id": e.source_id,
                "kind": e.kind,
                "title": e.title,
                "body": e.body_md[:600],
            }
            for e in events
        ],
    }
    return "```json\n" + json.dumps(payload, indent=2) + "\n```"


def _format_summary_user(event: SourceEvent, profile: UserProfile) -> str:
    return (
        f"User tone preference: {profile.tone}\n\n"
        f"Item title: {event.title}\n"
        f"Source: {event.source_id}\n"
        f"URL: {event.url}\n\n"
        f"Body:\n{event.body_md}\n"
    )


def _extract_json_block(s: str) -> str:
    """Find the first ```json ... ``` block, else return the whole string."""
    start = s.find("```json")
    if start == -1:
        return s
    start = s.find("\n", start) + 1
    end = s.find("```", start)
    return s[start:end] if end != -1 else s[start:]


def _parse_relevance_json(
    raw: str,
    *,
    fallback_event_ids: list[str],
) -> list[dict[str, Any]]:
    """Forgiving parse — strip code fences, fall back to per-event 0.0 if model
    returned malformed output. We don't crash the agent on a bad LLM response."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[: -3]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return [
            {"event_id": eid, "relevance_score": 0.0, "one_line_reason": "parse error"}
            for eid in fallback_event_ids
        ]
    if not isinstance(data, list):
        return [
            {"event_id": eid, "relevance_score": 0.0, "one_line_reason": "wrong shape"}
            for eid in fallback_event_ids
        ]
    return data


def _estimate_cost(
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    cache_creation_tokens: int,
    output_tokens: int,
) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (
        (input_tokens - cached_input_tokens - cache_creation_tokens) * p["in"] / 1_000_000
        + cached_input_tokens * p["in_cached"] / 1_000_000
        + cache_creation_tokens * p["in_write"] / 1_000_000
        + output_tokens * p["out"] / 1_000_000
    )


def total_cost(entries: Iterable[LedgerEntry]) -> float:
    return sum(e.estimated_cost_usd for e in entries)
