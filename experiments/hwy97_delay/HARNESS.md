# Evaluation harness spec (iteration 0 builds this)

The harness is the loop's verifier. Once the owner has approved it, it is **protected**: agents must not edit these files without asking.

- `forecasting/forecasting/delay_forecast_eval.py`: data preparation, splits, chaining, scoring
- `experiments/hwy97_delay/evaluate.py`: CLI entry point
- `experiments/hwy97_delay/config.json`: dataset window, fold dates, locked-test dates, holidays, stale-report threshold
- `forecasting/forecasting/delay_forecast_display.py`: standard run figures

Candidate models live in `forecasting/forecasting/traversal_models.py`, and agents change them freely (register new names in `make_traversal_model`). Candidate **anchor** models (first-stop departure delay) live in `forecasting/forecasting/anchor_models.py` (register in `make_anchor_model`). Candidate **dwell** models live in `forecasting/forecasting/dwell_models.py` (register in `make_dwell_model`; added for H7, owner request 2026-11-27).

## CLI

```
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split dev
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split test --i-have-owner-approval
```

Without the approval flag, `--split test` refuses to run. Pass `--hypothesis H1 --log-id E002` to tag the run and pre-fill its LOG row.

`--anchor` (default `feed`, Transit BC's earliest first-stop estimate) swaps the chain anchor $d_0$ of **B2, B3 and the candidate** for an anchor model trained per fold on observed first-stop departure delays: fresh final reports only (same stale rule as truth), published before the cutoff, from journeys disjoint from the block. The model sees only origin-time fields (first `stop_id`, its `calendar_departure`). **B0 and B1 always keep the feed anchor.** Predictions gain an `anchor_model_s` column, and the anchor is recorded as an MLflow param and tag (added for H6, owner-approved). **Study runs use the default `feed`**: the owner put first-stop departure delay out of scope on 2026-11-21 (see RESEARCH.md Scope), so a learned anchor is for later real-time work, not for this study's scored runs.

`--dwell` (default `stop_median`, the per-stop training median, which reproduces E017 exactly) sets the dwell model of **B2, B3, the candidate and the control**. It is fitted once per fold on the same training links as the traversal models. **B0 and B1 never use it.** A dwell model that sets `uses_arrival_delay` (e.g. `stop_hold`, a per-stop hold floor learned from training links) receives the chain's own predicted arrival delay at each stop, so the harness chains stop by stop (`chain_with_dwell_model`) instead of with one cumulative sum. For such a model, the anchor-free score re-chains each method from the actual first-stop departure (`anchor_free_pred_<method>` columns), because a shift is no longer exact. The dwell model is recorded as an MLflow param and tag and in the LOG row.

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

## Anchor-free score (secondary)

Each scored row carries two evaluation-only columns, never used as features: `actual_departure_delay_s` (the journey's final first-stop departure minus its feed schedule) and `chain_anchor_delay_s` (the anchor the chained methods started from). `score_predictions` adds an `anchor_free` block: every chained method's prediction is shifted by `chain_anchor_delay_s − actual_departure_delay_s`, and B1 becomes the actual departure delay. It reports `summary`, `by_fold`, `by_stop` and `paired_vs_B3` (day-bootstrap CI). B0 is excluded. MLflow metrics are prefixed `anchor_free_`; the printed output and the LOG row show the candidate's anchor-free MAE. The success criterion is unchanged (RESEARCH.md).

## Model interface

```python
class TraversalModel(Protocol):
    def fit(self, visits: pd.DataFrame) -> "TraversalModel":
        """Training traversals: segment key, scheduled upstream clock (origin-feed), traversal_seconds, ..."""
    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Traversal seconds for (segment key, scheduled upstream clock) requests known at t0."""
```

The harness, not the model, applies the anchor and the identity chaining. A traversal model only sees what is available at `t0`.

```python
class DwellModel(Protocol):
    uses_arrival_delay: bool
    def fit(self, links: pd.DataFrame) -> "DwellModel":
        """Training links: from_stop_id, upstream_dwell_seconds, upstream_arrival_delay_seconds, scheduled_dwell_seconds, ..."""
    def predict(self, requests: pd.DataFrame, arrival_delay_seconds: np.ndarray | None = None) -> np.ndarray:
        """Dwell seconds at each request's upstream stop (from_stop_id, scheduled_departure, scheduled_dwell_seconds, upstream_is_first_stop), given the chain's predicted arrival delay there."""
```

Dwell at a journey's first stop is always 0 (the anchor is the departure).

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
