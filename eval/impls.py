"""Registry of implementations the eval harness can drive.

Each entry is an `ImplSpec` with everything needed to:
    - run the impl on a fixture window (`run_window`)
    - know whether running it costs money (`requires_api_key`)
    - identify it in the matrix (`id`, `display_name`)

All four impls expose roughly the same shape (`run_loop(profile, state_dir,
fixture_start, until, speed, ...)`) so the registry is small.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from task.types import UserProfile

# A run callable: returns the impl's summary dict (published_weeks, etc.)
RunCallable = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ImplSpec:
    id: str
    display_name: str
    requires_api_key: bool  # True => skip when ANTHROPIC_API_KEY unset
    run: RunCallable
    # Optional extra check (e.g. Letta server reachability). Returns False if
    # this impl shouldn't be auto-included in the available set.
    extra_availability_check: Callable[[], bool] | None = None
    # Some impls take additional kwargs (langgraph: thread_id). We pack them.
    extra_run_kwargs: dict[str, Any] | None = None


def _wrap_langgraph() -> RunCallable:
    """Adapter that imports lazily so missing extras don't break harness import."""

    async def _run(
        *,
        profile: UserProfile,
        state_dir: Path,
        fixture_start: datetime,
        until: datetime,
        speed: float,
        thread_id: str = "eval",
        fixtures_dir: Path | None = None,
    ) -> dict[str, Any]:
        from implementations.langgraph.run import run_loop  # noqa: PLC0415
        return await run_loop(
            profile=profile,
            state_dir=state_dir,
            fixture_start=fixture_start,
            until=until,
            speed=speed,
            thread_id=thread_id,
            fixtures_dir=fixtures_dir,
        )

    return _run


def _wrap_temporal_pydantic() -> RunCallable:
    async def _run(
        *,
        profile: UserProfile,
        state_dir: Path,
        fixture_start: datetime,
        until: datetime,
        speed: float,
        thread_id: str = "eval",
        fixtures_dir: Path | None = None,
    ) -> dict[str, Any]:
        from implementations.temporal_pydantic.run import run_loop  # noqa: PLC0415
        return await run_loop(
            profile=profile,
            state_dir=state_dir,
            fixture_start=fixture_start,
            until=until,
            speed=speed,
            thread_id=thread_id,
            fixtures_dir=fixtures_dir,
        )

    return _run


def _wrap_letta() -> RunCallable:
    async def _run(
        *,
        profile: UserProfile,
        state_dir: Path,
        fixture_start: datetime,
        until: datetime,
        speed: float,
        thread_id: str = "eval",
        fixtures_dir: Path | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from implementations.letta.run import run_loop  # noqa: PLC0415
        return await run_loop(
            profile=profile,
            state_dir=state_dir,
            fixture_start=fixture_start,
            until=until,
            speed=speed,
            agent_name=f"release-radar-{thread_id}",
            fixtures_dir=fixtures_dir,
        )

    return _run


def _letta_reachable() -> bool:
    """Cheap probe so the registry can mark letta as runnable in this env."""
    import os  # noqa: PLC0415
    try:
        from implementations.letta.run import _check_letta_reachable  # noqa: PLC0415
    except ImportError:
        return False
    base_url = os.environ.get("LETTA_BASE_URL", "http://localhost:8283")
    return _check_letta_reachable(base_url)


def _wrap_claude_sdk() -> RunCallable:
    async def _run(
        *,
        profile: UserProfile,
        state_dir: Path,
        fixture_start: datetime,
        until: datetime,
        speed: float,
        fixtures_dir: Path | None = None,
        **_: Any,  # accept and ignore extra kwargs (e.g. thread_id)
    ) -> dict[str, Any]:
        from implementations.claude_sdk.run import run_loop  # noqa: PLC0415
        return await run_loop(
            profile=profile,
            state_dir=state_dir,
            fixture_start=fixture_start,
            until=until,
            speed=speed,
            fixtures_dir=fixtures_dir,
        )

    return _run


REGISTRY: list[ImplSpec] = [
    ImplSpec(
        id="langgraph",
        display_name="LangGraph + AsyncSqliteSaver",
        requires_api_key=False,  # offline mock by default; real API optional
        run=_wrap_langgraph(),
    ),
    ImplSpec(
        id="temporal_pydantic",
        display_name="Pydantic AI + Temporal",
        requires_api_key=False,  # offline mock by default; real API optional
        run=_wrap_temporal_pydantic(),
    ),
    ImplSpec(
        id="letta",
        display_name="Letta (server-resident agent)",
        requires_api_key=True,  # needs reachable Letta server + Anthropic creds
        run=_wrap_letta(),
        extra_availability_check=_letta_reachable,
    ),
    ImplSpec(
        id="claude_sdk",
        display_name="Claude Agent SDK (file-as-memory)",
        requires_api_key=True,  # SDK shells to claude CLI; needs real API
        run=_wrap_claude_sdk(),
    ),
]


def available_impls(skip_api: bool | None = None) -> list[ImplSpec]:
    """Return registry impls runnable in the current env.

    If skip_api is None: auto-detect from env (skip API-required when key unset).
    Also runs each spec's `extra_availability_check` and excludes those that
    return False (e.g. Letta when its server isn't reachable).
    """
    if skip_api is None:
        skip_api = not bool(os.environ.get("ANTHROPIC_API_KEY"))
    out: list[ImplSpec] = []
    for s in REGISTRY:
        if skip_api and s.requires_api_key:
            continue
        if s.extra_availability_check is not None and not s.extra_availability_check():
            continue
        out.append(s)
    return out


def find_impl(impl_id: str) -> ImplSpec:
    for s in REGISTRY:
        if s.id == impl_id:
            return s
    raise KeyError(impl_id)


def gather_run(
    spec: ImplSpec,
    *,
    profile: UserProfile,
    state_dir: Path,
    fixture_start: datetime,
    until: datetime,
    speed: float = 86400.0,
    thread_id: str = "eval",
) -> dict[str, Any]:
    """Synchronous convenience wrapper for tests/scripts."""
    return asyncio.run(
        spec.run(
            profile=profile,
            state_dir=state_dir,
            fixture_start=fixture_start,
            until=until,
            speed=speed,
            thread_id=thread_id,
        )
    )


__all__ = [
    "REGISTRY",
    "ImplSpec",
    "RunCallable",
    "available_impls",
    "find_impl",
    "gather_run",
]


# Mark `Iterable` as used to keep type tooling happy with future signatures.
_ = Iterable
