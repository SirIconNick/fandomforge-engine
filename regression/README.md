# FandomForge Regression Suite

Run `ff regress` from the repo root to verify that recent engine changes haven't degraded rendered output quality. The command re-renders each project whose baseline is stored here, scores the result with `ff review`, and reports PASS / WARN / FAIL against the stored baseline score with a configurable tolerance (default: overall score must not drop more than 2 points, no individual dimension more than 5 points).

## How to run

```bash
ff regress                          # run every locked baseline
ff regress --project action-legends # run one project only
ff regress --strict                 # any score drop fails (zero tolerance)
ff regress --json                   # machine-readable output for CI
```

## Why there is only one baseline right now

Only `action-legends` currently renders at A- / 90.7, which meets the lock threshold. The other four edit types (tribute, sad/emotional, dance/movement, dialogue-narrative) do not have working render pipelines yet. Per amendment A2 of the v2 roadmap, each non-action baseline locks the moment its vertical slice renders above Competent tier (overall >= 75 AND no individual dimension score < 60). Until then, locking those projects would create false coverage — they would pass against a trash baseline rather than a meaningful floor. Run `ff regress freeze --project <slug>` once a new edit type clears Competent tier.

## Baseline files

Each `baselines/<project>.review.json` is a snapshot of `projects/<project>/data/post-render-review.json` at the time the baseline was locked. The regress command compares the live re-render's scores against these snapshots.

## Adding a new baseline

```bash
# Render the project first, then lock it
ff regress freeze --project <slug>
# Optionally specify a different tier floor than the default (Competent)
ff regress freeze --project <slug> --tier-floor exceptional
```
