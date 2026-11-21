# Documentation

Reference docs for the data workflows and analyses in this repository. Start
with the [project README](../README.md) for setup, and the
[notebook guide](../notebooks/README.md) for which notebook to read first and
what each signal means.

## Data

- [Historical backtest data](backtest-data.md): offline snapshots under
  `data/backtests/`, the R2 Parquet archive, custom MySQL downloads and the
  `get_backtest_dataset` API.
- [Database download scaling benchmark](download-benchmark.md): the
  `forecasting.benchmark_download_speed` command, its outputs and how to
  interpret the scaling plot.

## Analyses

Each page describes the method, the public functions it uses and its
interpretation limits.

- [Congestion maps](congestion-maps.md): delay-change and speed-based
  congestion engines, snapshot replay, smoothing and map rendering.
- [Stop-delay congestion feasibility](stop-delay-feasibility.md): BC Transit
  prediction error compared against upstream congestion at one stop.
- [Recurring segment delay prediction](segment-delay-prediction.md): calendar
  models of segment delay change, splits, baselines and saved runs.
- [Stop dynamics feasibility EDA](stop-dynamics-eda.md): a single-segment
  view of probe speed and arrival delays.

## Process

- [Agent research loop](agent-research-loop.md): how research studies under
  `experiments/` are run and logged.
