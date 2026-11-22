---
name: forecasting-development
description: "Use when implementing, debugging, testing, or documenting the forecasting Python package, including transit prediction utilities, feature preparation, model code, and forecasting tests."
---

# Forecasting Development

Use this skill for production-code changes in `forecasting/`.

## Package map

- Python package: `forecasting/forecasting/`
- Package metadata and runtime dependencies: `forecasting/pyproject.toml`
- Notebook and analysis dependencies: `forecasting/requirements.txt`
- Unit tests: `forecasting/tests/unit/` (one `test_<module>.py` per module)

## Module map (`forecasting/forecasting/`)

Modules ending in `_display` hold the Plotly figures for the matching analysis module.

| Area | Modules | Purpose |
|---|---|---|
| Data access | `backtest_data`, `backtest_cache`, `r2_archive`, `static_gtfs`, `benchmark_download_speed` | Load, cache and benchmark historical realtime data (R2 Parquet, MySQL, local snapshots) and static GTFS |
| Feed utilities | `trip_updates`, `feed_clock` | Select and associate trip updates; measure the trip-label vs timetable clock offset (issue #13) |
| Hwy 97 study harness (**protected**) | `delay_forecast_eval`, `delay_forecast_display`, `experiment_tracking` | Origin-time delay forecasting harness, its run figures and MLflow logging. Do not edit without owner approval (see `experiments/hwy97_delay/HARNESS.md`) |
| Study exploration | `exploration_tracking` | Iterative exploratory-plot sessions logged to MLflow (`experiment-plots` skill); not part of the protected harness |
| Study candidates | `traversal_models`, `harmonic_regression` | Traversal-time models for the harness (register in `make_traversal_model`); the calendar harmonic regressor they use |
| Congestion | `road_segment_congestion`, `road_segment_delay_congestion`, `congestion_service`, `congestion_display`, `delay_congestion_display` | Speed- and delay-based segment congestion engines and maps |
| Delay propagation | `delay_propagation`, `delay_propagation_display` | Whether segment delay anomalies stay local or spread |
| Segment delay and traversal | `segment_delay_model`, `segment_traversal_speed`, `segment_traversal_comparison`, `segment_traversal_comparison_display` | Recurring segment delay-change models, full-segment speeds, origin-time vs final traversal comparisons |
| Stop and trip dynamics | `stop_dynamics_eda`, `stop_dynamics_model`, `stop_dynamics_display`, `trip_dynamics_eda`, `trip_dynamics_display`, `stop_congestion_analysis`, `stop_congestion_display` | Single-segment and full-journey delay/traversal/dwell EDA and retrospective models |

## Procedure

1. Check for an existing package utility before introducing equivalent notebook logic.
2. Keep reusable forecasting behavior in the package and expose intended public functions from `forecasting/forecasting/__init__.py`.
3. Declare direct runtime dependencies in `pyproject.toml`; keep notebook-only dependencies in `requirements.txt`.
4. Document Python functions with NumPy-style docstrings and preserve input frames unless mutation is an intentional, tested behavior.
5. Add focused pytest coverage for normal, boundary, and missing-data cases.
6. Run tests with the `transit_env` Conda environment from the forecasting project directory, for example:
   `cd forecasting && conda run -n transit_env python -m pytest tests/unit -q`
