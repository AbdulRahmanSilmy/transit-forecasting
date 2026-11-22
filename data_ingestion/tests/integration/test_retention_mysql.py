"""Retention commands against a real MySQL server, on test-only tables.

R2 verification is replaced by a stub; everything else (day counts, batched
deletes, and the copy-and-swap compaction under concurrent inserts) runs
real SQL. Enable with ``RUN_INTEGRATION_TESTS=1`` and ``MYSQL_*`` settings.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta

import mysql.connector
import pytest

from data_ingestion import archive, retention

# pylint: disable=missing-docstring,redefined-outer-name

_TABLES = {
    "trip_updates": "TEST_RETENTION_TRIP",
    "vehicle_updates": "TEST_RETENTION_VEHICLE",
}
_TODAY = date(2026, 1, 21)
_DAYS = 20
_ROWS_PER_DAY = 50


@pytest.fixture
def settings(monkeypatch):
    if os.getenv("RUN_INTEGRATION_TESTS", "0").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        pytest.skip("Integration tests require RUN_INTEGRATION_TESTS=1")
    try:
        loaded = archive.load_archive_settings(
            environ={
                **os.environ,
                "R2_ACCOUNT_ID": "unused",
                "R2_BUCKET": "unused",
                "R2_ACCESS_KEY_ID": "unused",
                "R2_SECRET_ACCESS_KEY": "unused",
            }
        )
    except ValueError as error:
        pytest.skip(str(error))
    monkeypatch.setattr(archive, "ARCHIVE_TABLES", _TABLES)
    conn = retention.mysql_connect(loaded)
    cursor = conn.cursor()
    for name in _TABLES.values():
        for table in (
            name,
            name + retention.BACKUP_SUFFIX,
            name + retention.COMPACT_SUFFIX,
        ):
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute(
            f"CREATE TABLE {name} (id INT AUTO_INCREMENT PRIMARY KEY, "
            "feed_timestamp TIMESTAMP, trip_route_id VARCHAR(20), "
            "INDEX idx_feed_timestamp (feed_timestamp))"
        )
        start = datetime.combine(_TODAY - timedelta(days=_DAYS), datetime.min.time())
        rows = [
            (start + timedelta(days=d, minutes=10 * r), "97-KEL")
            for d in range(_DAYS)
            for r in range(_ROWS_PER_DAY)
        ]
        cursor.executemany(
            f"INSERT INTO {name} (feed_timestamp, trip_route_id) VALUES (%s, %s)", rows
        )
    conn.commit()
    yield loaded
    for name in _TABLES.values():
        for table in (
            name,
            name + retention.BACKUP_SUFFIX,
            name + retention.COMPACT_SUFFIX,
        ):
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


def _verify_all_but(monkeypatch, bad_day=None):
    def counts(con, bucket, table):
        return {
            _TODAY - timedelta(days=offset): _ROWS_PER_DAY
            for offset in range(1, _DAYS + 1)
            if _TODAY - timedelta(days=offset) != bad_day
        }

    monkeypatch.setattr(archive, "connect", lambda settings: _Closable())
    monkeypatch.setattr(retention, "archived_day_counts", counts)


class _Closable:
    def close(self):
        pass


def _count(settings, name, where="1=1"):
    conn = retention.mysql_connect(settings)
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {name} WHERE {where}")
    (rows,) = cursor.fetchone()
    conn.close()
    return rows


def test_prune_deletes_verified_old_days_only(settings, monkeypatch):
    bad_day = _TODAY - timedelta(days=17)
    _verify_all_but(monkeypatch, bad_day)
    monkeypatch.setattr(retention, "DELETE_BATCH_ROWS", 7)
    deleted = retention.prune(settings, retain_days=14, today=_TODAY)
    # 6 old days per table (offsets 15-20), one of them unverified.
    assert len(deleted) == 2 * 5
    for name in _TABLES.values():
        assert _count(settings, name) == (14 + 1) * _ROWS_PER_DAY
        assert _count(settings, name, f"DATE(feed_timestamp) = '{bad_day}'") == 50


def test_compact_swaps_in_retained_rows_without_losing_concurrent_inserts(
    settings, monkeypatch
):
    _verify_all_but(monkeypatch)
    monkeypatch.setattr(retention, "COPY_BATCH_ROWS", 40)
    stop = threading.Event()
    inserted = {name: 0 for name in _TABLES.values()}

    def ingest():
        conn = mysql.connector.connect(
            **{**settings.mysql, "port": int(settings.mysql["port"])}
        )
        cursor = conn.cursor()
        while not stop.is_set():
            for name in _TABLES.values():
                cursor.execute(
                    f"INSERT INTO {name} (feed_timestamp, trip_route_id) "
                    "VALUES (UTC_TIMESTAMP(), 'live')"
                )
                conn.commit()
                inserted[name] += 1
        conn.close()

    writer = threading.Thread(target=ingest)
    writer.start()
    try:
        rows = retention.compact(settings, retain_days=14, today=_TODAY)
    finally:
        stop.set()
        writer.join()
    for table, name in _TABLES.items():
        assert rows[table] >= 14 * _ROWS_PER_DAY
        kept = _count(settings, name, "trip_route_id = '97-KEL'")
        assert kept == 14 * _ROWS_PER_DAY
        assert _count(settings, name, "trip_route_id = 'live'") == inserted[name]
        assert _count(settings, name + retention.BACKUP_SUFFIX) >= _DAYS * _ROWS_PER_DAY
    with pytest.raises(RuntimeError, match="drop-backup"):
        retention.compact(settings, retain_days=14, today=_TODAY)
    assert sorted(retention.drop_backups(settings)) == sorted(
        name + retention.BACKUP_SUFFIX for name in _TABLES.values()
    )
