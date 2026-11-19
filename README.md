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

This runs `tests/integration/` with `RUN_INTEGRATION_TESTS=1` inside the container and restores MySQL to its original state (stops it afterward only if the script had to start it).

### Archive To Cloudflare R2
`python -m data_ingestion.archive` copies complete UTC days of both realtime tables to R2 as zstd Parquet files, `archive/<table>/date=YYYY-MM-DD/data.parquet`, sorted by route and direction. Each upload's row count is checked against MySQL; days already in R2 are skipped unless `--overwrite` is given.

```bash
cd data_ingestion
pip install ".[archive]"
python -m data_ingestion.archive --env-file ../.env                # all complete days not yet archived
python -m data_ingestion.archive --start 2026-11-01 --end 2026-11-08 --table trip_updates
```

Settings come from `--env-file` and the environment: MySQL as `TRANSIT_DB_*` (laptop) or `MYSQL_*` (the Docker `.env`), plus `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`. `--end` is exclusive and cannot be later than today (UTC), so the current partial day is never archived.

### Nightly Export And MySQL Retention
The `archive` Compose service runs `python -m data_ingestion.nightly`: at start-up and then every night at `ARCHIVE_RUN_AT_UTC` (default `00:30`) it archives every complete UTC day not yet in R2, so a missed night catches up on the next run. Failures are logged to the container logs (`docker compose logs archive`) and retried after an hour.

Set these in `data_ingestion/.env` (never commit it):

| Setting | Purpose |
|---|---|
| `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 destination (required) |
| `ARCHIVE_RUN_AT_UTC` | Daily run time, `HH:MM` UTC (optional) |
| `ARCHIVE_RETAIN_DAYS` | When set, delete MySQL days older than this many complete days after each export (optional; unset keeps everything) |

`python -m data_ingestion.retention` removes archived rows. A UTC day is removed only when its R2 file exists and its row count equals MySQL's; anything else is kept and logged.

```bash
docker compose up -d --build archive
docker compose exec archive python -m data_ingestion.retention check --retain-days 14   # read-only report
docker compose exec archive python -m data_ingestion.retention compact --retain-days 14 # one-time shrink
docker compose exec archive python -m data_ingestion.retention drop-backup --yes        # irreversible
```

`prune` (what the nightly run does) deletes in small batches; InnoDB reuses that space, so the files stop growing but do not shrink. To reclaim the space already used, run `compact` once: it copies the retained rows into a new table while ingestion keeps writing, briefly locks both tables to copy the last rows and swap them with `RENAME TABLE`, and keeps the original as `<table>_pre_compact`. Dropping that backup frees the disk space and cannot be undone. Compaction needs free disk for the retained rows (about 2 GB for 14 days) and refuses to run if any older day is not verified in R2.
