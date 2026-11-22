# Operations runbook

How to run, deploy and check the ingestion stack on the Raspberry Pi. For what
each service does, see [architecture.md](architecture.md). For command
options, see the [data_ingestion README](../data_ingestion/README.md).

All `docker compose` commands run from `data_ingestion/` in the Pi's checkout.

## Services

| Service | Container | Purpose | Host port |
|---|---|---|---|
| `mysql` | `transit_mysql` | MySQL 8 holding recent realtime rows (volume `mysql_data`) | 3307 |
| `phpmyadmin` | `phpmyadmin` | Web UI for MySQL | 8080 |
| `data_ingestion` | `data_ingestion` | Polls both GTFS-RT feeds every 5 s and inserts changed rows | none |
| `archive` | `transit_archive` | Nightly R2 export and MySQL retention | none |
| `integration_tests` | (one-off) | Integration tests; only with `--profile test` | none |

Every service restarts automatically (`unless-stopped`), so they come back
after a reboot or power loss. Docker logs are capped at 3 × 10 MB per
container, and the ingestion log file inside the container at 4 × 10 MB.

## First-time setup

1. Clone the repository on the Pi and `cd data_ingestion`.
2. Copy [.env.example](../data_ingestion/.env.example) to `.env` and set the
   MySQL passwords. Add the R2 settings (`R2_ACCOUNT_ID`, `R2_BUCKET`,
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`) and, to keep MySQL small,
   `ARCHIVE_RETAIN_DAYS` (for example `14`). Never commit `.env`.
3. Start everything:

   ```bash
   docker compose up -d --build
   ```

4. Run the [daily check](#daily-check) after a few minutes.

## Deploying a change

Never edit code on the Pi. The Pi's checkout only pulls from GitHub.

1. On the development machine, branch off `main`, make the change, run the
   unit tests and, for database changes, `./run_integration_tests.sh`.
2. Push the branch.
3. On the Pi:

   ```bash
   cd data_ingestion
   git fetch origin
   git switch <branch>        # or stay on main after the PR is merged
   git pull
   docker compose up -d --build data_ingestion archive
   ```

4. Check the logs (below). Once the PR is merged, switch the Pi back to `main`
   and pull.

Rebuilding `data_ingestion` restarts it and loses its in-memory change
tracking, so it writes the current feed again once
([#6](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/6)).
Keep restarts infrequent.

## Daily check

```bash
docker compose ps                                     # all services "Up", mysql "healthy"
docker compose logs --tail 20 data_ingestion          # "Inserting N unique rows" every few seconds
docker compose logs --since 24h archive               # last night's export and prune
df -h /                                               # free space on the SD card
```

Latest stored row (should be seconds old during service hours, roughly 05:00
to 01:00 local):

```bash
docker compose exec mysql sh -c \
  'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" \
   -e "SELECT MAX(feed_timestamp) FROM TRANSIT_TRIP_TABLE; SELECT MAX(feed_timestamp) FROM TRANSIT_VEHICLE_TABLE;"'
```

The value is UTC. Kelowna is UTC−7 all year.

Archive status, read-only (which days are in R2 and verified):

```bash
docker compose exec archive python -m data_ingestion.retention check --retain-days 14
```

From the analysis machine,
`forecasting.list_archive_days("trip_updates", env_file=".env")` lists the
archived days. The newest should be yesterday (UTC) once the
nightly run at 10:00 UTC has finished.

## Troubleshooting

### No new rows

- `docker compose logs data_ingestion` shows `No data received from feed`
  after a network or HTTP error: the feed is down or unreachable. Check
  `curl -sI "https://bct.tmix.se/gtfs-realtime/tripupdates.pb?operatorIds=47"`.
  The service retries every 5 s, so nothing needs restarting. Note the gap in
  the analysis docs if it lasts more than an hour.
- `MySQL error, will attempt reconnection`: check `docker compose ps mysql`.
  If MySQL is not healthy, see [MySQL does not start](#mysql-does-not-start).
- Outside service hours (about 01:00 to 05:00 local) few or no rows is normal.

### Nightly export failed

The `archive` service logs the error and retries after an hour. Any missed
day is exported on the next successful run, because each run exports every
complete day not yet in R2. To run it now:

```bash
docker compose exec archive python -m data_ingestion.archive              # all missing days
docker compose exec archive python -m data_ingestion.archive --start 2026-11-01 --end 2026-11-02
```

Common causes are an expired or wrong R2 token (check the `R2_*` values in
`.env`) and no network.

### Disk is filling up

1. `df -h /` and `docker system df` to see where the space is.
2. Check that `ARCHIVE_RETAIN_DAYS` is set in `.env` and that the archive logs
   show prune runs. Without it MySQL keeps everything and grows about 200 MB a
   day.
3. Deleting rows does not shrink InnoDB files. To reclaim space once, run
   `compact` and then drop the backup tables (see the
   [retention commands](../data_ingestion/README.md#nightly-export-and-mysql-retention)).
   Compaction needs about 2 GB free for 14 days of rows. Dropping the backup
   cannot be undone, so confirm `check` reports every older day as verified
   first.
4. `docker image prune` removes old build images after several deploys.

### MySQL does not start

- `docker compose logs mysql` for the reason. A full disk is the usual cause
  on the Pi; free space first.
- After an unclean power loss, InnoDB recovers on start-up, which can take a
  few minutes. Wait for the health check before restarting anything.
- Do not delete the `mysql_data` volume: rows not yet exported to R2 would be
  lost. Run the archive export first if MySQL is up.

### Local times look an hour off

Kelowna has been on UTC−7 all year since 2026-03-08. A host or container with
tzdata older than 2026a converts times after 2026-11-01 with UTC−8. Ingestion
itself stores UTC and does no conversion, so this only matters for code that
converts to local time. See
[gtfs-rt-data.md](gtfs-rt-data.md#time-zones-and-clocks).

## Secrets

| File | Where | Holds |
|---|---|---|
| `data_ingestion/.env` | Pi | MySQL passwords (`MYSQL_*`), R2 keys, archive settings |
| `.env` at the repository root | Analysis machine | R2 keys and, for MySQL downloads, `TRANSIT_DB_*` |

Both are ignored by Git. If a key leaks, rotate it in the Cloudflare dashboard
and update both files.
