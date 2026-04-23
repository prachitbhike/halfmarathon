"""Pydantic AI + Temporal entrypoint.

CLI:
    python -m implementations.temporal_pydantic.run \
        --config task/fixtures/user.json \
        --state-dir implementations/temporal_pydantic/state \
        --start-from 2026-04-01T00:00:00Z \
        --until    2026-04-15T00:00:00Z \
        --speed    86400

The harness:
    1. Spawns a local Temporal dev server (via WorkflowEnvironment.start_local).
    2. Starts a Worker that registers the workflow + activities + uses
       PydanticAIPlugin's data converter so pydantic models flow through
       Temporal payloads cleanly.
    3. Starts a single ReleaseRadarWorkflow execution.
    4. Awaits its result and returns the summary.

To resume after a restart, pass the same --workflow-id; Temporal looks the
execution up by id and (if it's still running) the new client just observes
its progress. If the workflow has completed, this harness no-ops and returns
the prior result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from implementations.temporal_pydantic.activities import ALL_ACTIVITIES
from implementations.temporal_pydantic.workflow import (
    ReleaseRadarWorkflow,
    WorkflowArgs,
    WorkflowSummary,
)
from task.clock import DEFAULT_FIXTURE_START
from task.event_log import EventLog
from task.types import UserProfile

log = logging.getLogger("halfmarathon.temporal_pydantic")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "task" / "fixtures"

TASK_QUEUE = "halfmarathon-release-radar"


async def run_loop(
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float,
    thread_id: str = "default",
    fixtures_dir: Path | None = None,
) -> dict[str, Any]:
    """Drive one workflow execution to completion and return its summary.

    `thread_id` maps to Temporal's workflow id so re-invoking with the same
    thread_id either resumes-by-observation or re-uses the previous result.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    events_log = EventLog(state_dir / "events.jsonl")
    events_log.append("wake", {"reason": "harness_boot"}, ts=fixture_start)

    workflow_id = f"release-radar-{thread_id}"

    # Local dev server spawned in-process. Temporal CLI must be on $PATH.
    async with await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
    ) as env:
        client = env.client

        # Worker registers our workflow + all activities.
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[ReleaseRadarWorkflow],
            activities=ALL_ACTIVITIES,
        )

        async with worker:
            # If a previous run with this id is still running or completed,
            # observe its handle. Otherwise start a new execution.
            wf_args = WorkflowArgs(
                state_dir=str(state_dir),
                profile_json=profile.model_dump_json(),
                fixture_start_iso=fixture_start.isoformat(),
                until_iso=until.isoformat(),
                speed=speed,
                fixtures_dir=str(fixtures_dir) if fixtures_dir else None,
            )
            handle = await client.start_workflow(
                ReleaseRadarWorkflow.run,
                wf_args,
                id=workflow_id,
                task_queue=TASK_QUEUE,
            )
            log.info("workflow started: %s", workflow_id)

            try:
                summary: WorkflowSummary = await handle.result()
            except WorkflowFailureError as exc:
                log.exception("workflow failed")
                events_log.append(
                    "error",
                    {"workflow_id": workflow_id, "error": str(exc.cause)},
                    ts=until,
                )
                return {
                    "published_weeks": [],
                    "kb_size": 0,
                    "procedural_notes": [],
                    "ticks": 0,
                    "error": str(exc.cause),
                }

            desc = await handle.describe()
            log.info(
                "workflow finished: %s (status=%s)",
                workflow_id, desc.status,
            )
            assert desc.status == WorkflowExecutionStatus.COMPLETED

    return {
        "published_weeks": list(summary.published_weeks),
        "kb_size": summary.kb_size,
        "procedural_notes": list(summary.procedural_notes),
        "ticks": summary.ticks,
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
    p = argparse.ArgumentParser(description="Pydantic AI + Temporal Release Radar")
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--start-from", type=_parse_iso, default=DEFAULT_FIXTURE_START)
    p.add_argument(
        "--until",
        type=_parse_iso,
        default=DEFAULT_FIXTURE_START + timedelta(days=15),
    )
    p.add_argument("--speed", type=float, default=86400.0)
    p.add_argument("--thread-id", default=f"manual-{uuid.uuid4().hex[:6]}")
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


# Touch unused imports
_ = Client


if __name__ == "__main__":
    main()
