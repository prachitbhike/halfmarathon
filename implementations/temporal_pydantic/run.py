"""Pydantic AI + Temporal entrypoint.

CLI:
    python -m implementations.temporal_pydantic.run \
        --config task/fixtures/user.json \
        --state-dir implementations/temporal_pydantic/state \
        --start-from 2026-04-01T00:00:00Z \
        --until    2026-04-15T00:00:00Z \
        --speed    86400

The harness:
    1. Spawns (once per process) a local Temporal dev server via
       ``WorkflowEnvironment.start_local``, cached in a module-level
       singleton. A previously-started env is reused across invocations so
       eval dims (dim1 crash-recovery, dim6 HITL pause/resume) that call
       ``run_loop`` multiple times observe the same Temporal cluster.
       The env is torn down at process exit via ``atexit``.
    2. Starts a Worker that registers the workflow + activities + uses the
       pydantic data converter so pydantic models flow through Temporal
       payloads cleanly.
    3. Starts a ReleaseRadarWorkflow execution, using an
       ``ALLOW_DUPLICATE`` id-reuse policy so a later ``run_loop`` call with
       the same thread_id can start a fresh execution — that execution
       reloads its state from ``state_dir`` (see
       ``activities.load_prior_state``), which is how "resume" is modeled.
    4. Awaits its result and returns the summary.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowFailureError,
)
from temporalio.common import WorkflowIDReusePolicy
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


# ---- process-level Temporal env singleton --------------------------------
#
# Sharing a single WorkflowEnvironment across run_loop calls is what makes
# dim1/dim6 "resume" semantics actually testable. If we spun up a fresh env
# per call, Run B would talk to a brand-new cluster that had never heard of
# Run A's workflow — any "resume" would reduce to filesystem convergence.


class _EnvHolder:
    """Module-level mutable state for the Temporal env + client.

    Wrapped in a class so we can mutate via attribute access without
    `global` statements (ruff PLW0603 objects to those).
    """

    env: WorkflowEnvironment | None = None
    client: Client | None = None
    atexit_registered: bool = False


_HOLDER = _EnvHolder()
_ENV_LOCK = asyncio.Lock()


async def _get_env() -> tuple[WorkflowEnvironment, Client]:
    async with _ENV_LOCK:
        if _HOLDER.env is None or _HOLDER.client is None:
            _HOLDER.env = await WorkflowEnvironment.start_local(
                data_converter=pydantic_data_converter,
            )
            _HOLDER.client = _HOLDER.env.client
            if not _HOLDER.atexit_registered:
                atexit.register(_shutdown_env_sync)
                _HOLDER.atexit_registered = True
        return _HOLDER.env, _HOLDER.client


def _shutdown_env_sync() -> None:
    """atexit hook — best-effort teardown of the shared Temporal env."""
    env = _HOLDER.env
    _HOLDER.env = None
    _HOLDER.client = None
    if env is None:
        return
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(env.shutdown())
        finally:
            loop.close()
    except Exception as exc:
        log.warning("temporal env shutdown failed: %s", exc)


async def shutdown_env() -> None:
    """Explicit async teardown (used by long-lived test harnesses)."""
    env = _HOLDER.env
    _HOLDER.env = None
    _HOLDER.client = None
    if env is not None:
        await env.shutdown()


async def run_loop(
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float,
    thread_id: str = "default",
    fixtures_dir: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Drive one workflow execution to completion and return its summary.

    ``thread_id`` maps to Temporal's workflow id. Calling ``run_loop`` a
    second time with the same thread_id starts a *new* workflow execution
    (id-reuse policy: ALLOW_DUPLICATE). That new execution reconstructs
    prior state (kb, published_weeks, last_fetch_iso, procedural_notes)
    from ``state_dir`` via the load_prior_state activity — which is how
    Temporal's resume semantics are modeled here.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    events_log = EventLog(state_dir / "events.jsonl")
    events_log.append("wake", {"reason": "harness_boot"}, ts=fixture_start)

    workflow_id = f"release-radar-{thread_id}"

    _, client = await _get_env()

    # Worker is short-lived: it lives only for the duration of this call,
    # registering against the shared cluster. Starting/stopping a worker
    # is cheap, unlike starting the Temporal server.
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ReleaseRadarWorkflow],
        activities=ALL_ACTIVITIES,
    )

    async with worker:
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
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
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


if __name__ == "__main__":
    main()
