# Transit Forecasting

## Overview
Tools and data pipelines for transit forecasting and GTFS-realtime ingestion.

| Directory | Contents |
|-----------|----------|
| [data_ingestion/](data_ingestion) | GTFS-realtime ingestion service, MySQL storage and the nightly R2 Parquet archive |
| [forecasting/](forecasting) | `forecasting` package: backtest data loading, congestion engines, delay analyses and models |
| [notebooks/](notebooks) | Analysis notebooks; see the [notebook guide](notebooks/README.md) |
| [experiments/](experiments) | Research studies (charter, log and evaluation harness) |
| [docs/](docs) | Workflow and analysis documentation; see the [docs index](docs/README.md) |
| `data/` | Local snapshots and generated outputs (ignored by Git) |

## Documentation

- [Notebook guide](notebooks/README.md): each analysis's purpose, recommended
  reading order, inputs, outputs and interpretation limits.
- [Historical backtest data](docs/backtest-data.md): offline snapshots, the R2
  archive and MySQL downloads.
- [Database download scaling benchmark](docs/download-benchmark.md)
- Analyses: [congestion maps](docs/congestion-maps.md),
  [stop-delay feasibility](docs/stop-delay-feasibility.md),
  [recurring segment delay prediction](docs/segment-delay-prediction.md),
  [stop dynamics EDA](docs/stop-dynamics-eda.md)
- [Agent research loop](docs/agent-research-loop.md)

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

The root requirements file includes the data ingestion requirements. The
analysis notebooks also need the forecasting package's model dependencies:

```bash
conda run -n transit_env python -m pip install -e ./forecasting
```

### Tests

Use `pytest` for tests. Each package has its own unit tests; run them from the
package directory:

```bash
cd forecasting      # or data_ingestion
conda run -n transit_env python -m pytest tests/unit -q
```

See [Integration Tests In Docker](#integration-tests-in-docker) for the MySQL
ingestion tests.

### Pre-commit Hooks
Install the git hook:

```
pre-commit install
```

## Viewing Experiment Results (MLflow)

Research harness runs (for example
[experiments/hwy97_delay/evaluate.py](experiments/hwy97_delay/evaluate.py)) log
metrics, parameters and figures to a local MLflow store in `data/mlflow/`
(SQLite `mlflow.db` plus `artifacts/`, ignored by Git). Start the MLflow UI
from the repository root:

```bash
conda run --no-capture-output -n transit_env mlflow ui \
  --backend-store-uri sqlite:///data/mlflow/mlflow.db
```

Then open http://127.0.0.1:5000 and pick the study's experiment (such as
`hwy97_delay`). Add `--port <port>` if 5000 is already in use. Stop the server
with `Ctrl+C`.

In VS Code, the **MLflow UI** task in [.vscode/tasks.json](.vscode/tasks.json)
runs this automatically when the folder opens. Allow it once via
*Tasks: Manage Automatic Tasks* → *Allow Automatic Tasks*. It assumes the
`transit_env` environment lives at `~/miniconda3/envs/transit_env` and skips
starting if port 5000 is already taken. Run it manually with
*Tasks: Run Task* → *MLflow UI*, and stop it by closing its terminal.

The store only exists after at least one harness run; each
study's `LOG.md` cites the MLflow run ID for every logged result, and its
`HARNESS.md` lists what each run records.

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
The `archive` Compose service runs `python -m data_ingestion.nightly`: at start-up and then every night at `ARCHIVE_RUN_AT_UTC` (default `10:00`, when the feed is quietest: 2–3 a.m. in Kelowna) it archives every complete UTC day not yet in R2, so a missed night catches up on the next run. Failures are logged to the container logs (`docker compose logs archive`) and retried after an hour.

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
