# LangGraph + Postgres impl
# Phase 1 will populate implementations/langgraph/.
# This image installs Postgres locally for the checkpointer.

FROM halfmarathon-base:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-15 \
 && rm -rf /var/lib/apt/lists/*

# uv will install python deps at runtime from the workspace pyproject.
# Phase 1 will add `RUN uv sync --extra langgraph` once that extra exists.

EXPOSE 5432
