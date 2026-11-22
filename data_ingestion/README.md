# data_ingestion

Captures BC Transit's GTFS-Realtime feeds for Kelowna into MySQL and archives
them nightly to Cloudflare R2 as Parquet. In production it runs on a
Raspberry Pi under Docker Compose.

- What the stored data means and its known quirks: [docs/gtfs-rt-data.md](../docs/gtfs-rt-data.md)
- Running and fixing it on the Pi: [docs/operations.md](../docs/operations.md)
- How it fits with the rest of the repository: [docs/architecture.md](../docs/architecture.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)

## Layout

| Path | Contents |
|---|---|
| [data_ingestion/\_\_main\_\_.py](data_ingestion/__main__.py) | Service entry point: one polling thread per feed |
| [data_ingestion/data_ingestion.py](data_ingestion/data_ingestion.py) | Fetch, parse, change detection and inserts (`TripUpdatesDataIngestion`, `VehicleUpdatesDataIngestion`) |
| [data_ingestion/constants.py](data_ingestion/constants.py) | Table names, feed field paths and column names |
| [data_ingestion/config.py](data_ingestion/config.py) | Loads [config.yaml](config.yaml) and expands `${VAR}` placeholders from the environment |
| [data_ingestion/queries.py](data_ingestion/queries.py), [sql/](data_ingestion/sql) | `CREATE TABLE` and `INSERT` templates |
| [data_ingestion/archive.py](data_ingestion/archive.py) | Export complete UTC days to R2 Parquet |
| [data_ingestion/retention.py](data_ingestion/retention.py) | Verify, prune and compact archived days in MySQL |
| [data_ingestion/nightly.py](data_ingestion/nightly.py) | Scheduler that runs the export and prune once a day |
| [tests/unit/](tests/unit), [tests/integration/](tests/integration) | Unit tests; MySQL integration tests (Docker) |

## Configuration

[config.yaml](config.yaml) sets the feed URLs, the poll interval (5 s) and the
reconnect delay. The MySQL connection comes from environment variables, which
Docker Compose reads from `.env` in this directory. Copy
[.env.example](.env.example) to `.env` and fill it in. Never commit it.

| Setting | Used by | Purpose |
|---|---|---|
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` | all services | MySQL connection (`MYSQL_HOST=mysql` inside Compose) |
| `MYSQL_ROOT_PASSWORD`, `PMA_PASSWORD` | `mysql`, `phpmyadmin` | Admin passwords |
| `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | `archive` | R2 destination (required for archiving) |
| `ARCHIVE_RUN_AT_UTC` | `archive` | Daily run time, `HH:MM` UTC (default `10:00`, when the feed is quietest: 3 a.m. in Kelowna) |
| `ARCHIVE_RETAIN_DAYS` | `archive` | When set, delete MySQL days older than this many complete days after each export. Unset keeps everything |
| `RUN_INTEGRATION_TESTS` | tests | `1` enables the integration tests |

When run outside Docker, the archive and retention commands also accept the
laptop names `TRANSIT_DB_HOST`, `TRANSIT_DB_PORT`, `TRANSIT_DB_USER`,
`TRANSIT_DB_PASSWORD` and `TRANSIT_DB_NAME`, through `--env-file`.

## Running with Docker

```bash
cd data_ingestion
docker compose up --build
```

This starts MySQL, phpMyAdmin (http://localhost:8080), the ingestion service
and the nightly archive service. MySQL is published on host port 3307. Each
ingestion thread creates its table and the `feed_timestamp` index on first
start.

## Running without Docker

Point the `MYSQL_*` variables at a reachable MySQL server, then:

```bash
cd data_ingestion
pip install -e ".[archive,test]"
python -m data_ingestion --config config.yaml
```

The service logs to the console and to `data_ingestion.log` (rotated at
10 MB, three backups).

## Tests

Unit tests:

```bash
cd data_ingestion
conda run -n transit_env python -m pytest tests/unit -q
```

Integration tests run inside Docker, so MySQL host resolution matches the
Compose network:

```bash
cd data_ingestion
./run_integration_tests.sh
```

This runs `tests/integration/` with `RUN_INTEGRATION_TESTS=1` inside the
container. It leaves MySQL as it found it, stopping it afterwards only if the
script started it.

## Archive to Cloudflare R2

`python -m data_ingestion.archive` copies complete UTC days of both realtime
tables to R2 as zstd Parquet files,
`archive/<table>/date=YYYY-MM-DD/data.parquet`, sorted by route and
direction. Each upload's row count is checked against MySQL. Days already in
R2 are skipped unless `--overwrite` is given.

```bash
cd data_ingestion
pip install ".[archive]"
python -m data_ingestion.archive --env-file ../.env                # all complete days not yet archived
python -m data_ingestion.archive --start 2026-11-01 --end 2026-11-08 --table trip_updates
```

Settings come from `--env-file` and the environment (see
[Configuration](#configuration)). `--end` is exclusive and cannot be later
than today (UTC), so the current partial day is never archived.

## Nightly export and MySQL retention

The `archive` Compose service runs `python -m data_ingestion.nightly`. At
start-up, and then every night at `ARCHIVE_RUN_AT_UTC`, it archives every
complete UTC day not yet in R2, so a missed night catches up on the next run.
Failures are logged to the container logs (`docker compose logs archive`) and
retried after an hour.

`python -m data_ingestion.retention` removes archived rows. A UTC day is
removed only when its R2 file exists and its row count equals MySQL's.
Anything else is kept and logged.

```bash
docker compose up -d --build archive
docker compose exec archive python -m data_ingestion.retention check --retain-days 14   # read-only report
docker compose exec archive python -m data_ingestion.retention compact --retain-days 14 # one-time shrink
docker compose exec archive python -m data_ingestion.retention drop-backup --yes        # irreversible
```

`prune`, which the nightly run does, deletes in small batches. InnoDB reuses
that space, so the files stop growing but do not shrink. To reclaim space
already used, run `compact` once. It copies the retained rows into a new
table while ingestion keeps writing, briefly locks both tables to copy the
last rows, swaps them with `RENAME TABLE`, and keeps the original as
`<table>_pre_compact`. Dropping that backup frees the disk space and cannot be
undone. Compaction needs free disk for the retained rows (about 2 GB for 14
days) and refuses to run if any older day is not verified in R2.
