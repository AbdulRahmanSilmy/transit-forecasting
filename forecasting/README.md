# forecasting

The `forecasting` package (distribution name `transit-forecasting`): loading
historical realtime data, congestion and delay analyses, traversal models and
the evaluation harness for research studies. Notebooks and study scripts
import from it, so reusable logic lives here rather than in notebook cells.

- Loading data: [docs/backtest-data.md](../docs/backtest-data.md)
- What the data means and its quirks: [docs/gtfs-rt-data.md](../docs/gtfs-rt-data.md)
- Analyses built on this package: [docs/README.md](../docs/README.md)

## Install

```bash
conda activate transit_env
cd forecasting
python -m pip install -r requirements.txt     # the package (editable) plus Jupyter, widgets and MLflow
```

[pyproject.toml](pyproject.toml) lists the direct runtime dependencies, with
extras `test` (pytest) and `tracking` (MLflow).
[requirements.txt](requirements.txt) adds what notebooks and study runs need.

## Tests

Run from this directory. Running from the repository root fails on imports.

```bash
cd forecasting
conda run -n transit_env python -m pytest tests/unit -q
```

Tests for a module go in `tests/unit/test_<module>.py`.
`test_feed_clock.py` also fails if the environment's timezone database
predates British Columbia's switch to permanent UTC−7 (see
[issue #13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13)).

## Module map

Modules in [forecasting/](forecasting). Modules ending in `_display` hold the
Plotly figures for the matching analysis module. Public functions are
exported from [\_\_init\_\_.py](forecasting/__init__.py).

| Area | Modules | Purpose |
|---|---|---|
| Data access | `backtest_data`, `backtest_cache`, `r2_archive`, `static_gtfs`, `benchmark_download_speed` | Load, cache and benchmark historical realtime data (R2 Parquet, MySQL, local snapshots) and static GTFS |
| Feed utilities | `trip_updates`, `feed_clock` | Select trip updates and match them to vehicle positions; measure each journey's offset between feed schedule and timetable |
| Hwy 97 study harness (**protected**) | `delay_forecast_eval`, `delay_forecast_display`, `experiment_tracking` | Origin-time delay forecasting harness, its run figures and MLflow logging. Do not edit without owner approval (see [HARNESS.md](../experiments/hwy97_delay/HARNESS.md)) |
| Study exploration | `exploration_tracking` | Exploratory-plot sessions logged to MLflow (`experiment-plots` skill); not part of the protected harness |
| Study candidates | `traversal_models`, `anchor_models`, `harmonic_regression` | Traversal-time models (register in `make_traversal_model`), first-stop anchor models (register in `make_anchor_model`) and the calendar harmonic regressor they use |
| Congestion | `road_segment_congestion`, `road_segment_delay_congestion`, `congestion_service`, `congestion_display`, `delay_congestion_display` | Speed- and delay-based segment congestion engines and maps |
| Delay propagation | `delay_propagation`, `delay_propagation_display` | Whether segment delay anomalies stay local or spread along the route |
| Segment delay and traversal | `segment_delay_model`, `segment_traversal_speed`, `segment_traversal_comparison`, `segment_traversal_comparison_display` | Recurring segment delay-change models, full-segment speeds, origin-time vs final traversal comparisons |
| Stop and trip dynamics | `stop_dynamics_eda`, `stop_dynamics_model`, `stop_dynamics_display`, `trip_dynamics_eda`, `trip_dynamics_display`, `stop_congestion_analysis`, `stop_congestion_display` | Single-segment and full-journey delay, traversal and dwell EDA, and retrospective models |

## Conventions

- NumPy-style docstrings on public functions and methods.
- Functions do not modify their input frames unless that is intended and
  tested.
- Direct runtime dependencies go in `pyproject.toml`; notebook-only ones go in
  `requirements.txt`.
- New tests cover normal, boundary and missing-data cases.
