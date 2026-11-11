# Transit analysis notebooks

These notebooks explore whether bus observations can describe congestion and
help predict delays. They progress from exploratory prototypes to audited
historical replay and a separate out-of-time modelling experiment. They are
research workflows, not a deployed arrival-prediction service.

## Choose a notebook

| Notebook | Question and purpose | Main results |
| --- | --- | --- |
| [database_table_storage_profile.ipynb](database_table_storage_profile.ipynb) | How much storage do the realtime MySQL tables use? Runs `ANALYZE TABLE` and reads data-plus-index sizes from `information_schema`. | Database table storage sizes, not the size of a filtered backtest snapshot. |
| [transit_forecasting_prototype.ipynb](transit_forecasting_prototype.ipynb) | How could vehicle positions, schedules and reported delays support forecasting? Broad prototype covering maps, bus animations, speed-based congestion, stop-delay case studies and an arrival-delay model comparison. | Exploratory plots and baseline-versus-congestion-feature backtests. Prefer the focused notebooks below for the package-backed analyses. |
| [route_97_speed_congestion_analysis.ipynb](route_97_speed_congestion_analysis.ipynb) | What do vehicle speed probes say about Route 97 congestion, and could upstream congestion explain remaining stop-delay prediction error? | Directed segment geometry, speed comparisons, fresh/stale maps, animations, probe support and stop-delay feasibility dashboards. |
| [route_97_delay_congestion_and_propagation.ipynb](route_97_delay_congestion_and_propagation.ipynb) | Where do Route 97 buses gain or recover delay, and do unusual delays persist locally or propagate? | Audited stop-to-stop delay changes, coverage, replay maps, event responses, same-bus carry-over and time-space diagrams. |
| [route_97_recurring_segment_delay_prediction.ipynb](route_97_recurring_segment_delay_prediction.ipynb) | Can scheduled time-of-day and weekday patterns predict recurring signed segment delay changes? | Calendar-average and XGBoost comparisons, blocked validation, a final held-out week, tail diagnostics and saved model/evaluation bundles. |

## Recommended reading order

1. Start with the speed-based congestion analysis to understand segment
   geometry, observation support, replay freshness and the distinction between
   individual vehicle probes and complete bus traversals.
2. Read the delay-based congestion and propagation analysis to distinguish
   existing lateness from delay gained or recovered on a segment.
3. Run the recurring segment-delay prediction experiment to test the calendar
   hypothesis on held-out service days.

The database-size notebook is an optional capacity check. The broad prototype
provides development context but is not required by the other notebooks.
The analytical notebooks share package APIs and saved datasets; running one
does not initialize another.

## Environment and inputs

From the repository root, install the project and analysis dependencies in the
`transit_env` Conda environment:

```bash
conda run -n transit_env python -m pip install -r requirements.txt
conda run -n transit_env python -m pip install -r forecasting/requirements.txt
conda run --no-capture-output -n transit_env jupyter lab
```

Select the `transit_env` kernel and keep the congestion notebooks' working
directory set to `notebooks` so their package imports resolve. The prediction
notebook also bootstraps imports when run from the repository root. Run setup
and configuration cells before the analysis. The focused notebooks use utilities from
[`forecasting/forecasting/`](../forecasting/forecasting/); reusable ingestion,
transformations, fitting and persistence belong in packages rather than new
notebook-local helper frameworks.

### Historical analysis

The three focused analytical notebooks use trusted snapshots under
`data/backtests/<dataset_id>/`. Each snapshot contains vehicle updates, trip
updates, static GTFS and a manifest with coverage and checksums. These local
datasets are not included in Git.

- **Cache mode** loads the selected snapshot offline, without credentials or
  replacement downloads. A missing or corrupt snapshot raises an error.
- **Download mode**, available in the two congestion notebooks, explicitly
  queries MySQL, fetches current static GTFS and saves a new snapshot. Choose an
  unused dataset ID or `None` for automatic naming; existing IDs are not
  overwritten. Route, direction and lookback filters are bound through the SQL
  parameter dictionaries. Downloads may be large.
- The prediction notebook pins `2026-11-10_2_months` and only uses cache mode.
  Its initial configuration requires at least 21 observed training service
  days, two seven-day development blocks, one-day gaps and a final seven-day
  holdout. Sparse static-schedule matches mean a nominal two-month download is
  not two months of complete modelling coverage.

Inspect the dataset catalog, manifest and exclusion/coverage tables before
interpreting results. Cache selection determines the evidence; changing
download SQL does not filter an already saved snapshot. The propagation
notebook's written findings identify their original dataset and should not be
treated as conclusions for a newly selected snapshot.

The two legacy notebooks query MySQL directly and assume additional local
files and connection settings. They are not offline replay workflows. The
storage-size notebook runs `ANALYZE TABLE`, so it is not a read-only inspection
and should only be run against an appropriate database.

All legacy database connection cells read the required password from
`TRANSIT_DB_PASSWORD`; no password is embedded in notebook source. Export it
in the shell before launching Jupyter so the kernel inherits it, or source
the project's ignored database environment file. A missing variable raises
`KeyError` before any database connection is attempted. The host, username,
database and port remain explicit in each cell; review these settings before
running against a different database. Never put credentials in notebook cells
or saved outputs.

## What the signals mean

- **Probe speed:** a reported instantaneous vehicle speed. Time-binned means
  can contain repeated updates from one bus. The speed engine compares them
  with a full-period median probe-speed baseline.
- **Traversal speed:** shape-path distance divided by downstream arrival minus
  upstream departure, excluding upstream dwell. One marker represents a
  completed bus/trip-instance traversal. Its comparison baseline is the median
  traversal speed, not the engine's probe-speed baseline.
- **Segment delay change:** final reported downstream arrival delay minus
  upstream arrival delay, in seconds. Positive means added delay; negative
  means recovery. Already being late is not itself segment congestion.
- **Recurring-delay forecast:** signed segment delay change predicted from
  scheduled upstream arrival, weekday and directed segment identity. It does
  not forecast unexpected incidents or consume actual endpoint delays as
  inputs.

Final reported times and delays are reference proxies, not independently
measured arrivals. Delay changes include dwell, holding, schedule slack and
prediction revisions; none of these signals isolates general road traffic.
Missing observations remain unknown, not zero. UTC is used for replay;
Vancouver local time is used for calendar features and readable plots.

The speed-based feasibility analysis retains full-history baselines and is
not a fully leakage-free forecast backtest. The prediction experiment uses
past-only training and label-availability guards, but a few held-out weeks do
not establish operational benefit or causality. Statistical tail bands are
diagnostics, not verified incident labels.

## Outputs and supporting tools

Congestion notebooks write interactive HTML figures to `notebooks/visuals/`.
The prediction notebook saves new, non-overwriting experiment directories
under `data/segment_delay_models/`, including model state, dataset provenance,
configuration, splits, predictions and metrics. These generated outputs stay
out of Git. Only load pickle snapshots and model bundles from trusted sources.

Database download scaling is a command-line experiment, not an additional
notebook:

```bash
conda run --no-capture-output -n transit_env python -m forecasting.benchmark_download_speed
```

It measures realtime database downloads and writes CSV/HTML results to
`data/download_benchmarks/`; it excludes static GTFS downloading and snapshot
serialization. See the [project README](../README.md) for its controls and
detailed analysis assumptions.

Validate package changes without querying the live database:

```bash
cd forecasting
conda run -n transit_env python -m pytest tests/unit -q
```
