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
| [dynamics_model.ipynb](dynamics_model.ipynb) | How does arrival-delay accounting work on Highway 97 segment 4's directed stop pair? | Current-GTFS EDA plus a full-data dynamics explanation, per-visit feed-inferred scheduled allowances, delay time series, allowance distribution/time series and a 1:1 agreement scatter. No fitting, holdouts or competing approaches; exact agreement is circular, not forecast skill. A basis for future traversal/dwell prediction. |
| [segment_4_traversal_time_eda.ipynb](segment_4_traversal_time_eda.ipynb) | Which calendar and dynamic patterns should a segment 4 traversal-time model represent? | Separate offline full-cache timing EDA: eligibility/coverage, aggregation support, weekday/hour profiles, robust candidate episodes, residual drift and gap-preserving lag correlations. Recommends calendar-plus-dynamic-residual candidates and an as-of, chronological modelling methodology; no fitting or selected forecast horizon. |
| [segment_4_harmonic_traversal_regression.ipynb](segment_4_harmonic_traversal_regression.ipynb) | Can a calendar-only harmonic model learn typical segment 4 traversal time without following isolated spikes? | Custom sklearn daily/weekly Fourier transformer, Huber-loss L1 regression via weighted Lasso, ordinary Lasso and median comparisons, chronological development/final blocks, spike-injection sensitivity, and saved recurring patterns/residuals. |

## Recommended reading order

1. Start with the speed-based congestion analysis to understand segment
   geometry, observation support, replay freshness and the distinction between
   individual vehicle probes and complete bus traversals.
2. Read the delay-based congestion and propagation analysis to distinguish
   existing lateness from delay gained or recovered on a segment.
3. Run the recurring segment-delay prediction experiment to test the calendar
   hypothesis on held-out service days.
4. Use the dynamics notebook to inspect individual journeys and evaluate the
   exact segment 4 delay identity. Run its Preprocessing section first;
   EDA and Modelling can then be run independently. Per-visit inferred schedules,
   allowances and accounting values, allowance summaries, eligibility audit,
   metadata and an identity calculation check are saved as CSV alongside
   `segment_4_dynamics_identity_timeseries.html`,
   `segment_4_dynamics_identity_scatter.html`,
   `segment_4_dynamics_scheduled_allowances.html` and
   `segment_4_dynamics_scheduled_allowances_timeseries.html` under
   `visuals/dynamics_model_notebook/`.
   The allowance time series uses scheduled upstream arrivals in Vancouver
   local time, with one point per visit and day/week/month controls.
   This notebook pins the two-month cache and runs offline from either the
   repository root or the notebooks directory.
5. Use the segment 4 traversal-time EDA before fitting a road-duration model.
   It pins the same two-month cache and exact direction-0 endpoint IDs, but
   validates traversal independently of delays, dwell and inferred allowances.
   Historical trip IDs need not match current GTFS; consecutive numeric
   realtime sequences are required and historical shape is unconfirmed.
   Coverage uses a local 03:00-cutover service-day proxy; fixed-grid lags use UTC.
   All-sample calendar centring and MAD outlier scores are descriptive only.
   The notebook saves eight standalone HTML figures and CSV evidence under
   `visuals/segment_4_traversal_time_eda/`, overwriting only its own exports.
   It proposes per-bus or next-window forecasting, train-only preprocessing,
   label-receipt guards and blocked validation. Because EDA inspects the full
   cache, a genuinely blind final evaluation needs an untouched future period.
6. Run the harmonic traversal notebook to fit the recurring component only.
   It uses the same offline cache and endpoint identity, low-order local
   daily/weekly harmonics and interactions, and log-duration targets. Huber
   loss bounds spike amplitude influence while the L1 penalty shrinks
   unnecessary harmonics; valid slow journeys are retained. Two seven-day
   development blocks select orders/alpha before an exploratory final week,
   with 28 observed training days, a one-day embargo and both-endpoint label
   publication guards. Saved pipelines predict **log minutes**; exponentiate
   once. New bundles under `data/segment_harmonic_models/` contain provenance,
   splits, scores, fitting weights, profiles, spike sensitivity, residuals
   and self-contained HTML figures, including separate in-sample training
   and reserved-week traversal time series. A separate paired comparison uses
   the first downstream and as-of upstream reports to invert the delay identity
   into a zero-dwell traversal estimate. The frozen harmonic model predicts at
   the early-implied scheduled upstream time, never the final departure. Both
   methods are scored on the same eligible reserved-week visits against latest
   endpoint traversal with observed dwell removed. Origin guards, coverage,
   schedule revisions, paired errors and daily scores are reported; latest
   feed values remain reference proxies, not independent ground truth.
   Training comparisons are shown separately as in-sample diagnostics. Both
   comparison time series include a second row for minutes between earliest
   publication and the early-implied scheduled downstream arrival. Two
   method-specific prediction-versus-reference scatter figures separate
   training and reserved-week panels and use prediction on X, reference on Y.
   Each scatter panel reports its own count, MAE, median absolute error, RMSE
   and signed bias (prediction minus reference); error metrics are in minutes.
   All eight plots are also exported to
   [`visuals/segment_4_harmonic_traversal_regression/`](visuals/segment_4_harmonic_traversal_regression/);
   subsequent runs update only this notebook's named HTML files there while
   experiment bundles remain archived. This is not an operational as-of replay
   or an incident predictor. Train a separate residual/spike component using
   out-of-fold calendar predictions and independently available inputs.

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
- **Retrospective delay identity:** downstream delay equals upstream delay
  plus completed traversal and upstream dwell minus the scheduled allowance.
  Infer each endpoint's schedule as its reported arrival time minus delay.
  Pair exact directed stop IDs and consecutive numeric sequences within a
  reliably dated realtime journey, without requiring a historical trip ID to
  match current GTFS. Historical shape identity is unconfirmed. No coefficients
  or hourly baselines are fitted; all eligible visits are shown together without
  holdouts or competing approaches. Same-visit allowance
  inference uses the downstream outcome, guaranteeing agreement by construction.
  The separate identity-check notebook retains an independent GTFS comparison.
  Future forecasts must use an independently available scheduled allowance
  with predicted traversal/dwell, not an allowance derived from the target
  visit's downstream outcome.

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
