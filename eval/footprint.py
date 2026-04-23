"""Cross-cutting "footprint" metrics that vary between implementations.

The dim-based accuracy scores measure what the impl *does* — and many of
those are determined by the LLM (real or mocked), not the framework. The
metrics in this module measure what the impl *is*: how much code, how many
dependencies, how much wall-clock overhead it adds, how many services you
have to operate.

These are what genuinely separates LangGraph from Pydantic-AI+Temporal in
the offline matrix (where they tie at 0.90 on every dim). Two impls can
produce identical outputs but differ wildly on adoption cost.

Computed automatically:
    - lines_of_code:    `wc -l` over implementations/<impl>/*.py
                        (excluding tests/smoke/__init__)
    - source_files:     count of .py files in the impl
    - direct_deps:      count of direct pip deps for that extra
    - mean_elapsed_s:   mean wall-clock per dim cell from eval-summary

Hand-curated (in IMPL_OPS_METADATA below):
    - services_to_run:  number of long-lived processes outside the agent
    - prod_storage:     what backs the agent's state in production
    - ops_steps:        ordered list of bring-up steps from a clean machine
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IMPL_DIR = ROOT / "implementations"


# ---- hand-curated operational metadata ---------------------------------

# Reflects an honest builder's experience bringing each impl up from a
# clean machine. These were measured during the actual build-out
# (Phases 0-5) — see findings.md for narrative details.

IMPL_OPS_METADATA: dict[str, dict[str, Any]] = {
    "langgraph": {
        "services_to_run": 0,
        "prod_storage": "SQLite file (or Postgres for HA)",
        "ops_steps": [
            "uv pip install langgraph langgraph-checkpoint-sqlite",
        ],
        # 1-5 ratings; higher = better unless noted otherwise.
        "ratings": {
            "observability": (4, "LangGraph Studio + LangSmith integration; "
                                 "checkpoint state inspectable via API."),
            "mental_model_simplicity": (3, "Graph + state + checkpointer; "
                                           "well-documented but you own routing."),
            "production_scaling": (4, "Stateless workers + checkpointer in "
                                      "shared db scales horizontally."),
            "multi_tenancy": (4, "thread_id per user is the canonical pattern; "
                                 "well-supported."),
            "type_safety": (3, "TypedDict state. Tools and edges are loose."),
        },
        "vendor_lockin": "Low — Apache 2.0; tied to LangChain ecosystem but "
                         "model-provider-agnostic.",
    },
    "temporal_pydantic": {
        "services_to_run": 1,  # Temporal dev server (auto-spawned)
        "prod_storage": "Temporal history (Cassandra/Postgres in prod)",
        "ops_steps": [
            "uv pip install pydantic-ai-slim[temporal,anthropic] temporalio",
            "Temporal dev server: bundled in temporalio.testing — auto-spawn",
        ],
        "ratings": {
            "observability": (5, "Temporal Web UI is the gold standard for "
                                 "workflow debugging: time-travel replay, "
                                 "event history, signal/query inspection."),
            "mental_model_simplicity": (
                2, "Workflow/activity determinism contract is famously steep. "
                   "pydantic-ai layer adds another concept on top.",
            ),
            "production_scaling": (5, "Designed for it. Workers scale "
                                      "horizontally, history is durable."),
            "multi_tenancy": (4, "workflow-per-user is the canonical pattern; "
                                 "well-supported with workflow IDs."),
            "type_safety": (5, "Pydantic models flow through Temporal payloads "
                               "via pydantic_data_converter; workflow inputs typed."),
        },
        "vendor_lockin": "Low — Temporal is OSS Apache 2.0, polyglot (Go/"
                         "Python/TS/Java/Ruby/PHP). Pydantic-AI is OSS MIT. "
                         "Model provider swappable.",
    },
    "letta": {
        "services_to_run": 2,  # Letta server + Postgres
        "prod_storage": "Postgres + pgvector (Letta server-managed)",
        "ops_steps": [
            "uv pip install letta-client",
            "Run Letta server (PyPI: `letta` package)",
            "Postgres + pgvector extension installed",
            "Schema bootstrap (no migration tool bundled — see findings.md)",
        ],
        "ratings": {
            "observability": (3, "Web dashboard for agents/messages; limited "
                                 "workflow-level view."),
            "mental_model_simplicity": (
                3, "Server + memory blocks + recall + archival is idiomatic "
                   "but specific to Letta's worldview.",
            ),
            "production_scaling": (4, "Server scales independently; multi-"
                                      "agent fan-out is built-in."),
            "multi_tenancy": (5, "Built for it: each user is an agent, "
                                 "memory_blocks isolate state."),
            "type_safety": (3, "Pydantic models internally; client surface is "
                               "dict-heavy."),
        },
        "vendor_lockin": "Medium — Open source MIT, but the server-resident "
                         "agent model is Letta-specific. Letta Cloud lock-in "
                         "if you don't self-host (which itself is non-trivial).",
    },
    "claude_sdk": {
        "services_to_run": 0,
        "prod_storage": "Filesystem (progress.md + knowledge_base.json + git)",
        "ops_steps": [
            "uv pip install claude-agent-sdk",
            "Anthropic API key (real LLM, no offline mock)",
        ],
        "ratings": {
            "observability": (3, "Hooks (PreToolUse, PostToolUse, Stop) + "
                                 "transcripts + git history. DIY observability."),
            "mental_model_simplicity": (
                5, "Agent reads files, runs in a loop. Almost trivial conceptually.",
            ),
            "production_scaling": (2, "Single process per agent. Multi-agent "
                                      "orchestration is your problem."),
            "multi_tenancy": (2, "One CWD per agent — per-user means "
                                 "per-process or per-directory."),
            "type_safety": (2, "Plain Python. Types are what you write."),
        },
        "vendor_lockin": "High — Anthropic API only. SDK is MIT, but the "
                         "agent loop assumes Claude semantics. Bedrock/Vertex "
                         "routing exists but you're still on Anthropic models.",
    },
}


# ---- computed metrics --------------------------------------------------


RatingValue = tuple[int, str]  # (score 1-5, one-line rationale)


@dataclass(frozen=True)
class Footprint:
    impl_id: str
    lines_of_code: int
    source_files: int
    direct_deps: int
    mean_elapsed_s: float | None  # None if impl ran no cells
    services_to_run: int
    prod_storage: str
    ops_steps: list[str]
    # Hand-curated 1-5 ratings, each with a one-line rationale.
    ratings: dict[str, RatingValue]
    vendor_lockin: str

    @property
    def setup_step_count(self) -> int:
        return len(self.ops_steps)


def _count_loc(impl_id: str) -> tuple[int, int]:
    """Return (loc, file_count) for the impl's hand-written source.

    Excludes __init__.py and smoke.py (test scaffolding) so the number
    reflects the code a builder actually has to maintain.
    """
    impl_path = IMPL_DIR / impl_id
    if not impl_path.exists():
        return (0, 0)
    files = [
        p for p in impl_path.rglob("*.py")
        if p.name not in ("__init__.py", "smoke.py")
    ]
    loc = sum(
        sum(1 for line in p.read_text().splitlines() if line.strip())
        for p in files
    )
    return (loc, len(files))


_EXTRA_NAME_BY_IMPL = {
    "langgraph": "langgraph",
    "temporal_pydantic": "temporal-pydantic",
    "letta": "letta",
    "claude_sdk": "claude-sdk",
}


def _direct_deps(impl_id: str) -> int:
    """Number of pip deps in the matching pyproject extra."""
    extra = _EXTRA_NAME_BY_IMPL.get(impl_id)
    if extra is None:
        return 0
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        return 0
    data = tomllib.loads(pyproject.read_text())
    extras = data.get("project", {}).get("optional-dependencies", {})
    return len(extras.get(extra, []))


def _mean_elapsed(impl_id: str, results: list[dict]) -> float | None:
    """Mean wall-clock per cell that this impl actually ran."""
    elapsed = [
        float(r.get("elapsed_s") or 0.0)
        for r in results
        if r.get("impl_id") == impl_id and (r.get("elapsed_s") or 0.0) > 0
    ]
    return None if not elapsed else sum(elapsed) / len(elapsed)


def compute(impl_id: str, results: list[dict]) -> Footprint:
    loc, files = _count_loc(impl_id)
    ops = IMPL_OPS_METADATA.get(impl_id, {})
    raw_ratings = ops.get("ratings") or {}
    # Normalize: tuple form (score, note); accept missing as (0, "").
    ratings: dict[str, RatingValue] = {
        k: (int(v[0]), str(v[1])) for k, v in raw_ratings.items()
    }
    return Footprint(
        impl_id=impl_id,
        lines_of_code=loc,
        source_files=files,
        direct_deps=_direct_deps(impl_id),
        mean_elapsed_s=_mean_elapsed(impl_id, results),
        services_to_run=int(ops.get("services_to_run", 0)),
        prod_storage=str(ops.get("prod_storage", "")),
        ops_steps=list(ops.get("ops_steps") or []),
        ratings=ratings,
        vendor_lockin=str(ops.get("vendor_lockin", "")),
    )


def compute_all(impls: list[str], results: list[dict]) -> dict[str, Footprint]:
    return {impl: compute(impl, results) for impl in impls}


# Convenience for ad-hoc inspection.
if __name__ == "__main__":  # pragma: no cover
    summary_path = ROOT / "results" / "eval-summary.json"
    if summary_path.exists():
        results = json.loads(summary_path.read_text())["results"]
    else:
        results = []
    impls = sorted(IMPL_OPS_METADATA.keys())
    for fp in compute_all(impls, results).values():
        print(fp)
