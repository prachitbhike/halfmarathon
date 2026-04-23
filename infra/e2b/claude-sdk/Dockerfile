# Claude Agent SDK impl
# Plain Python — no Postgres, no Temporal, no extra server.
# Persistence is files + git in the sandbox FS.

FROM halfmarathon-base:latest

# git is in base. The Claude Agent SDK ships pure Python; no native deps.
# Phase 1 will add `RUN uv sync --extra claude-sdk` once that extra exists.
