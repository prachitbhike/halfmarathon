#!/usr/bin/env bash
# Sandbox entrypoint for the LangGraph impl.
# Phase 0: scaffold only. Phase 1 wires up Postgres + the agent.
set -euo pipefail

echo "[halfmarathon-langgraph] starting (Phase 0 scaffold)"

cd /workspace
test -f task/fixtures/timeline.json || {
  echo "fixtures missing; expected at /workspace/task/fixtures/timeline.json"
  exit 1
}

# Phase 1 will:
#   1. service postgresql start
#   2. createdb langgraph
#   3. uv sync --extra langgraph
#   4. exec uv run python -m implementations.langgraph.run --config ...

# For now, keep the sandbox alive so we can shell in for inspection.
exec sleep infinity
