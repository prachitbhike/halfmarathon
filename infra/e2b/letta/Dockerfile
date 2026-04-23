# Letta self-hosted impl
# Letta server + Postgres in the same sandbox.
# Bumped memory in e2b.toml because Letta + Postgres + Python is heavier.

FROM halfmarathon-base:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-15 \
 && rm -rf /var/lib/apt/lists/*

# Phase 1 will add `RUN uv sync --extra letta` once that extra exists.
# The Letta server itself is a Python process, started from start.sh.

EXPOSE 8283 5432
