# results/

Eval output lands here.

## What's committed

- **`eval-matrix.md`** — the rendered comparison matrix. Updated each time
  someone runs `make eval && make report`. The committed copy is a snapshot;
  re-run locally for fresh numbers.

## What's gitignored

- **`eval-summary.json`** — full per-cell metrics. Re-generated each run.
- **`runs/`** — any per-impl state-dirs left behind by dimension tests
  (the harness writes into `results/dim<N>/<impl>/`).
- **`*.sqlite`** — checkpoint databases from impl runs.

## Reproducing the matrix

```bash
make install
make eval          # runs all available dims x impls; deterministic in offline mode
make report        # renders results/eval-matrix.md from the JSON summary
```

Set `ANTHROPIC_API_KEY` to also run the Claude Agent SDK impl (~$5–10 for the
full Phase 2 matrix).

## Phase status

Phase 2 covers dimensions 1, 6, 8 (the deterministic, fast-to-run ones).
Dimensions 2, 3, 4, 5, 7 (and optional 9, 10) land in Phase 4 as part of the
8h compressed run inside e2b sandboxes.
