# Database download scaling benchmark

The [download benchmark](../forecasting/forecasting/benchmark_download_speed.py)
uses the same filtered realtime download pattern as the
[speed-based congestion notebook](../notebooks/route_97_speed_congestion_analysis.ipynb):
Route `97-KEL`, direction `0`, both realtime tables, all columns, and
`ORDER BY id DESC`. Run from the repository root:

```bash
conda run --no-capture-output -n transit_env python -m forecasting.benchmark_download_speed
```

It reads the same ignored `.env` file or existing
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
