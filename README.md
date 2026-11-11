# Transit Forecasting

## Overview
Tools and data pipelines for transit forecasting and GTFS-realtime ingestion.

## Notebooks

See the [notebook guide](notebooks/README.md) for each analysis's purpose,
recommended reading order, inputs, outputs and interpretation limits.

## Development Setup

### Python Environment
Use your preferred Python environment manager (conda, venv). Example with conda:

```
conda create -n transit_env python=3.11
conda activate transit_env
```

Install Python dependencies:

```
pip install -r requirements.txt
```

The root requirements file includes the data ingestion requirements.

Use `pytest` for tests.

### Pre-commit Hooks
Install the git hook:

```
pre-commit install
```

## Historical Backtests

The [road-segment congestion notebook](notebooks/route_97_speed_congestion_analysis.ipynb)
stores complete local datasets instead of repeatedly querying the database:

```text
data/
  backtests/
    <dataset_id>/
      metadata.json
      vehicle_updates.pkl
      trip_updates.pkl
      static_gtfs/
        stops.txt
        stop_times.txt
        trips.txt
        shapes.txt
        ...other available GTFS tables and archive
  static_gtfs_cache/     # Content-addressed source archives for new downloads
```

In **Download backtest data**, set:

- `backtest_source = 'cache'` to replay offline, without database credentials.
  Leave `backtest_dataset_id = None` to use the newest saved snapshot, or set
  it to an ID from the displayed catalog to select any historical dataset.
- `backtest_source = 'download'` to pull and save a new dataset. Set
  `lookback_hours` in each query's parameter dictionary as needed, then switch
  back to `'cache'` for reruns. Set `backtest_dataset_id` to an unused name for
  the new snapshot, or `None` for an automatic UTC timestamp ID. An existing
  name raises `FileExistsError` before credentials are read or any database or
  network download begins. Invalid directory names raise `ValueError` just as
  early. Names may contain letters, digits, dots, dashes, and underscores and
  must start with a letter or digit.
  This mode alone reads database credentials and fetches current static GTFS.

Each dataset has UTC data bounds, row counts, creation time, source, requested
lookback, and file checksums in its manifest. Catalog order is by save time, not
the historical period. Dataset IDs are immutable: saving an existing ID fails
rather than overwriting it. Missing/corrupt files raise an error; no fresh data
is silently substituted. Static GTFS is copied into the dataset so historical
reruns do not fetch a potentially incompatible current schedule.

The notebook delegates dataset selection, credentials, downloading, and caching
to `get_backtest_dataset`, which returns a `BacktestDataset` with named
`vehicle_updates`, `trip_updates`, `directory`, and `static_data_dir` attributes:

```python
from forecasting import get_backtest_dataset

dataset = get_backtest_dataset("data", source="cache", dataset_id=None)
```

In download mode, the optional `env_file` supplies shell-style `TRANSIT_DB_*`
settings; otherwise the existing environment is used. Cache mode never reads
credentials. File settings do not modify the process environment.

Download mode also accepts optional `vehicle_query` and `trip_query` SQL strings.
Pass each query's named values separately through `vehicle_query_params` and
`trip_query_params`. Values are bound by the database driver using named
placeholders, so each query can have its own parameter names and values. For
example, download a week for Route `97-KEL`, direction `0`:

```python
vehicle_query = """
SELECT *
FROM TRANSIT_VEHICLE_TABLE
WHERE feed_timestamp >= NOW() - INTERVAL %(lookback_hours)s HOUR
  AND trip_route_id = %(route_id)s
  AND trip_direction_id = %(direction_id)s
ORDER BY id DESC
"""
vehicle_query_params = {
    "lookback_hours": 7 * 24,
    "route_id": "97-KEL",
    "direction_id": 0,
}
trip_query = vehicle_query.replace("TRANSIT_VEHICLE_TABLE", "TRANSIT_TRIP_TABLE")
trip_query_params = {
    "lookback_hours": 7 * 24,
    "route_id": "97-KEL",
    "direction_id": 0,
}
dataset = get_backtest_dataset(
    "data",
    source="download",
    dataset_id="route97_direction0_week",
    env_file=".env.road_segment_congestion",
    vehicle_query=vehicle_query,
    vehicle_query_params=vehicle_query_params,
    trip_query=trip_query,
    trip_query_params=trip_query_params,
)
```

Omitting either query, or passing `None`, keeps that table's existing all-route
query. Its parameter dictionary defaults to `{"lookback_hours": 48}`; supplying
one explicitly requires a positive integer `lookback_hours`. The same query
and dictionary arguments are available on `download_backtest_data`, whose
default queries retain their 3-hour window. A custom query receives only its
supplied parameters; use `None` or `{}` for SQL without placeholders. These
APIs no longer accept a separate `lookback_hours` argument.

Custom queries must return `feed_timestamp` to save a snapshot and the columns
needed by the analysis. The manifest's requested lookback is recorded only
when both dictionaries contain the same positive integer `lookback_hours`;
otherwise it is null, while actual UTC data bounds remain available for each
table. SQL and parameter overrides only affect new downloads; cache mode
loads the selected snapshot as saved.

Lower-level `save_backtest_dataset`, `load_backtest_dataset`, and
`list_backtest_datasets` utilities remain available from `forecasting`. Pickle
snapshots preserve DataFrame dtypes and order, but must only be loaded from
trusted sources. All of `data/` remains ignored by Git; no credentials are
stored in snapshots.

## Database Download Scaling Benchmark

The [download benchmark](forecasting/forecasting/benchmark_download_speed.py)
uses the same filtered realtime download pattern as the
[speed-based congestion notebook](notebooks/route_97_speed_congestion_analysis.ipynb):
Route `97-KEL`, direction `0`, both realtime tables, all columns, and
`ORDER BY id DESC`. Run from the repository root:

```bash
conda run --no-capture-output -n transit_env python -m forecasting.benchmark_download_speed
```

It reads the same ignored `.env.road_segment_congestion` file or existing
`TRANSIT_DB_*` variables. Default windows are **1, 2, 4, 8, 12, 24, 48, 72, 96,
120, 144, and 168 hours**, with three full downloads per window. The first
sweep increases in size; subsequent sweeps use a reproducible shuffled order
to reduce systematic ordering effects. This can transfer substantial data
and load the database; it never changes data or flushes server caches.
For a shorter single-sweep run:

```bash
conda run --no-capture-output -n transit_env python -m forecasting.benchmark_download_speed \
  --hours 1 6 24 72 168 --repeats 1
```

Results go to a new timestamped directory under `data/download_benchmarks/`:

- `timings.csv`: every trial, including both row counts, elapsed seconds,
  filters, and fixed server-time window bounds. Completed trials are flushed
  after each download, so a failed/interrupted run preserves earlier timings.
- `summary.csv`: medians, observed minimum/maximum timings, and time per row.
- `scaling.html`: self-contained interactive plot; open it in a browser.
  Override the destination with `--output-dir <new-directory>`; existing
  directories are rejected to protect previous results.

The timer covers the same `download_backtest_data` path as the notebook:
connection setup, both SQL queries, sorting, network transfer, DataFrame
construction, and connection close. It excludes static GTFS downloads,
snapshot serialization, CSV writes, and plotting. Each window ends at the
same database `NOW()` captured before timing. Normal live ingestion cannot
extend that endpoint, although late backfills/deletions can change counts.
Only one pair of DataFrames is kept in memory at a time.

Use **returned vehicle + trip rows**, not hours, as n: transit activity varies
by hour. The plot compares median elapsed time with an O(n) line anchored at
the smallest nonempty window, and shows milliseconds per row. Sustained
growth in time per row is evidence of superlinear behavior over these sampled
sizes. A descriptive log-log exponent p above 1 is another indication, not
proof of asymptotic complexity or statistical significance. Bars show observed
min/max, not confidence intervals. Fixed connection overhead, server load,
cache warming, different table mixes, and query plans can affect results.
Empty windows are reported and excluded from per-row/exponent calculations;
an all-empty run raises an error rather than producing a misleading plot.

## Congestion Maps

### Congestion via arrival-delay changes

The [delay-based notebook](notebooks/route_97_delay_congestion_and_propagation.ipynb)
reuses the original notebook's cached-backtest workflow for Route `97-KEL`
and direction `0`, without changing the speed-based analysis. Select the
saved snapshot explicitly with `backtest_dataset_id` in the notebook.
`run_delay_congestion_engine` subtracts the latest reported upstream
`arrival_delay` from the downstream value within the same trip instance.
Positive seconds mean added delay, zero means no additional delay, and negative
seconds mean recovery relative to schedule. Existing lateness is not itself a
segment congestion signal. These are final reported **proxies**, not independently
observed arrivals or pure road traffic: dwell, holding, schedule slack, and
prediction revisions can influence the difference.

The engine reuses directed, shape-specific GTFS segment geometry. It keeps one
latest row per trip instance/stop/sequence (publication time, then greatest
database ID; first input row otherwise), preserving missing latest estimates.
`get_latest_stop_delays` also supports omitting `stop_name` to select all route
stops; unknown stop IDs are retained for auditing. Missing trip starts are
explicitly excluded from the delay engine to avoid ambiguous daily/midnight
pairing. Missing, unmatched, unfinished, and temporally reversed endpoints are
audited, not substituted or bridged across missing stops.

`DelayCongestionResult` exposes `stop_visits`, candidate `traversals`,
`congestion`, `audit`, and segment geometry. Each usable traversal contributes
once to its downstream proxy arrival's five-minute UTC bin. Bins contain median
signed delay change, distinct traversal count, spread, and sign shares. Single
traversals are low-support observations; their standard deviation is undefined.
Unobserved segments remain unknown rather than receiving invented zero values.

`prepare_delay_congestion_snapshots` waits for every contributing endpoint's
publication/receipt and proxy crossing before exposing a final bin. Missing
trip-update receipt timestamps use publication time with an explicit warning;
the legacy snapshot therefore supports publication-time, not exact receipt-time,
replay. Late backfilled bins cannot replace newer crossed bins. The provisional
15-minute expiry measures age from the last contributing proxy arrival, not
publication. Optional trailing smoothing uses available actual bins once and
never extends freshness or pools shapes. Equivalent directed geometries use
the freshest shape-specific source with deterministic segment-ID tie breaking.

`make_delay_congestion_map` shows signed seconds, with green for recovery,
yellow for zero, red for accumulation, and grey for stale/unknown readings.
The symmetric colour saturation limit is visual only; hover values are not
clipped. The notebook includes a trip decomposition, raw/smoothed animations,
and an exact-shape segment time series with retrospective crossing-bin counts.
The latest map stays anchored at the recording end even when coverage has
expired; it never silently substitutes an earlier good reading.

### Speed-based congestion replay

The notebook keeps two separate speed-comparison plots. The original shows
time-binned mean probe speeds against the engine's median individual probe-speed
baseline. Select its segment with `probe_speed_segment_id`; a marker represents
a time bin, not an individual bus.

The per-bus plot uses `get_segment_traversal_speeds` to show one marker
per completed bus/trip-instance traversal, at its final reported downstream
arrival. Select its segment independently with `speed_segment_id`.
Speed is the segment's shape-path distance divided by the time from
upstream departure to downstream arrival, excluding upstream dwell. Hover labels
identify the bus, trip instance, path distance, and travel time. Missing final
times, ambiguous bus identities, and unfinished traversals are excluded with
warnings, never replaced with older predictions or instantaneous speed averages.
The comparison baseline is the median of these full-traversal speeds for the
selected directed segment and shape; it does not change the congestion engine's
probe-speed baseline. Both stop-time estimates and shape-derived distances are
proxies, and the full-period traversal median is not a past-only forecast.

`prepare_congestion_snapshots` selects the latest already-observed congestion
estimate at each requested UTC timestamp. An aggregate becomes available at its
last contributing vehicle probe, not at the start of its five-minute bin.
History preceding the display window can initialize a frame, but estimates expire
after `max_age` (inclusive). The notebook's provisional default is 15 minutes;
calibrate this against actual observation gaps. Full-history engine speed
baselines are unchanged, so this visualization is not a causal baseline backtest.

`make_congestion_map` uses the same rendering for static and animated snapshots.
The latest map and final raw animation frame share their timestamp and freshness
settings. Hover labels show the actual observation time and age; an old estimate
is never described as current. Neither function modifies engine results.

For display only, identical geometries sharing the same direction and directed
stop pair are represented once. The freshest eligible shape-specific estimate
wins; equal observation times are resolved by the lowest engine segment ID.
Hover labels retain group membership and the selected source segment/shape.
Missing or inactive duplicates cannot cover an observed road section. Engine
values and baselines remain separate: different directions, stop pairs, or paths
are never combined, even when some of their geometry overlaps.

Hover states distinguish fresh estimates, expired estimates, no matched probes
yet, matched probes without an eligible congestion baseline, and inactive service
variants. Schedule activity comes from `get_congestion_service_activity`, using
the selected snapshot's agency timezone, weekly calendar, dated additions and
removals, and GTFS times beyond 24:00. Counts indicate trips whose scheduled
interval overlaps the selected period, not the exact time a bus crosses each
segment. Missing or invalid schedule metadata raises an error rather than
silently labeling service inactive. Without schedule classification, hover text
explicitly says activity was not evaluated.

The notebook displays the original per-segment activity table for inspection,
including inactive duplicate shapes hidden by an observed equivalent road.
Static GTFS segments are retained even when inactive in the selected period;
actual observations always take precedence over schedule-based status.

Set `smoothing_window` on `prepare_congestion_snapshots` to smooth actual
segment/bin estimates with a trailing time window before selecting display
frames. Each observation contributes once; repeated carried-forward frames do
not add weight. The window is open on the left and closed at the observation
timestamp. The notebook uses 50 minutes rather than a centered 10-frame mean.
Smoothing is performed separately for each engine segment, before equivalent
paths choose their freshest source, and never advances observation timestamps,
extends expiry, or consumes future observations. Historical observations before
the visible window still contribute when eligible. `raw_congestion_index`
retains the selected raw value while `congestion_index` contains the displayed,
freshness-masked value. The latest raw map remains identical to the final raw
animation frame; smoothed values are intentionally different.

## Stop-delay Congestion Feasibility Analysis

The [road-segment congestion notebook](notebooks/route_97_speed_congestion_analysis.ipynb)
includes an offline EDA comparison at the selected stop, direction 0. It contrasts
BC Transit's remaining prediction error with historically available congestion
on each trip's own upstream path, including the incoming segment to the stop.
Example dashboards are saved as local HTML files under `notebooks/visuals/`.
The package also provides cohort residual plots, error distributions, horizon
associations, and coverage/robustness diagnostics. No delay model is fitted.

`prepare_stop_congestion_analysis` preserves visit indices and returns ordered
paths, bin provenance, segment comparisons, scoped summaries, and audit counts.
Only completed engine bins whose contributing probes were published/received
by the cutoff are eligible. Measurement-age limits mask stale data; missing
segments never become zero congestion. Remaining paths require a fresh
same-trip-instance vehicle position and respect its stop sequence/status.
Means describe observed segments only, alongside explicit path coverage.
`summarize_stop_congestion_signal` reports descriptive Spearman associations,
paired counts, and common-visit, complete-coverage, and external-probe subsets.
The display helpers produce the example and cohort Plotly figures.
The analysis also retains `reference_horizons` for every original visit, with
`path_available` and `path_exclusion_reason`. Selecting a known excluded visit
shows its own delay/reference curves and a warning with unavailable congestion
panels, rather than crashing or substituting another visit. Filter the reference
table by `path_available` to find eligible indices for a full dashboard.

**Feasibility limitation:** the engine's full-period median speed baselines
are intentionally unchanged and can use observations later than the evaluation.
This is not a fully leakage-free real-time backtest. A real-time implementation
needs prior-only baselines, trip-update receipt timestamps, and held-out-day
validation. The final reported arrival/delay is a reference proxy, not measured
ground truth; descriptive association does not demonstrate forecast improvement.
Missing static trip/shape matches are warned about and excluded, never mapped
to another trip or shape.

## Recurring Segment Delay Prediction

The separate [segment delay modelling notebook](notebooks/route_97_recurring_segment_delay_prediction.ipynb)
tests whether calendar patterns predict signed downstream-minus-upstream arrival
delay changes on Route `97-KEL`, direction `0`. It explicitly replays the
`2026-11-10_2_months` snapshot offline, using each traversal's own **scheduled**
upstream arrival time, local weekday and categorical directed segment identity.
Final-reported arrival delays are proxies that include dwell, holding and
schedule slack, not independently measured road traffic.

Run with the `transit_env` kernel. Install declared model dependencies from the
repository root if needed:

```bash
conda run -n transit_env python -m pip install -e ./forecasting
```

`prepare_segment_delay_data` reuses the audited delay engine, joins exact trip
stop sequences, handles extended GTFS times and DST, and excludes recording-edge
service days. `make_segment_delay_splits` builds **two seven-day expanding
development blocks and a final seven-day holdout**, with a one-service-day
training gap and label-availability/trip-boundary guards. The initially planned
four development weeks were reduced after auditing sparse early static-GTFS
matches; the first retained training block has 22 observed days. Insufficient
coverage raises an error rather than silently changing the design.

`run_segment_delay_experiment` compares L1-loss XGBoost with ordinary
segment/weekday/30-minute arithmetic averages, training-winsorised averages,
robust segment-only averages and zero change. Sparse calendar groups require
five observations across three service days, with explicit segment/time,
segment and global fallbacks. All feature fitting and model selection use
training/development history only; final-test labels are never used for fitting.

MAE, median absolute error, RMSE, R-squared, macro-segment MAE, support and
paired daily/segment improvements describe the result. Unchanged held-out
targets are scored for **all observations**, and separately within
training-only conditional residual bands and their tails (95% and 99%
sensitivity). There are no verified incident labels: these masks are
retrospective diagnostics, not an anomaly detector. Unexpected-delay magnitudes
are downweighted through robust training, not hidden by clipping test outcomes.
Calendar-model improvement beyond fixed segment corrections and consistency
across held-out weeks are more relevant than feature importance alone.

Running the notebook produces tables and figures. `save_segment_delay_experiment`
saves a non-overwriting run under ignored `data/segment_delay_models/`, including
the pre-test model bundle, snapshot manifest/checksums, versions/configuration,
segment mapping, split/audit tables, tuning results, out-of-time predictions
and metrics. Load pickle bundles only from trusted sources. This experiment
does not modify the live delay estimator or the reference notebook.

## Data Ingestion
The ingestion scripts live under [data_ingestion/](data_ingestion), and [data_ingestion/config.yaml](data_ingestion/config.yaml) defines the configuration. It pulls environmental variables from a `.env` file. To run the code, use [.env.example](data_ingestion/.env.example) as a reference for what to include in your `.env` file.

### Docker
The Docker Compose file is in [data_ingestion/docker-compose.yml](data_ingestion/docker-compose.yml). Run it from the `data_ingestion` directory:

```bash
cd data_ingestion
docker compose up --build
```

This starts MySQL, phpMyAdmin, and the data ingestion service.

### Integration Tests In Docker
Run integration tests inside Docker so MySQL host resolution matches the Compose network:

```bash
cd data_ingestion
./run_integration_tests.sh
```

This runs `tests/integration/test_mysql_ingestion.py` with `RUN_INTEGRATION_TESTS=1` inside the container and restores MySQL to its original state (stops it afterward only if the script had to start it).
