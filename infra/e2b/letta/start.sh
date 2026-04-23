#!/usr/bin/env bash
# Sandbox entrypoint for the Letta impl.
# Phase 0: scaffold only.
set -euo pipefail

echo "[halfmarathon-letta] starting (Phase 0 scaffold)"

cd /workspace

# Phase 1 will:
#   1. service postgresql start
#   2. createdb letta
#   3. uv sync --extra letta
#   4. uv run letta server --port 8283 &
#   5. exec uv run python -m implementations.letta.run --config ...

exec sleep infinity
