# Historical backtest data

The [road-segment congestion notebook](../notebooks/route_97_speed_congestion_analysis.ipynb)
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

## R2 archive (default for notebooks)

The full realtime history lives in a Cloudflare R2 bucket as one zstd Parquet
file per table and UTC day (`archive/<table>/date=YYYY-MM-DD/data.parquet`),
written by `python -m data_ingestion.archive`. The Raspberry Pi MySQL database
keeps only recent days. Put the R2 settings in the ignored repository `.env`:

```text
R2_ACCOUNT_ID=...
R2_BUCKET=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```

`source="r2"` reads an inclusive UTC window, optionally filtered by route and
direction, downloads current static GTFS, and saves a normal snapshot. If the
`dataset_id` is already saved, it is replayed offline instead, so rerunning a
notebook does not download again. Choose a new `dataset_id` for a new window.

```python
dataset = get_backtest_dataset(
    "data",
    source="r2",
    dataset_id="route97_direction0_october",
    env_file=".env",
    archive_start="2026-10-01 00:00:00",
    archive_end="2026-10-31 23:59:59",
    route_id="97-KEL",
    direction_id=0,
)
```

For ad hoc reads without a snapshot, use `load_archive_updates` (returns the
same columns and dtypes as a MySQL download, newest `id` first) and
`list_archive_days`. Days not yet archived raise `FileNotFoundError`; the
newest day reaches R2 after the nightly export.

```python
from forecasting import load_archive_updates

trips = load_archive_updates(
    "trip_updates", "2026-11-01", "2026-11-07 23:59:59",
    route_id="97-KEL", direction_id=0, env_file=".env",
)
```

## MySQL download

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
    env_file=".env",
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
