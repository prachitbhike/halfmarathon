"""Pydantic AI agents + typed outputs used from inside Temporal activities.

We use Pydantic AI *inside* activities, not directly inside the workflow. This
keeps the workflow strictly deterministic (no IO, no model calls) and lets the
activity retry/timeout policies handle flaky model providers cleanly. The
TemporalAgent wrapper is an alternative pattern that wraps the model call
itself as an activity — for our call shape, doing it by hand inside an activity
is simpler to reason about and keeps the offline path identical to the other
impls.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field
from pydantic_ai import Agent

OFFLINE = os.environ.get("HALFMARATHON_OFFLINE_LLM") == "1"


class RelevanceScore(BaseModel):
    event_id: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    one_line_reason: str


class RelevanceScores(BaseModel):
    items: list[RelevanceScore]


# Only create the real agents when online — import-time TestModel substitution
# trips over pydantic-ai's model registry.
_RELEVANCE_SYSTEM = (
    "You are a relevance scorer for a research-radar agent.\n"
    "You receive a JSON list of recent items and a list of user interests.\n"
    "Score each item in [0, 1] and write one line explaining the score.\n"
    "Be conservative: most items are not relevant to most users.\n"
    "0.0 = irrelevant, 0.5 = tangential, 0.8 = on-topic, 1.0 = exact match."
)

_SUMMARY_SYSTEM = (
    "Write a tight, technical one-paragraph summary of the item below, "
    "in 2-3 sentences, matching the user's stated tone. Lead with the "
    "substantive change or claim. No marketing language. Output the "
    "summary text only, no preamble."
)


def relevance_agent() -> Agent[None, RelevanceScores]:
    """Return a freshly-constructed agent. Callers instantiate per-activity."""
    return Agent(
        model="anthropic:claude-sonnet-4-6",
        output_type=RelevanceScores,
        system_prompt=_RELEVANCE_SYSTEM,
    )


def summary_agent() -> Agent[None, str]:
    return Agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=_SUMMARY_SYSTEM,
    )
