#!/usr/bin/env bash
# Sandbox entrypoint for the Pydantic AI + Temporal impl.
# Phase 0: scaffold only.
set -euo pipefail

echo "[halfmarathon-temporal-pydantic] starting (Phase 0 scaffold)"

cd /workspace

# Phase 1 will:
#   1. temporal server start-dev --headless &
#   2. uv sync --extra temporal-pydantic
#   3. exec uv run python -m implementations.temporal_pydantic.run --config ...

exec sleep infinity
