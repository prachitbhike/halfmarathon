#!/usr/bin/env bash
# Sandbox entrypoint for the Claude Agent SDK impl.
# Phase 0: scaffold only.
set -euo pipefail

echo "[halfmarathon-claude-sdk] starting (Phase 0 scaffold)"

cd /workspace

# Phase 1 will:
#   1. uv sync --extra claude-sdk
#   2. exec uv run python -m implementations.claude_sdk.run --config ...

exec sleep infinity
