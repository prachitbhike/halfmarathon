# Contributing

Pull requests welcome — especially for:

- A 5th implementation (Convex / Cloudflare Agents / Mastra / DBOS — the
  TS-native ones we deliberately scoped out in [plan.md](plan.md))
- Better fixtures (a months-long timeline would unlock real dim 3 evaluation)
- Real-LLM dim runs from the cloud impls (Letta + Claude SDK columns
  would populate the matrix)
- A new dimension test — see [eval/dimensions/base.py](eval/dimensions/base.py)
  for the `DimensionResult` shape

## Before submitting a PR

```bash
make install
make test          # 21 unit tests, ~0.4s
make lint          # ruff
make typecheck     # mypy on task/

# Smoke whichever impl you touched:
make smoke-langgraph         # offline mock; free
make smoke-temporal-pydantic # offline mock; free
make smoke-letta             # needs LETTA_BASE_URL
make smoke-claude-sdk        # needs ANTHROPIC_API_KEY (~$0.10–$0.50)

# If you changed a dim or an impl, re-run the matrix:
HALFMARATHON_OFFLINE_LLM=1 uv run python -m eval.harness
make report  # writes results/eval-matrix.md — commit if it changed
```

## Adding a new implementation

1. Create `implementations/<name>/__init__.py` + `run.py` + `smoke.py`.
2. `run.py` must expose `async def run_loop(*, profile, state_dir,
   fixture_start, until, speed, fixtures_dir=None, **_) -> dict`.
3. Honor the [task spec](task/spec.md) R1–R7 (event log, file paths,
   approval convention).
4. Register in [eval/impls.py](eval/impls.py) — `_wrap_<name>()` adapter
   + `ImplSpec` entry.
5. Add a `make smoke-<name>` target.
6. Run the matrix and commit the updated `results/eval-matrix.md`.

## Adding a new dimension

1. New file `eval/dimensions/dim<N>_<short>.py` with `DIM_ID`, `DIM_NAME`,
   and `async def run(spec, *, results_dir, profile) -> DimensionResult`.
2. Register in `eval/dimensions/__init__.py` and `eval/harness.DIMENSIONS`.
3. Document the score thresholds in the module docstring.
4. Run the matrix; verify the dim shows up in `results/eval-matrix.md`.

## Fixtures

The canonical fixtures under `task/fixtures/` are intentionally small + hand-curated. The fixture-override mechanism
([eval/fixtures_override.py](eval/fixtures_override.py)) lets dimension
tests inject events / mutations without touching the canonical files.
Use it.

## License

By contributing, you agree your contribution is licensed under the
repository's [MIT License](LICENSE).
