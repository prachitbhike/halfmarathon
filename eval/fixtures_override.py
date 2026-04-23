"""Build a temporary fixtures directory with timeline mutations applied.

Used by dim 4/5/7 to inject probe events, adversarial events, or simulate
deletions/edits without touching the canonical fixtures under task/fixtures/.

Each impl's `run_loop()` accepts a `fixtures_dir` parameter; the eval
harness builds an override dir per dim run and points the impl at it.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_FIXTURES = ROOT / "task" / "fixtures"


def build_override(
    dest_dir: Path,
    *,
    add_events: Iterable[dict[str, Any]] | None = None,
    delete_event_ids: Iterable[str] | None = None,
    edit_events: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Materialize an overridden fixtures directory and return its path.

    Args:
        dest_dir: where to write the overridden fixtures (created if missing).
        add_events: events to append to timeline.json (kept sorted).
        delete_event_ids: event ids to remove from timeline.json.
        edit_events: {event_id: {field: new_value, ...}} — applied to matching
            events (e.g. {"evt_0011": {"title": "..."}}).

    Returns dest_dir.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Always copy sources.json + user.json verbatim — only timeline mutates.
    for name in ("sources.json", "user.json"):
        src = CANONICAL_FIXTURES / name
        if src.exists():
            shutil.copy(src, dest_dir / name)

    timeline = json.loads((CANONICAL_FIXTURES / "timeline.json").read_text())

    if delete_event_ids:
        deletes = set(delete_event_ids)
        timeline = [evt for evt in timeline if evt.get("id") not in deletes]

    if edit_events:
        for evt in timeline:
            patch = edit_events.get(evt.get("id"))
            if patch:
                evt.update(patch)

    if add_events:
        timeline.extend(add_events)

    timeline.sort(key=lambda evt: evt.get("fixture_timestamp", ""))
    (dest_dir / "timeline.json").write_text(
        json.dumps(timeline, indent=2, default=str)
    )
    return dest_dir
