"""Unit tests for the fixture clock and types.

These run as part of `make test` (pytest). They do NOT exercise the actual
2-week fixture timing — see clock_smoke.py for that.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl

from task.clock import DEFAULT_FIXTURE_START, FixtureClock
from task.types import Source, SourceEvent, SourceKind


def _evt(id: str, ts: datetime, source_id: str = "src-1") -> SourceEvent:
    return SourceEvent(
        id=id,
        source_id=source_id,
        fixture_timestamp=ts,
        kind="post",
        title=f"event {id}",
        url=HttpUrl("https://example.test/" + id),
        body_md="body",
    )


def _src(id: str = "src-1") -> Source:
    return Source(
        id=id,
        type=SourceKind.RSS,
        url=HttpUrl("https://example.test/feed"),
        name="Test Source",
    )


def test_clock_now_advances_with_speed() -> None:
    clock = FixtureClock.for_test(speed=100.0)
    clock.start(wall_anchor=time.time())
    t1 = clock.now()
    time.sleep(0.05)
    t2 = clock.now()
    delta = (t2 - t1).total_seconds()
    # ~5s in fixture-time for 0.05s wall x 100x speed; allow slack.
    assert 3.0 < delta < 8.0


def test_fetch_until_excludes_future_events() -> None:
    base = DEFAULT_FIXTURE_START
    events = [_evt(f"e{i}", base + timedelta(hours=i)) for i in range(10)]
    clock = FixtureClock.for_test(events=events, sources=[_src()])
    visible = clock.fetch_events_until(base + timedelta(hours=4, minutes=30))
    assert [e.id for e in visible] == ["e0", "e1", "e2", "e3", "e4"]


def test_fetch_until_with_since_is_strict_lower() -> None:
    base = DEFAULT_FIXTURE_START
    events = [_evt(f"e{i}", base + timedelta(hours=i)) for i in range(5)]
    clock = FixtureClock.for_test(events=events, sources=[_src()])
    visible = clock.fetch_events_until(
        base + timedelta(hours=4),
        since=base + timedelta(hours=1),
    )
    assert [e.id for e in visible] == ["e2", "e3", "e4"]


def test_fetch_until_filters_by_source() -> None:
    base = DEFAULT_FIXTURE_START
    events = [
        _evt("a1", base + timedelta(hours=1), source_id="src-a"),
        _evt("b1", base + timedelta(hours=2), source_id="src-b"),
        _evt("a2", base + timedelta(hours=3), source_id="src-a"),
    ]
    clock = FixtureClock.for_test(
        events=events,
        sources=[_src("src-a"), _src("src-b")],
    )
    visible = clock.fetch_events_until(
        base + timedelta(hours=10),
        source_ids=["src-a"],
    )
    assert [e.id for e in visible] == ["a1", "a2"]


def test_next_event_ts_returns_first_after() -> None:
    base = DEFAULT_FIXTURE_START
    events = [_evt(f"e{i}", base + timedelta(hours=i)) for i in range(3)]
    clock = FixtureClock.for_test(events=events, sources=[_src()])
    nxt = clock.next_event_ts(base + timedelta(minutes=30))
    assert nxt == base + timedelta(hours=1)


def test_next_event_ts_returns_none_at_end() -> None:
    base = DEFAULT_FIXTURE_START
    events = [_evt("e0", base)]
    clock = FixtureClock.for_test(events=events, sources=[_src()])
    assert clock.next_event_ts(base + timedelta(hours=1)) is None


def test_now_requires_start() -> None:
    clock = FixtureClock.for_test()
    with pytest.raises(RuntimeError):
        clock.now()


async def test_sleep_until_returns_immediately_for_past() -> None:
    clock = FixtureClock.for_test(speed=1.0)
    clock.start()
    t0 = time.time()
    # fixture_start is in the past once started, so this should not block.
    await clock.sleep_until(DEFAULT_FIXTURE_START)
    assert time.time() - t0 < 0.05


async def test_sleep_until_advances_to_target() -> None:
    clock = FixtureClock.for_test(speed=1000.0)  # 1000x: 100s fixture = 0.1s wall
    clock.start()
    target = clock.now() + timedelta(seconds=100)
    t0 = time.time()
    await clock.sleep_until(target)
    elapsed = time.time() - t0
    assert 0.05 < elapsed < 0.5


def test_fixture_start_must_be_tz_aware() -> None:
    with pytest.raises(ValueError):
        FixtureClock(
            timeline=[],
            sources={},
            fixture_start=datetime(2026, 4, 1),  # naive
        )
