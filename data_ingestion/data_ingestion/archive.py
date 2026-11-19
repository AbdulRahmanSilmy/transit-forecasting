"""Archive realtime MySQL tables to daily Parquet files in Cloudflare R2.

Each table is written as one zstd-compressed Parquet file per UTC day::

    s3://<bucket>/archive/<table>/date=YYYY-MM-DD/data.parquet

Rows are sorted by route, direction, and ``id`` and written in small row
groups, so readers that filter on route and direction download only the
matching parts of each file. Every upload is verified by comparing its row
count with the source rows before it is reported as archived.

Run ``python -m data_ingestion.archive --help`` for the command-line usage.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .constants import TRIP_UPDATE_TABLE, VEHICLE_UPDATE_TABLE

logger = logging.getLogger(__name__)

ARCHIVE_PREFIX = "archive"
ARCHIVE_TABLES = {
    "trip_updates": TRIP_UPDATE_TABLE,
    "vehicle_updates": VEHICLE_UPDATE_TABLE,
}
ARCHIVE_FILE_NAME = "data.parquet"
ROW_GROUP_SIZE = 65_536
SORT_COLUMNS = ("trip_route_id", "trip_direction_id", "id")

# Laptop .env files use TRANSIT_DB_*; the Pi's Docker .env uses MYSQL_*.
_MYSQL_SETTING_NAMES = {
    "host": ("TRANSIT_DB_HOST", "MYSQL_HOST"),
    "port": ("TRANSIT_DB_PORT", "MYSQL_PORT"),
    "user": ("TRANSIT_DB_USER", "MYSQL_USER"),
    "password": ("TRANSIT_DB_PASSWORD", "MYSQL_PASSWORD"),
    "database": ("TRANSIT_DB_NAME", "MYSQL_DATABASE"),
}
_R2_SETTING_NAMES = {
    "account_id": "R2_ACCOUNT_ID",
    "bucket": "R2_BUCKET",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
}


@dataclass(frozen=True)
class ArchiveSettings:
    """Connection settings for the MySQL source and the R2 destination.

    Parameters
    ----------
    mysql : dict[str, str]
        ``host``, ``port``, ``user``, ``password``, and ``database``.
    r2 : dict[str, str]
        ``account_id``, ``bucket``, ``access_key_id``, and
        ``secret_access_key``.
    """

    mysql: Mapping[str, str]
    r2: Mapping[str, str]


@dataclass(frozen=True)
class DayExport:
    """Result of archiving one table for one UTC day.

    Parameters
    ----------
    table : str
        Archive table name, such as ``"trip_updates"``.
    day : datetime.date
        UTC day that was archived.
    rows : int
        Verified row count of the uploaded file.
    seconds : float
        Wall-clock duration of the export.
    """

    table: str
    day: date
    rows: int
    seconds: float


def read_env_file(path: str | Path | None) -> dict[str, str]:
    """Parse a shell-style ``KEY=value`` settings file.

    Parameters
    ----------
    path : str or pathlib.Path or None
        File to read. A missing file or None yields an empty mapping.

    Returns
    -------
    dict[str, str]
        Parsed settings. Blank lines and ``#`` comments are skipped, an
        optional ``export`` prefix is removed, and values are unquoted.

    Raises
    ------
    ValueError
        If a non-comment line has no ``=`` or a value cannot be unquoted.
    """
    if path is None or not Path(path).is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        name, separator, raw_value = line.partition("=")
        if not separator:
            raise ValueError(f"Invalid setting on line {line_number} of {path}.")
        parsed = shlex.split(raw_value, posix=True)
        if len(parsed) > 1:
            raise ValueError(f"Invalid value on line {line_number} of {path}.")
        values[name.strip()] = parsed[0] if parsed else ""
    return values


def load_archive_settings(
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ArchiveSettings:
    """Resolve MySQL and R2 settings from a file and the environment.

    Parameters
    ----------
    env_file : str or pathlib.Path or None, optional
        Settings file whose values override ``environ``.
    environ : collections.abc.Mapping or None, optional
        Fallback variables; defaults to ``os.environ``.

    Returns
    -------
    ArchiveSettings
        Resolved settings.

    Raises
    ------
    ValueError
        If any required setting is missing or the port is not an integer.
    """
    values = {**(os.environ if environ is None else environ), **read_env_file(env_file)}
    mysql: dict[str, str] = {}
    missing: list[str] = []
    for key, names in _MYSQL_SETTING_NAMES.items():
        found = next((values[name] for name in names if values.get(name)), None)
        if found is None:
            missing.append(" or ".join(names))
        else:
            mysql[key] = found
    r2: dict[str, str] = {}
    for key, name in _R2_SETTING_NAMES.items():
        if values.get(name):
            r2[key] = values[name]
        else:
            missing.append(name)
    if missing:
        raise ValueError("Missing archive settings: " + ", ".join(missing))
    if not mysql["port"].isdigit():
        raise ValueError("The MySQL port must be an integer.")
    return ArchiveSettings(mysql=mysql, r2=r2)


def archive_uri(bucket: str, table: str, day: date) -> str:
    """Return the R2 URI of one archived table-day.

    Parameters
    ----------
    bucket : str
        R2 bucket name.
    table : str
        Archive table name; a key of ``ARCHIVE_TABLES``.
    day : datetime.date
        UTC day.

    Returns
    -------
    str
        ``r2://`` URI of the Parquet file.
    """
    _check_table(table)
    return (
        f"r2://{bucket}/{ARCHIVE_PREFIX}/{table}/"
        f"date={day.isoformat()}/{ARCHIVE_FILE_NAME}"
    )


def source_query(table: str, day: date) -> str:
    """Build the MySQL query selecting one UTC day of a table.

    Parameters
    ----------
    table : str
        Archive table name; a key of ``ARCHIVE_TABLES``.
    day : datetime.date
        UTC day. The MySQL server stores and returns ``TIMESTAMP`` values
        in UTC.

    Returns
    -------
    str
        Query using the ``feed_timestamp`` index with a half-open day range.
    """
    _check_table(table)
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    return (
        f"SELECT * FROM {ARCHIVE_TABLES[table]} "
        f"WHERE feed_timestamp >= '{start:%Y-%m-%d %H:%M:%S}' "
        f"AND feed_timestamp < '{end:%Y-%m-%d %H:%M:%S}'"
    )


def day_range(start: date, end: date) -> list[date]:
    """Return the UTC days in ``[start, end)``.

    Parameters
    ----------
    start, end : datetime.date
        Inclusive first day and exclusive last day.

    Returns
    -------
    list[datetime.date]
        Consecutive days; empty when ``end <= start``.
    """
    return [start + timedelta(days=offset) for offset in range((end - start).days)]


def connect(settings: ArchiveSettings):
    """Open a DuckDB connection with MySQL attached and R2 credentials set.

    Parameters
    ----------
    settings : ArchiveSettings
        Source and destination settings.

    Returns
    -------
    duckdb.DuckDBPyConnection
        Connection where the MySQL database is attached read-only as ``src``.
    """
    import duckdb  # pylint: disable=import-outside-toplevel

    con = duckdb.connect()
    con.execute("INSTALL mysql; LOAD mysql; INSTALL httpfs; LOAD httpfs;")
    mysql = settings.mysql
    # DuckDB does not bind parameters in secrets, ATTACH, or table functions,
    # so values are passed as escaped SQL string literals.
    con.execute(
        "CREATE OR REPLACE SECRET mysql_source (TYPE mysql, "
        f"HOST {_sql_literal(mysql['host'])}, PORT {int(mysql['port'])}, "
        f"USER {_sql_literal(mysql['user'])}, "
        f"PASSWORD {_sql_literal(mysql['password'])}, "
        f"DATABASE {_sql_literal(mysql['database'])})"
    )
    con.execute("ATTACH '' AS src (TYPE mysql, SECRET mysql_source, READ_ONLY)")
    r2 = settings.r2
    con.execute(
        "CREATE OR REPLACE SECRET r2_archive (TYPE r2, "
        f"KEY_ID {_sql_literal(r2['access_key_id'])}, "
        f"SECRET {_sql_literal(r2['secret_access_key'])}, "
        f"ACCOUNT_ID {_sql_literal(r2['account_id'])})"
    )
    return con


def archived_days(con, bucket: str, table: str) -> set[date]:
    """List the UTC days already archived for a table.

    Parameters
    ----------
    con : duckdb.DuckDBPyConnection
        Connection from :func:`connect`.
    bucket : str
        R2 bucket name.
    table : str
        Archive table name.

    Returns
    -------
    set[datetime.date]
        Days with an archive file present.
    """
    _check_table(table)
    pattern = f"r2://{bucket}/{ARCHIVE_PREFIX}/{table}/date=*/{ARCHIVE_FILE_NAME}"
    rows = con.execute(f"SELECT file FROM glob({_sql_literal(pattern)})").fetchall()
    days = set()
    for (path,) in rows:
        part = next(p for p in path.split("/") if p.startswith("date="))
        days.add(date.fromisoformat(part.removeprefix("date=")))
    return days


def export_day(con, bucket: str, table: str, day: date) -> DayExport:
    """Copy one UTC day of a MySQL table to R2 and verify the row count.

    Parameters
    ----------
    con : duckdb.DuckDBPyConnection
        Connection from :func:`connect`.
    bucket : str
        R2 bucket name.
    table : str
        Archive table name.
    day : datetime.date
        UTC day to export. It should be complete (before today in UTC).

    Returns
    -------
    DayExport
        Verified result.

    Raises
    ------
    RuntimeError
        If the uploaded file's row count differs from the source rows.
    """
    started = time.monotonic()
    uri = archive_uri(bucket, table, day)
    con.execute(
        "CREATE OR REPLACE TEMP TABLE day_rows AS "
        f"SELECT * FROM mysql_query('src', {_sql_literal(source_query(table, day))})"
    )
    source_rows = con.execute("SELECT count(*) FROM day_rows").fetchone()[0]
    order = ", ".join(SORT_COLUMNS)
    con.execute(
        f"COPY (SELECT * FROM day_rows ORDER BY {order}) TO {_sql_literal(uri)} "
        f"(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE})"
    )
    con.execute("DROP TABLE day_rows")
    archived_rows = con.execute(
        f"SELECT count(*) FROM read_parquet({_sql_literal(uri)})"
    ).fetchone()[0]
    if archived_rows != source_rows:
        raise RuntimeError(
            f"{table} {day}: archived {archived_rows} rows, expected {source_rows}."
        )
    return DayExport(table, day, archived_rows, time.monotonic() - started)


def export_days(
    settings: ArchiveSettings,
    table: str,
    days: Iterable[date],
    *,
    overwrite: bool = False,
) -> list[DayExport]:
    """Archive several days of one table sequentially on one connection.

    Parameters
    ----------
    settings : ArchiveSettings
        Source and destination settings.
    table : str
        Archive table name.
    days : collections.abc.Iterable[datetime.date]
        UTC days to export, in order.
    overwrite : bool, optional
        Re-export days that already have an archive file.

    Returns
    -------
    list[DayExport]
        Results for exported days; skipped days are omitted.
    """
    con = connect(settings)
    bucket = settings.r2["bucket"]
    existing = set() if overwrite else archived_days(con, bucket, table)
    results = []
    for day in days:
        if day in existing:
            logger.info("%s %s: already archived, skipping", table, day)
            continue
        result = export_day(con, bucket, table, day)
        logger.info(
            "%s %s: %d rows archived in %.1f s",
            table,
            day,
            result.rows,
            result.seconds,
        )
        results.append(result)
    con.close()
    return results


def _check_table(table: str) -> None:
    if table not in ARCHIVE_TABLES:
        raise ValueError(
            f"Unknown archive table {table!r}; choose from {sorted(ARCHIVE_TABLES)}."
        )


def _sql_literal(value: str) -> str:
    """Return ``value`` as a single-quoted SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _earliest_day(settings: ArchiveSettings) -> date:
    con = connect(settings)
    try:
        earliest = min(
            con.execute(
                "SELECT * FROM mysql_query('src', "
                f"{_sql_literal(f'SELECT MIN(feed_timestamp) FROM {name}')})"
            ).fetchone()[0]
            for name in ARCHIVE_TABLES.values()
        )
    finally:
        con.close()
    return earliest.date()


def run_export(
    settings: ArchiveSettings,
    *,
    start: date | None = None,
    end: date | None = None,
    tables: Iterable[str] | None = None,
    overwrite: bool = False,
) -> dict[str, list[DayExport]]:
    """Archive every complete UTC day in ``[start, end)`` of several tables.

    Parameters
    ----------
    settings : ArchiveSettings
        Source and destination settings.
    start : datetime.date or None, optional
        First UTC day; defaults to the earliest day in MySQL.
    end : datetime.date or None, optional
        Exclusive last UTC day; defaults to, and may not exceed, today (UTC).
    tables : collections.abc.Iterable[str] or None, optional
        Archive table names; defaults to all of ``ARCHIVE_TABLES``.
    overwrite : bool, optional
        Re-export days that already have an archive file.

    Returns
    -------
    dict[str, list[DayExport]]
        Exported days per table; days already archived are omitted.

    Raises
    ------
    ValueError
        If ``end`` is later than today (UTC).
    """
    today = datetime.now(timezone.utc).date()
    end = today if end is None else end
    if end > today:
        raise ValueError("end cannot be later than today (UTC); days must be complete.")
    start = start or _earliest_day(settings)
    days = day_range(start, end)
    tables = sorted(tables or ARCHIVE_TABLES)
    logger.info("Archiving %s for %d days: %s to %s", tables, len(days), start, end)
    results: dict[str, list[DayExport]] = {}
    with ThreadPoolExecutor(max_workers=len(tables)) as pool:
        futures = {
            pool.submit(export_days, settings, table, days, overwrite=overwrite): table
            for table in tables
        }
        for future in as_completed(futures):
            table = futures[future]
            results[table] = future.result()
            logger.info(
                "%s: %d days, %d rows archived",
                table,
                len(results[table]),
                sum(result.rows for result in results[table]),
            )
    return results


def main(argv: list[str] | None = None) -> None:
    """Archive complete UTC days of the realtime tables to R2.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.
    """
    today = datetime.now(timezone.utc).date()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        help="First UTC day (default: earliest day in MySQL).",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=today,
        help="Exclusive last UTC day (default and maximum: today).",
    )
    parser.add_argument(
        "--table",
        choices=sorted(ARCHIVE_TABLES),
        action="append",
        help="Table to archive; repeatable (default: all).",
    )
    parser.add_argument("--env-file", type=Path, help="Settings file to read.")
    parser.add_argument(
        "--overwrite", action="store_true", help="Re-export archived days."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    if args.end > today:
        parser.error("--end cannot be later than today (UTC); days must be complete.")
    run_export(
        load_archive_settings(args.env_file),
        start=args.start,
        end=args.end,
        tables=args.table,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
