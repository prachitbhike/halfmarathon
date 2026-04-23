# e2b sandbox templates

Each implementation runs in its own e2b sandbox so the multi-hour runs survive
laptop sleep, are reproducible across machines, and mirror the way real
production agents are deployed.

## Layout

```
infra/e2b/
  base/            # shared base image (Python + uv + repo + fixtures)
  langgraph/       # LangGraph + Postgres
  temporal-pydantic/   # Pydantic AI + Temporal dev server
  claude-sdk/      # Claude Agent SDK (no infra deps)
```

Each per-impl directory has:
- `e2b.toml` — e2b template manifest (template name, dockerfile, ready cmd)
- `Dockerfile` — built FROM the base image, layering impl-specific deps
- `start.sh` — entrypoint inside the sandbox; brings up infra and the agent

## Phase 0 status

These are **scaffolds**. The base image and per-impl Dockerfiles are written
to be buildable but the per-impl stacks (LangGraph, Temporal, etc.) are not
yet wired up — that happens in Phase 1+.

The base image alone should build today (`e2b template build` from
`infra/e2b/base/`); per-impl images will build once we install their deps in
Phase 1.

## Building

Requires the e2b CLI:

```bash
# install
npm i -g @e2b/cli
e2b auth login

# build the base
cd infra/e2b/base
e2b template build

# build an impl (Phase 1+)
cd infra/e2b/langgraph
e2b template build
```

## Why per-impl images and not one mega-image

Three reasons:
1. **Honest comparison** — each impl's image only has its own deps. Cold-start, image size, and "what does it actually take to ship this thing" become measurable.
2. **Isolation** — Temporal needs the dev server, the others don't. Mixing them masks the operational footprint.
3. **Parallelism** — when we run all three in parallel for the 8h compressed test, they don't share state.

## Sandbox lifetime

e2b sandboxes default to 24h max lifetime. Our 8h compressed run fits
comfortably. To extend, we'd use `keep_alive` or the persistent-sandbox
beta — out of scope for Phase 0.
