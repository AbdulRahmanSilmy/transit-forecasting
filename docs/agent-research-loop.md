# Agent Loop for Data Science / ML Work

Reference notes on structuring AI-agent iteration for experiments, modelled on the software-engineering loop.

## Core idea

The software loop works because tests give a fast, objective pass/fail. Data science has no built-in equivalent — a notebook "runs" whether the result is real or not. So most of the work is **building the verifier first**, then letting the agent iterate against it.

## The loop

1. **Hypothesis** — a specific, falsifiable claim (e.g. "harmonic time-of-day terms cut segment 4 MAE at 10–30 min horizons").
2. **Plan** — the smallest experiment that could falsify it, plus the expected effect size.
3. **Implement** — code in the package, not throwaway notebook cells.
4. **Run** — the frozen evaluation harness, with fixed seeds and splits.
5. **Evaluate** — against baselines, with uncertainty (folds/bootstrap), plus diagnostics.
6. **Record** — one entry in the experiment log: hypothesis, change, numbers, verdict.
7. **Decide** — keep / discard / refine, then on to the next hypothesis.

Steps 4–6 are fixed infrastructure the agent uses but doesn't change. Steps 1, 2 and 7 are where human judgement matters most.

## What replaces the test suite

- **Frozen evaluation harness** — one script (e.g. `evaluate.py --model X`) with the same data loading, time-based backtest splits and metrics every time. The agent must not edit it without asking; otherwise it tends to make the score better by changing how it's measured.
- **Baselines always in the table** — schedule-only, historical mean by segment/time of day, current best model. Every result is a comparison.
- **Locked holdout** — iterate on validation folds only; touch the final test period rarely and only with approval, or the loop slowly overfits it.
- **Sanity checks as unit tests** — leakage (no feature timestamp after prediction time), row counts, NaN rates, shuffled-label model scores near zero.
- **Noise awareness** — report fold-to-fold spread; set a minimum effect size before a change counts as "keep".

## Memory layer (state lives in files)

- **`experiments/<study>/LOG.md`** — one row per run: git hash, hypothesis, config, metrics, verdict. Include negative results so dead ends aren't retried.
- **`RESEARCH.md`** — current goal, success metric, open questions, ruled-out ideas. Edit this to steer the loop.
- **One git commit per experiment** — any logged number is reproducible from its hash.

## Division of labour

| Agent alone | Human decides |
|---|---|
| Feature variants, hyperparameter sweeps | Which hypotheses are worth testing |
| Refactors, diagnostic plots | Whether a gain is real or an artifact |
| Running the harness, writing up runs | Touching the holdout |
| | Changing the metric or the data |

**Stopping rules:** a run budget (e.g. 10 experiments), stop after N attempts with no improvement, and stop immediately on any sanity-check failure.

## How this repo implements it

| Piece | Where |
|---|---|
| Study charter, frozen definitions, success criterion, locked test | `experiments/<study>/RESEARCH.md` |
| Experiment ledger (reasoning; cites MLflow run IDs) | `experiments/<study>/LOG.md` |
| Harness contract and protected files | `experiments/<study>/HARNESS.md` |
| Frozen harness | `experiments/hwy97_delay/evaluate.py` + `forecasting/forecasting/delay_forecast_eval.py` |
| Run store (metrics, figures, predictions) | local MLflow in `data/mlflow/` (see the [README](../README.md#viewing-experiment-results-mlflow)) |
| Agent rules | `AGENTS.md`, Research Loop section |
| One iteration, step by step | the `research-loop` skill (`/research-loop` in Claude Code) |
| Work queue and priority | GitHub project [Hwy 97 delay forecasting](https://github.com/users/AbdulRahmanSilmy/projects/3), issues labelled `study:<study>` |

Tickets move `Todo` → `In Progress` → `Review` (the agent is finished) → `Done`. Only the owner moves a ticket to `Done`, after reviewing it.

The harness is the part worth getting right first; the rest of the loop is only as good as it is.
