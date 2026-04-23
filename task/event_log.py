"""Append-only events.jsonl writer.

Required by spec R7: every implementation writes a structured event log
to `state/events.jsonl`, one JSON object per line, fixture-time stamped.
The replay-eval (dim 8) re-runs the agent from this log.

Implementations import this directly so the format is identical across all
four — that's what makes replay comparable.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from task.types import EventLogEntry


class EventLog:
    """Thread-unsafe but process-safe append-only JSONL log.

    Process-safe because each write is a single `os.write` of a complete JSON
    line — concurrent processes appending to the same file do not interleave
    bytes within a line on POSIX (writes < PIPE_BUF, which is ≥4KB).
    For multi-thread use within one process, hold an external lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # touch
        self.path.touch(exist_ok=True)

    def append(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        ts: datetime,
    ) -> None:
        entry = EventLogEntry(ts=ts, kind=kind, payload=payload or {})
        # model_dump_json is faster than json.dumps + manual encoding and
        # handles datetime serialization correctly.
        line = entry.model_dump_json() + "\n"
        data = line.encode("utf-8")
        # O_APPEND on POSIX gives us atomic appends below PIPE_BUF.
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def read_all(self) -> list[EventLogEntry]:
        if not self.path.exists():
            return []
        out: list[EventLogEntry] = []
        with self.path.open() as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                out.append(EventLogEntry.model_validate_json(line))
        return out
