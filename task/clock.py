"""Fixture clock — replays a frozen 2-week timeline at 42x real speed.

Why this exists:
    Long-running agents react to events arriving over time. We want to
    exercise multi-week behavior (sleeps, drift, memory recall, stale state)
    in an 8h budget. The fixture clock maps wall-clock to "fixture time" at a
    constant multiplier and exposes the same primitives the agent would use
    against a real wall clock: now(), sleep_until(), fetch_events_until().

Caveats (called out in plan.md §6.2):
    Anthropic API calls are still real-time — we do not fake LLM latency or
    cost. The 5-minute prompt-cache TTL therefore behaves *better* under
    compressed time than it would in production. Dim 9 (cost regression) runs
    a small uncompressed comparison to surface this.

Usage:
    clock = FixtureClock.from_fixtures(
        timeline_path=Path("task/fixtures/timeline.json"),
        sources_path=Path("task/fixtures/sources.json"),
        fixture_start=datetime(2026, 4, 1, tzinfo=UTC),
        speed=42.0,
    )
    clock.start()  # anchors fixture-time to wall-clock now()
    while clock.now() < fixture_end:
        events = clock.fetch_events_until(clock.now())
        ...
        await clock.sleep_until(next_wake_fixture_ts)
"""

from __future__ import annotations

import asyncio
import bisect
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from task.types import Source, SourceEvent

DEFAULT_SPEED = 42.0  # 1 fixture-day == 24*60/42 ≈ 34.3 wall-clock minutes
DEFAULT_FIXTURE_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)


@dataclass
class FixtureClock:
    """Wall-clock <-> fixture-time mapping over a frozen event timeline."""

    timeline: list[SourceEvent]
    sources: dict[str, Source]
    fixture_start: datetime = DEFAULT_FIXTURE_START
    speed: float = DEFAULT_SPEED

    # set by .start(); the wall-clock instant at which fixture-time = fixture_start
    _wall_anchor: float | None = field(default=None, init=False, repr=False)
    # cached sorted fixture-timestamps for binary search
    _ts_index: list[datetime] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.fixture_start.tzinfo is None:
            raise ValueError("fixture_start must be timezone-aware")
        self.timeline.sort(key=lambda e: e.fixture_timestamp)
        self._ts_index = [e.fixture_timestamp for e in self.timeline]

    # ---------- lifecycle ------------------------------------------------

    def start(self, wall_anchor: float | None = None) -> None:
        """Anchor fixture-time to wall-clock now (or a provided wall ts)."""
        self._wall_anchor = wall_anchor if wall_anchor is not None else time.time()

    def is_started(self) -> bool:
        return self._wall_anchor is not None

    # ---------- time mapping ---------------------------------------------

    def now(self) -> datetime:
        """Current fixture-time."""
        if self._wall_anchor is None:
            raise RuntimeError("FixtureClock.start() must be called first")
        wall_elapsed = time.time() - self._wall_anchor
        fixture_elapsed = timedelta(seconds=wall_elapsed * self.speed)
        return self.fixture_start + fixture_elapsed

    def fixture_to_wall(self, fixture_ts: datetime) -> float:
        """Map a fixture timestamp to a wall-clock unix timestamp."""
        if self._wall_anchor is None:
            raise RuntimeError("FixtureClock.start() must be called first")
        delta = (fixture_ts - self.fixture_start).total_seconds() / self.speed
        return self._wall_anchor + delta

    def wall_seconds_until(self, fixture_ts: datetime) -> float:
        """Wall-clock seconds from now() until fixture_ts. Negative if past."""
        return self.fixture_to_wall(fixture_ts) - time.time()

    # ---------- sleeping --------------------------------------------------

    async def sleep_until(self, fixture_ts: datetime) -> None:
        """Async sleep until wall-clock catches up to fixture_ts.

        Returns immediately if fixture_ts is already in the past.
        """
        secs = self.wall_seconds_until(fixture_ts)
        if secs > 0:
            await asyncio.sleep(secs)

    def sleep_until_blocking(self, fixture_ts: datetime) -> None:
        """Blocking sleep until wall-clock catches up to fixture_ts."""
        secs = self.wall_seconds_until(fixture_ts)
        if secs > 0:
            time.sleep(secs)

    # ---------- event timeline -------------------------------------------

    def fetch_events_until(
        self,
        fixture_ts: datetime,
        *,
        since: datetime | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> list[SourceEvent]:
        """Events with fixture_timestamp in (since, fixture_ts].

        The agent cannot peek beyond fixture_ts — this is what enforces the
        "you cannot read tomorrow's news today" guarantee.
        """
        upper = bisect.bisect_right(self._ts_index, fixture_ts)
        lower = (
            bisect.bisect_right(self._ts_index, since) if since is not None else 0
        )
        events = self.timeline[lower:upper]
        if source_ids is not None:
            wanted = set(source_ids)
            events = [e for e in events if e.source_id in wanted]
        return events

    def next_event_ts(self, after: datetime) -> datetime | None:
        """Fixture-timestamp of the first event after `after`, or None."""
        idx = bisect.bisect_right(self._ts_index, after)
        return self._ts_index[idx] if idx < len(self._ts_index) else None

    # ---------- factories -------------------------------------------------

    @classmethod
    def from_fixtures(
        cls,
        timeline_path: Path,
        sources_path: Path,
        fixture_start: datetime = DEFAULT_FIXTURE_START,
        speed: float = DEFAULT_SPEED,
    ) -> FixtureClock:
        events_adapter = TypeAdapter(list[SourceEvent])
        sources_adapter = TypeAdapter(list[Source])
        with timeline_path.open() as f:
            events = events_adapter.validate_python(json.load(f))
        with sources_path.open() as f:
            sources_list = sources_adapter.validate_python(json.load(f))
        return cls(
            timeline=events,
            sources={s.id: s for s in sources_list},
            fixture_start=fixture_start,
            speed=speed,
        )

    @classmethod
    def for_test(
        cls,
        events: list[SourceEvent] | None = None,
        sources: list[Source] | None = None,
        speed: float = DEFAULT_SPEED,
    ) -> FixtureClock:
        """Construct without reading fixtures from disk."""
        return cls(
            timeline=events or [],
            sources={s.id: s for s in (sources or [])},
            speed=speed,
        )
