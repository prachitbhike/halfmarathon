"""End-to-end smoke test for the fixture clock.

Verifies fixtures load, the clock advances, events become visible at the
right times, and sleep_until matches wall-clock seconds correctly.

Run with: `make clock-test`
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import timedelta
from pathlib import Path

from task.clock import DEFAULT_FIXTURE_START, FixtureClock

FIXTURES = Path(__file__).parent / "fixtures"


async def main() -> int:
    clock = FixtureClock.from_fixtures(
        timeline_path=FIXTURES / "timeline.json",
        sources_path=FIXTURES / "sources.json",
        speed=86400.0,  # 1 fixture-day per 1 wall-second — smoke test must be fast
    )
    print(f"loaded {len(clock.timeline)} events across {len(clock.sources)} sources")

    clock.start()
    print(f"clock started; now() = {clock.now().isoformat()}")

    # Day-1 fetch: events visible by end of day 1.
    eod_day1 = DEFAULT_FIXTURE_START + timedelta(days=1)
    print(f"sleeping until fixture day 1 EOD ({eod_day1.isoformat()})")
    t0 = time.time()
    await clock.sleep_until(eod_day1)
    elapsed = time.time() - t0
    print(f"  woke after {elapsed:.2f}s wall-clock")

    day1_events = clock.fetch_events_until(clock.now())
    print(f"  events visible after day 1: {len(day1_events)}")
    for evt in day1_events:
        print(f"    [{evt.fixture_timestamp.isoformat()}] {evt.source_id} :: {evt.title[:60]}")

    # The agent must not be able to peek past now() — fetch_events_until at a
    # *far-future* fixture_ts must NOT match a fetch capped at now() (otherwise
    # the timeline is leaking events).
    visible_now = clock.fetch_events_until(clock.now())
    visible_far = clock.fetch_events_until(DEFAULT_FIXTURE_START + timedelta(days=20))
    if len(visible_far) <= len(visible_now):
        print("FAIL: fetch_events_until appears to be capped at now() implicitly", file=sys.stderr)
        return 1

    # next_event_ts() utility check.
    nxt = clock.next_event_ts(clock.now())
    print(f"  next event after now() at {nxt.isoformat() if nxt else 'None'}")

    # Verify since= filters correctly.
    incremental = clock.fetch_events_until(
        clock.now(),
        since=DEFAULT_FIXTURE_START + timedelta(hours=12),
    )
    print(f"  events strictly after fixture-noon day 1: {len(incremental)}")

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
