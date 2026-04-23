# Fixtures

Frozen inputs the agent runs against. **Implementations must not modify these files** — they are the cross-impl contract. To extend or regenerate, edit the source list and re-run the generator (TODO: `make fixtures`).

## Files

- **`sources.json`** — 8 sources (RSS feeds + GitHub releases). Modelled on real feeds an agent would plausibly monitor; URLs are realistic but the agent never actually fetches them at runtime — events come from `timeline.json`.
- **`timeline.json`** — 2 weeks of plausible events (2026-04-01 → 2026-04-15) spread across the 8 sources. Mix of on-topic (relevant to user interests in `user.json`) and off-topic items, to give the goal-drift evaluation real signal.
- **`user.json`** — the user profile (interests, tone, max items per digest).
- **`adversarial-events.json`** — additional off-topic items injected at runtime for the goal-drift dimension (dim 5). Not part of the base timeline.
- **`mutations.json`** — planned mutations applied to the source state during the agent's sleeps, for the stale-state dimension (dim 7). E.g. "edit body of evt_0007 between fixture-day 4 and 5."
- **`memory-probes.json`** — planted facts + queries for the memory-recall dimension (dim 4).

## Realism notes

- Event volumes per source are roughly calibrated to real cadence: Anthropic blog posts ~weekly, GitHub release feeds 2–4× per week.
- Body texts are short and evocative rather than full posts — enough to drive relevance scoring and summarization, not so long that fixtures balloon.
- All `url` fields point to plausible-looking URLs. Implementations should treat them as opaque identifiers; nothing in the test resolves them.
- Timestamps use UTC throughout.

## How fixtures interact with the fixture clock

The clock anchors fixture-start (default `2026-04-01T00:00:00Z`) to wall-clock now. Events become "visible" to the agent only when their `fixture_timestamp ≤ clock.now()`. See `task/clock.py` for the implementation.

## Refresh procedure (TODO)

Phase 0 ships the fixtures hand-curated. A future `make fixtures` target will:
1. Pull the last 2 weeks of real items from each source via RSS / GitHub Releases API.
2. Strip noise, normalize timestamps to the canonical fortnight.
3. Re-emit `timeline.json`.

Until that lands, edits to fixtures are manual.
