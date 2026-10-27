# Transit Forecasting

## Overview
Tools and data pipelines for transit forecasting and GTFS-realtime ingestion.

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

The [road-segment congestion notebook](notebooks/road_segment_congestion.ipynb)
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
  `backtest_lookback_hours` as needed, then switch back to `'cache'` for reruns.
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

Lower-level `save_backtest_dataset`, `load_backtest_dataset`, and
`list_backtest_datasets` utilities remain available from `forecasting`. Pickle
snapshots preserve DataFrame dtypes and order, but must only be loaded from
trusted sources. All of `data/` remains ignored by Git; no credentials are
stored in snapshots.

## Congestion Maps

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
