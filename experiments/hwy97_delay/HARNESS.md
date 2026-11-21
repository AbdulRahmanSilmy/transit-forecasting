# Evaluation harness spec (iteration 0 builds this)

The harness is the loop's verifier. Once the owner has approved it, it is **protected**: agents must not edit these files without asking.

- `forecasting/forecasting/delay_forecast_eval.py`: data preparation, splits, chaining, scoring
- `experiments/hwy97_delay/evaluate.py`: CLI entry point
- `experiments/hwy97_delay/config.json`: dataset window, fold dates, locked-test dates, holidays, stale-report threshold
- `forecasting/forecasting/delay_forecast_display.py`: standard run figures

Candidate models live in `forecasting/forecasting/traversal_models.py`, and agents change them freely (register new names in `make_traversal_model`). Candidate **anchor** models (first-stop departure delay) live in `forecasting/forecasting/anchor_models.py` (register in `make_anchor_model`).

## CLI

```
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split dev
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split test --i-have-owner-approval
```

Without the approval flag, `--split test` refuses to run. Pass `--hypothesis H1 --log-id E002` to tag the run and pre-fill its LOG row.

`--anchor` (default `feed`, Transit BC's earliest first-stop estimate) swaps the chain anchor $d_0$ of **B2, B3 and the candidate** for an anchor model trained per fold on observed first-stop departure delays: fresh final reports only (same stale rule as truth), published before the cutoff, from journeys disjoint from the block. The model sees only origin-time fields (first `stop_id`, its `calendar_departure`). **B0 and B1 always keep the feed anchor.** Predictions gain an `anchor_model_s` column, and the anchor is recorded as an MLflow param and tag (added for H6, owner-approved).

## Run tracking (MLflow)

Every run is logged to a local MLflow store (`data/mlflow/`, gitignored: SQLite `mlflow.db` plus `artifacts/`) as one run in the `hwy97_delay` experiment:

- **tags:** git hash, dirty flag, split, model, hypothesis, LOG ID, locked-test flag
- **params:** model, split and the flattened study config
- **metrics:** MAE/MedAE/RMSE/bias per method, Δ vs B0 with CI, win rate, and MAE per fold, period, stop and lead bin, plus control and criterion flags (0/1)
- **artifacts:** the files listed below

Browse runs with:

```
conda run -n transit_env mlflow ui --backend-store-uri sqlite:///data/mlflow/mlflow.db
```

(from the repo root, then open http://127.0.0.1:5000). MLflow is the run store. **LOG.md stays the narrative** (hypothesis, prediction before running, verdict) and cites the MLflow run ID. `--tracking files` writes the same files to `data/experiments/hwy97_delay/<UTC timestamp>_<model>_<split>/` instead, with offline figures.

Artifacts per run:

- `config.json`: CLI args, config, git hash, dirty flag, dataset checksums
- `predictions.csv.gz`: one row per scored visit with truth, B0 to B3 and candidate predictions, lead, fold, stop
- `metrics.json` and a printed table: primary/secondary metrics, paired Δ vs B0 with a day-bootstrap CI, per fold
- `audit.csv`: rows excluded at each stage, and why
- `figures/`: daily MAE and mean delay, error by hour of day, predicted-vs-observed scatter per method, and per-visit delay time series at the second and last stops (in MLflow they load Plotly from its CDN so they render in the UI; `--tracking files` keeps one local `plotly.min.js` for offline viewing)

It also prints a ready-to-paste LOG.md summary row.

## Model interface

```python
class TraversalModel(Protocol):
    def fit(self, visits: pd.DataFrame) -> "TraversalModel":
        """Training traversals: segment key, scheduled upstream clock (origin-feed), traversal_seconds, ..."""
    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Traversal seconds for (segment key, scheduled upstream clock) requests known at t0."""
```

The harness, not the model, applies the dwell rule, the anchor and the identity chaining. A model only sees what is available at `t0`.

## Pipeline

1. Load the window from R2 or the local snapshot (`get_backtest_dataset`), route `97-KEL`, direction 0. Event times are used as published (physical; see `forecasting/feed_clock.py` and issue #13). Each journey's **timetable offset** (start descriptor minus first-stop feed schedule, in whole hours) is measured; with `calendar_clock: "timetable"` the service day and the models' `scheduled_departure` feature use the timetable clock. Journeys whose offset isn't a whole number of hours are excluded (`schedule_clock_mismatch`), and the run stops if more than `max_clock_mismatch_share` of a month's journeys are. The per-month offset table is saved in `metrics.json` and MLflow.
2. Build dated journeys, consecutive stop pairs and the segment catalogue (reuse `prepare_segment_traversal_eda` and `prepare_early_traversal_comparison` logic).
3. For each journey, find `t0`, the origin-feed scheduled arrivals for every stop, the B0 estimate per stop, and the final truth.
4. Per fold: fit on visits whose final publication is before the cutoff, predict traversal for every segment of every evaluation journey, then chain from the anchor.
5. Score all methods on the same rows and run the leakage assertions (RESEARCH.md guardrails 1 to 5).

## Tests (`forecasting/tests/unit/test_delay_forecast_eval.py`)

- Chaining with known `T, W, S` reproduces the identity exactly.
- A synthetic journey with a publication after `t0` cannot influence features (leakage test).
- Fold cutoffs exclude visits whose final publication falls after the cutoff.
- Locked-test dates are refused without the flag.
- Identical row sets across methods; unscorable rows are counted.
