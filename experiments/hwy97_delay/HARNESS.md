# Evaluation harness spec (iteration 0 builds this)

The harness is the loop's verifier. Once the owner has approved it, it is **protected**: agents must not edit these files without asking.

- `forecasting/forecasting/delay_forecast_eval.py`: data preparation, splits, chaining, scoring
- `experiments/hwy97_delay/evaluate.py`: CLI entry point
- `experiments/hwy97_delay/config.toml`: dataset window, fold dates, locked-test dates, holidays

Candidate models live in `forecasting/forecasting/traversal_models.py` (or similar), and agents change them freely.

## CLI

```
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split dev
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber --split test --i-have-owner-approval
```

Without the approval flag, `--split test` refuses to run. Every run writes `data/experiments/hwy97_delay/<UTC timestamp>_<model>/` containing:

- `config.json`: CLI args, config, git hash, dirty flag, dataset checksums
- `predictions.parquet`: one row per scored visit with truth, B0 to B3 and candidate predictions, lead, fold, stop
- `metrics.json` and a printed table: primary/secondary metrics, paired Δ vs B0 with a day-bootstrap CI, per fold
- `audit.csv`: rows excluded at each stage, and why

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

1. Load the window from R2 or the local snapshot (`get_backtest_dataset`), route `97-KEL`, direction 0.
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
