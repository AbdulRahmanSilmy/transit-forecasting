"""Read archived realtime updates from the Cloudflare R2 Parquet archive.

The archive holds one Parquet file per table and UTC day::

    r2://<bucket>/archive/<table>/date=YYYY-MM-DD/data.parquet

It is written by ``python -m data_ingestion.archive``. Rows inside each file
are sorted by route and direction, so filtered reads download only the
matching row groups. Credentials come from ``R2_*`` settings in the
repository ``.env`` file or the environment.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

ARCHIVE_PREFIX = "archive"
ARCHIVE_TABLES = ("trip_updates", "vehicle_updates")
ArchiveTable = Literal["trip_updates", "vehicle_updates"]

_R2_SETTING_NAMES = {
    "account_id": "R2_ACCOUNT_ID",
    "bucket": "R2_BUCKET",
    "access_key_id": "R2_ACCESS_KEY_ID",
    "secret_access_key": "R2_SECRET_ACCESS_KEY",
}


def read_r2_settings(
    env_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve R2 credentials from a settings file and the environment.

    Parameters
    ----------
    env_file : str or pathlib.Path or None, optional
        Shell-style ``KEY=value`` file, such as the repository ``.env``.
        Its values override ``environ``; other variables are ignored.
    environ : collections.abc.Mapping or None, optional
        Fallback variables; defaults to ``os.environ``.

    Returns
    -------
    dict[str, str]
        ``account_id``, ``bucket``, ``access_key_id``, and
        ``secret_access_key``.

    Raises
    ------
    ValueError
        If the file has a malformed line or a setting is missing.
    """
    values = dict(os.environ if environ is None else environ)
    if env_file is not None and Path(env_file).is_file():
        path = Path(env_file)
        for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, raw_value = line.removeprefix("export ").partition("=")
            if not separator:
                raise ValueError(f"Invalid setting on line {line_number} of {path}.")
            parsed = shlex.split(raw_value, posix=True)
            if len(parsed) > 1:
                raise ValueError(f"Invalid value on line {line_number} of {path}.")
            values[name.strip()] = parsed[0] if parsed else ""
    missing = [name for name in _R2_SETTING_NAMES.values() if not values.get(name)]
    if missing:
        raise ValueError(
            "Missing R2 settings: "
            + ", ".join(missing)
            + ". Add them to the repository .env file."
        )
    return {key: values[name] for key, name in _R2_SETTING_NAMES.items()}


def connect_r2_archive(env_file: str | Path | None = None):
    """Open a DuckDB connection that can read the R2 archive.

    Parameters
    ----------
    env_file : str or pathlib.Path or None, optional
        Settings file holding the ``R2_*`` credentials.

    Returns
    -------
    tuple[duckdb.DuckDBPyConnection, str]
        Connection with an R2 secret configured, and the bucket name.
    """
    import duckdb  # pylint: disable=import-outside-toplevel

    settings = read_r2_settings(env_file)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        "CREATE OR REPLACE SECRET r2_archive (TYPE r2, "
        f"KEY_ID {_sql_literal(settings['access_key_id'])}, "
        f"SECRET {_sql_literal(settings['secret_access_key'])}, "
        f"ACCOUNT_ID {_sql_literal(settings['account_id'])})"
    )
    return con, settings["bucket"]


def archive_uri(bucket: str, table: str, day: date) -> str:
    """Return the R2 URI of one archived table-day.

    Parameters
    ----------
    bucket : str
        R2 bucket name.
    table : {'trip_updates', 'vehicle_updates'}
        Archive table.
    day : datetime.date
        UTC day.

    Returns
    -------
    str
        ``r2://`` URI of the Parquet file.
    """
    _check_table(table)
    return f"r2://{bucket}/{ARCHIVE_PREFIX}/{table}/date={day.isoformat()}/data.parquet"


def list_archive_days(
    table: ArchiveTable, *, env_file: str | Path | None = None, con=None
) -> list[date]:
    """List the UTC days available in the archive for one table.

    Parameters
    ----------
    table : {'trip_updates', 'vehicle_updates'}
        Archive table.
    env_file : str or pathlib.Path or None, optional
        Settings file holding the ``R2_*`` credentials.
    con : duckdb.DuckDBPyConnection or None, optional
        Existing connection from :func:`connect_r2_archive`; its bucket is
        read from ``env_file``.

    Returns
    -------
    list[datetime.date]
        Sorted archived days.
    """
    _check_table(table)
    if con is None:
        con, bucket = connect_r2_archive(env_file)
    else:
        bucket = read_r2_settings(env_file)["bucket"]
    pattern = f"r2://{bucket}/{ARCHIVE_PREFIX}/{table}/date=*/data.parquet"
    rows = con.execute(f"SELECT file FROM glob({_sql_literal(pattern)})").fetchall()
    days = {
        date.fromisoformat(
            next(part for part in path.split("/") if part.startswith("date="))[5:]
        )
        for (path,) in rows
    }
    return sorted(days)


def load_archive_updates(
    table: ArchiveTable,
    start: str | datetime | pd.Timestamp,
    end: str | datetime | pd.Timestamp,
    *,
    route_id: str | None = None,
    direction_id: int | None = None,
    columns: list[str] | None = None,
    env_file: str | Path | None = None,
) -> pd.DataFrame:
    """Load archived updates with ``start <= feed_timestamp <= end``.

    Parameters
    ----------
    table : {'trip_updates', 'vehicle_updates'}
        Archive table.
    start, end : str or datetime.datetime or pandas.Timestamp
        Inclusive UTC bounds on ``feed_timestamp``. Naive values are UTC;
        timezone-aware values are converted to UTC.
    route_id : str or None, optional
        Keep only this ``trip_route_id``.
    direction_id : int or None, optional
        Keep only this ``trip_direction_id``.
    columns : list[str] or None, optional
        Columns to read; all columns by default.
    env_file : str or pathlib.Path or None, optional
        Settings file holding the ``R2_*`` credentials.

    Returns
    -------
    pandas.DataFrame
        Rows ordered by ``id`` descending, with the same column names and
        dtypes as a ``pandas.read_sql`` download from MySQL (naive UTC
        timestamps, 64-bit integers).

    Raises
    ------
    ValueError
        If ``end`` is before ``start`` or the table is unknown.
    FileNotFoundError
        If any UTC day in the window is not archived yet.
    """
    _check_table(table)
    start_utc, end_utc = _to_naive_utc(start), _to_naive_utc(end)
    if end_utc < start_utc:
        raise ValueError("end must not be earlier than start.")
    con, bucket = connect_r2_archive(env_file)
    try:
        wanted = [
            start_utc.date() + timedelta(days=offset)
            for offset in range((end_utc.date() - start_utc.date()).days + 1)
        ]
        available = set(list_archive_days(table, env_file=env_file, con=con))
        missing = [day for day in wanted if day not in available]
        if missing:
            last = max(available).isoformat() if available else "none"
            raise FileNotFoundError(
                f"{table} is not archived for {len(missing)} requested day(s), "
                f"first {missing[0]} (latest archived day: {last}). Newer data "
                "is still only in MySQL until the nightly export runs."
            )
        files = ", ".join(_sql_literal(archive_uri(bucket, table, d)) for d in wanted)
        selected = (
            "*" if columns is None else ", ".join(_quote_name(c) for c in columns)
        )
        filters = [
            f"feed_timestamp >= {_sql_literal(start_utc.isoformat(sep=' '))}",
            f"feed_timestamp <= {_sql_literal(end_utc.isoformat(sep=' '))}",
        ]
        if route_id is not None:
            filters.append(f"trip_route_id = {_sql_literal(route_id)}")
        if direction_id is not None:
            filters.append(f"trip_direction_id = {int(direction_id)}")
        frame = con.execute(
            f"SELECT {selected} FROM read_parquet([{files}], hive_partitioning=false) "
            f"WHERE {' AND '.join(filters)} ORDER BY id DESC"
        ).df()
    finally:
        con.close()
    for name in frame.columns:
        if pd.api.types.is_integer_dtype(frame[name]):
            frame[name] = frame[name].astype("int64")
    return frame


def _to_naive_utc(value: str | datetime | pd.Timestamp) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp.to_pydatetime()


def _check_table(table: str) -> None:
    if table not in ARCHIVE_TABLES:
        raise ValueError(
            f"Unknown archive table {table!r}; choose from {list(ARCHIVE_TABLES)}."
        )


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _quote_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
