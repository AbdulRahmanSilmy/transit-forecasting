"""Remove realtime rows from MySQL once their days are verified in R2.

A UTC day of a table is *verified* when its R2 Parquet file exists and holds
exactly as many rows as MySQL has for that day. Only verified days older than
the retention window are ever removed; a day missing from R2, or whose counts
differ, is kept and reported.

Commands (``python -m data_ingestion.retention --help``):

``check``
    Report which days older than the window are verified. Changes nothing.
``prune``
    Delete verified old days in small batches. Run nightly after the export.
    InnoDB reuses the freed pages, so the table files stop growing, but they
    do not shrink.
``compact``
    One-time rebuild that shrinks the table files: copy the retained rows to
    a new table, then swap it in with ``RENAME TABLE``. Refuses to run unless
    every old day is verified. The original tables are kept as
    ``<table>_pre_compact`` until ``drop-backup``.
``drop-backup``
    Drop the ``<table>_pre_compact`` tables to free their disk space. This
    cannot be undone and requires ``--yes``.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from . import archive

logger = logging.getLogger(__name__)

DEFAULT_RETAIN_DAYS = 14
DELETE_BATCH_ROWS = 50_000
COPY_BATCH_ROWS = 200_000
BACKUP_SUFFIX = "_pre_compact"
COMPACT_SUFFIX = "_compact_new"


@dataclass(frozen=True)
class DayCheck:
    """Row counts of one table-day in MySQL and in the R2 archive.

    Parameters
    ----------
    table : str
        Archive table name, such as ``"trip_updates"``.
    day : datetime.date
        UTC day.
    mysql_rows : int
        Rows in MySQL for the day.
    archived_rows : int or None
        Rows in the day's R2 file, or None if the file does not exist.
    """

    table: str
    day: date
    mysql_rows: int
    archived_rows: int | None

    @property
    def verified(self) -> bool:
        """bool: Whether the R2 file exists and matches the MySQL count."""
        return self.archived_rows == self.mysql_rows

    def describe(self) -> str:
        """Return a one-line, human-readable status of the check.

        Returns
        -------
        str
            Table, day, and the verification result.
        """
        if self.archived_rows is None:
            status = "missing from R2"
        elif self.verified:
            status = "verified"
        else:
            status = f"R2 has {self.archived_rows} rows"
        return f"{self.table} {self.day}: {self.mysql_rows} rows in MySQL, {status}"


def retention_cutoff(today: date, retain_days: int) -> date:
    """Return the first UTC day kept in MySQL.

    Parameters
    ----------
    today : datetime.date
        Current UTC day.
    retain_days : int
        Complete days to keep before ``today``; must be at least 1.

    Returns
    -------
    datetime.date
        Days strictly before this one may be removed.

    Raises
    ------
    ValueError
        If ``retain_days`` is less than 1.
    """
    if retain_days < 1:
        raise ValueError("retain_days must be at least 1.")
    return today - timedelta(days=retain_days)


def compare_counts(
    table: str,
    mysql_counts: Mapping[date, int],
    archived_counts: Mapping[date, int],
) -> list[DayCheck]:
    """Pair MySQL day counts with archived day counts.

    Parameters
    ----------
    table : str
        Archive table name.
    mysql_counts : collections.abc.Mapping[datetime.date, int]
        Rows per UTC day in MySQL; days with no rows may be omitted.
    archived_counts : collections.abc.Mapping[datetime.date, int]
        Rows per UTC day in R2.

    Returns
    -------
    list[DayCheck]
        One check per MySQL day that has rows, sorted by day.
    """
    return [
        DayCheck(table, day, rows, archived_counts.get(day))
        for day, rows in sorted(mysql_counts.items())
        if rows > 0
    ]


def mysql_connect(settings: archive.ArchiveSettings):
    """Open a read-write MySQL connection that works in UTC.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Settings whose ``mysql`` part is used.

    Returns
    -------
    mysql.connector.connection.MySQLConnection
        Connection with ``time_zone`` set to UTC and ``READ COMMITTED``
        isolation, so long copies take no shared locks on source rows.
    """
    import mysql.connector  # pylint: disable=import-outside-toplevel

    params = settings.mysql
    conn = mysql.connector.connect(
        host=params["host"],
        port=int(params["port"]),
        user=params["user"],
        password=params["password"],
        database=params["database"],
    )
    cursor = conn.cursor()
    cursor.execute("SET time_zone = '+00:00'")
    cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cursor.close()
    return conn


def mysql_day_counts(cursor, table: str, before: date) -> dict[date, int]:
    """Count MySQL rows per UTC day for every day before ``before``.

    Parameters
    ----------
    cursor : mysql.connector.cursor.MySQLCursor
        Cursor on a connection from :func:`mysql_connect`.
    table : str
        Archive table name.
    before : datetime.date
        Exclusive upper bound.

    Returns
    -------
    dict[datetime.date, int]
        Row counts for days that have rows.
    """
    cursor.execute(
        f"SELECT DATE(feed_timestamp), COUNT(*) FROM {archive.ARCHIVE_TABLES[table]} "
        "WHERE feed_timestamp < %s GROUP BY DATE(feed_timestamp)",
        (_day_start(before),),
    )
    return {_as_date(day): int(rows) for day, rows in cursor.fetchall()}


def archived_day_counts(con, bucket: str, table: str) -> dict[date, int]:
    """Read each archived day's row count from its Parquet footer.

    Parameters
    ----------
    con : duckdb.DuckDBPyConnection
        Connection from :func:`data_ingestion.archive.connect`.
    bucket : str
        R2 bucket name.
    table : str
        Archive table name.

    Returns
    -------
    dict[datetime.date, int]
        Row counts per archived UTC day.
    """
    pattern = archive.archive_uri(bucket, table, date(2000, 1, 1)).replace(
        "date=2000-01-01", "date=*"
    )
    rows = con.execute(
        "SELECT file_name, sum(num_rows) FROM parquet_file_metadata("
        f"{archive._sql_literal(pattern)}) GROUP BY file_name"
    ).fetchall()
    counts = {}
    for path, num_rows in rows:
        part = next(p for p in path.split("/") if p.startswith("date="))
        counts[date.fromisoformat(part.removeprefix("date="))] = int(num_rows)
    return counts


def check_old_days(
    settings: archive.ArchiveSettings,
    *,
    retain_days: int,
    today: date | None = None,
    tables: Iterable[str] | None = None,
) -> list[DayCheck]:
    """Verify every MySQL day older than the retention window against R2.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Source and archive settings.
    retain_days : int
        Complete days to keep before today.
    today : datetime.date or None, optional
        Current UTC day; defaults to the real one.
    tables : collections.abc.Iterable[str] or None, optional
        Archive table names; defaults to all.

    Returns
    -------
    list[DayCheck]
        Checks for every old day that still has rows in MySQL.
    """
    cutoff = retention_cutoff(_today(today), retain_days)
    con = archive.connect(settings)
    conn = mysql_connect(settings)
    try:
        cursor = conn.cursor()
        checks = []
        for table in sorted(tables or archive.ARCHIVE_TABLES):
            checks += compare_counts(
                table,
                mysql_day_counts(cursor, table, cutoff),
                archived_day_counts(con, settings.r2["bucket"], table),
            )
        cursor.close()
    finally:
        conn.close()
        con.close()
    return checks


def delete_day(conn, table: str, day: date, *, batch_rows: int = DELETE_BATCH_ROWS):
    """Delete one UTC day of a MySQL table in small committed batches.

    Parameters
    ----------
    conn : mysql.connector.connection.MySQLConnection
        Connection from :func:`mysql_connect`.
    table : str
        Archive table name.
    day : datetime.date
        UTC day to delete.
    batch_rows : int, optional
        Rows per ``DELETE`` statement, which keeps each transaction and its
        undo log small on the Raspberry Pi.

    Returns
    -------
    int
        Rows deleted.
    """
    deleted = 0
    cursor = conn.cursor()
    try:
        while True:
            cursor.execute(
                f"DELETE FROM {archive.ARCHIVE_TABLES[table]} "
                "WHERE feed_timestamp >= %s AND feed_timestamp < %s LIMIT %s",
                (_day_start(day), _day_start(day + timedelta(days=1)), batch_rows),
            )
            conn.commit()
            deleted += cursor.rowcount
            if cursor.rowcount < batch_rows:
                return deleted
    finally:
        cursor.close()


def prune(
    settings: archive.ArchiveSettings,
    *,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    today: date | None = None,
    dry_run: bool = False,
) -> list[DayCheck]:
    """Delete verified MySQL days older than the retention window.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Source and archive settings.
    retain_days : int, optional
        Complete days to keep before today.
    today : datetime.date or None, optional
        Current UTC day; defaults to the real one.
    dry_run : bool, optional
        Report what would be deleted without deleting.

    Returns
    -------
    list[DayCheck]
        Checks of the verified days that were (or would be) deleted.
    """
    checks = check_old_days(settings, retain_days=retain_days, today=today)
    for check in checks:
        if not check.verified:
            logger.warning("Keeping %s", check.describe())
    verified = [check for check in checks if check.verified]
    if dry_run:
        for check in verified:
            logger.info("Would delete %s", check.describe())
        return verified
    conn = mysql_connect(settings)
    try:
        for check in verified:
            deleted = delete_day(conn, check.table, check.day)
            logger.info("Deleted %s (%d rows)", check.describe(), deleted)
    finally:
        conn.close()
    return verified


def compact(
    settings: archive.ArchiveSettings,
    *,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    today: date | None = None,
) -> dict[str, int]:
    """Rebuild each table with only the retention window, keeping a backup.

    Retained rows are copied to ``<table>_compact_new`` in ``id`` batches
    while ingestion keeps writing. Both tables are then write-locked for the
    few seconds it takes to copy the newest rows and swap the names, so no
    row is lost. The original table is left as ``<table>_pre_compact``.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Source and archive settings.
    retain_days : int, optional
        Complete days to keep before today.
    today : datetime.date or None, optional
        Current UTC day; defaults to the real one.

    Returns
    -------
    dict[str, int]
        Rows in each rebuilt table.

    Raises
    ------
    RuntimeError
        If any old day is not verified in R2, or a backup table from an
        earlier compaction still exists.
    """
    today = _today(today)
    checks = check_old_days(settings, retain_days=retain_days, today=today)
    unverified = [check for check in checks if not check.verified]
    if unverified:
        raise RuntimeError(
            f"{len(unverified)} old day(s) are not verified in R2; archive them "
            "before compacting. First: " + unverified[0].describe()
        )
    cutoff = retention_cutoff(today, retain_days)
    conn = mysql_connect(settings)
    rows = {}
    try:
        for table in sorted(archive.ARCHIVE_TABLES):
            rows[table] = _compact_table(conn, archive.ARCHIVE_TABLES[table], cutoff)
    finally:
        conn.close()
    return rows


def _compact_table(conn, name: str, cutoff: date) -> int:
    backup, new = name + BACKUP_SUFFIX, name + COMPACT_SUFFIX
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW TABLES LIKE %s", (backup,))
        if cursor.fetchall():
            raise RuntimeError(
                f"{backup} already exists; run drop-backup before compacting again."
            )
        cursor.execute(f"DROP TABLE IF EXISTS {new}")
        cursor.execute(f"CREATE TABLE {new} LIKE {name}")
        cursor.execute(
            f"SELECT MIN(id), MAX(id) FROM {name} WHERE feed_timestamp >= %s",
            (_day_start(cutoff),),
        )
        first, last = cursor.fetchone()
        copied = first - 1 if first is not None else _max_id(cursor, name)
        last = copied if last is None else last
        logger.info("%s: copying ids %s to %s into %s", name, copied + 1, last, new)
        while copied < last:
            upper = min(copied + COPY_BATCH_ROWS, last)
            cursor.execute(
                f"INSERT INTO {new} SELECT * FROM {name} WHERE id > %s AND id <= %s",
                (copied, upper),
            )
            conn.commit()
            copied = upper
        cursor.execute(f"LOCK TABLES {name} WRITE, {new} WRITE")
        try:
            cursor.execute(
                f"INSERT INTO {new} SELECT * FROM {name} WHERE id > %s", (copied,)
            )
            conn.commit()
            cursor.execute(f"RENAME TABLE {name} TO {backup}, {new} TO {name}")
        finally:
            cursor.execute("UNLOCK TABLES")
        cursor.execute(f"SELECT COUNT(*) FROM {name}")
        (count,) = cursor.fetchone()
        logger.info(
            "%s: rebuilt with %d rows; original kept as %s", name, count, backup
        )
        return int(count)
    finally:
        cursor.close()


def drop_backups(settings: archive.ArchiveSettings) -> list[str]:
    """Drop the ``<table>_pre_compact`` tables left by :func:`compact`.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Settings whose ``mysql`` part is used.

    Returns
    -------
    list[str]
        Names of the dropped tables.
    """
    conn = mysql_connect(settings)
    dropped = []
    try:
        cursor = conn.cursor()
        for name in sorted(archive.ARCHIVE_TABLES.values()):
            backup = name + BACKUP_SUFFIX
            cursor.execute("SHOW TABLES LIKE %s", (backup,))
            if cursor.fetchall():
                cursor.execute(f"DROP TABLE {backup}")
                logger.info("Dropped %s", backup)
                dropped.append(backup)
        cursor.close()
    finally:
        conn.close()
    return dropped


def _max_id(cursor, name: str) -> int:
    cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {name}")
    return int(cursor.fetchone()[0])


def _today(today: date | None) -> date:
    return datetime.now(timezone.utc).date() if today is None else today


def _day_start(day: date) -> str:
    return f"{day.isoformat()} 00:00:00"


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def main(argv: list[str] | None = None) -> None:
    """Run a retention command; see the module docstring.

    Parameters
    ----------
    argv : list[str] or None, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "prune", "compact", "drop-backup"])
    parser.add_argument(
        "--retain-days",
        type=int,
        default=DEFAULT_RETAIN_DAYS,
        help=f"Complete UTC days to keep in MySQL (default: {DEFAULT_RETAIN_DAYS}).",
    )
    parser.add_argument("--env-file", type=Path, help="Settings file to read.")
    parser.add_argument(
        "--dry-run", action="store_true", help="prune: report without deleting."
    )
    parser.add_argument(
        "--yes", action="store_true", help="drop-backup: confirm the drop."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    settings = archive.load_archive_settings(args.env_file)
    if args.command == "check":
        checks = check_old_days(settings, retain_days=args.retain_days)
        for check in checks:
            logger.info(check.describe())
        logger.info(
            "%d of %d old table-days verified",
            sum(check.verified for check in checks),
            len(checks),
        )
    elif args.command == "prune":
        prune(settings, retain_days=args.retain_days, dry_run=args.dry_run)
    elif args.command == "compact":
        compact(settings, retain_days=args.retain_days)
    elif not args.yes:
        parser.error("drop-backup permanently deletes the backup tables; add --yes.")
    else:
        drop_backups(settings)


if __name__ == "__main__":
    main()
