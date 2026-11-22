from __future__ import annotations

from datetime import date

import pytest

from data_ingestion import archive, retention

# pylint: disable=missing-docstring,protected-access

_SETTINGS = archive.ArchiveSettings(
    mysql={
        "host": "h",
        "port": "1",
        "user": "u",
        "password": "p",
        "database": "d",
    },
    r2={
        "account_id": "a",
        "bucket": "b",
        "access_key_id": "k",
        "secret_access_key": "s",
    },
)


def test_retention_cutoff_keeps_complete_days_before_today():
    assert retention.retention_cutoff(date(2026, 11, 19), 14) == date(2026, 11, 5)


def test_retention_cutoff_rejects_non_positive_window():
    with pytest.raises(ValueError):
        retention.retention_cutoff(date(2026, 11, 19), 0)


def test_compare_counts_verifies_only_matching_archived_days():
    checks = retention.compare_counts(
        "trip_updates",
        {
            date(2026, 1, 3): 7,
            date(2026, 1, 1): 5,
            date(2026, 1, 2): 6,
            date(2026, 1, 4): 0,
        },
        {date(2026, 1, 1): 5, date(2026, 1, 2): 4},
    )
    assert [(c.day.day, c.verified) for c in checks] == [
        (1, True),
        (2, False),
        (3, False),
    ]
    assert "verified" in checks[0].describe()
    assert "R2 has 4 rows" in checks[1].describe()
    assert "missing from R2" in checks[2].describe()


class _Cursor:
    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.statements = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.rowcount = self.rowcounts.pop(0)

    def close(self):
        pass


class _Connection:
    def __init__(self, rowcounts):
        self.cur = _Cursor(rowcounts)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_delete_day_deletes_in_committed_batches_within_the_day():
    conn = _Connection([3, 3, 1])
    assert (
        retention.delete_day(conn, "trip_updates", date(2026, 1, 2), batch_rows=3) == 7
    )
    assert conn.commits == 3
    sql, params = conn.cur.statements[0]
    assert sql.startswith("DELETE FROM TRANSIT_TRIP_TABLE")
    assert params == ("2026-01-02 00:00:00", "2026-01-03 00:00:00", 3)


def _patch_checks(monkeypatch, checks):
    monkeypatch.setattr(retention, "check_old_days", lambda *a, **k: checks)
    deleted = []
    monkeypatch.setattr(retention, "mysql_connect", lambda settings: _Connection([]))
    monkeypatch.setattr(
        retention,
        "delete_day",
        lambda conn, table, day: deleted.append((table, day)) or 1,
    )
    return deleted


def _check(day, mysql_rows, archived_rows):
    return retention.DayCheck(
        "trip_updates", date(2026, 1, day), mysql_rows, archived_rows
    )


def test_prune_deletes_only_verified_days(monkeypatch):
    deleted = _patch_checks(
        monkeypatch, [_check(1, 5, 5), _check(2, 5, None), _check(3, 5, 4)]
    )
    result = retention.prune(_SETTINGS, retain_days=14)
    assert deleted == [("trip_updates", date(2026, 1, 1))]
    assert [c.day.day for c in result] == [1]


def test_prune_dry_run_deletes_nothing(monkeypatch):
    deleted = _patch_checks(monkeypatch, [_check(1, 5, 5)])
    assert len(retention.prune(_SETTINGS, retain_days=14, dry_run=True)) == 1
    assert not deleted


def test_compact_refuses_when_an_old_day_is_unverified(monkeypatch):
    _patch_checks(monkeypatch, [_check(1, 5, 5), _check(2, 5, None)])
    with pytest.raises(RuntimeError, match="not verified"):
        retention.compact(_SETTINGS, retain_days=14, today=date(2026, 2, 1))


class _CompactCursor:
    """Answer the compaction queries for one table with retained ids 11-25."""

    def __init__(self, backup_exists=False):
        self.statements = []
        self._backup_exists = backup_exists
        self._result = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.startswith("SHOW TABLES"):
            self._result = [("x",)] if self._backup_exists else []
        elif sql.startswith("SELECT MIN(id)"):
            self._result = [(11, 25)]
        elif sql.startswith("SELECT COUNT(*)"):
            self._result = [(17,)]

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0]

    def close(self):
        pass


def test_compact_table_copies_in_batches_then_swaps_under_lock(monkeypatch):
    monkeypatch.setattr(retention, "COPY_BATCH_ROWS", 10)
    conn = _Connection([])
    conn.cur = _CompactCursor()
    assert retention._compact_table(conn, "T", date(2026, 1, 5)) == 17
    sql = [s for s, _ in conn.cur.statements]
    params = [p for _, p in conn.cur.statements]
    copies = [p for s, p in conn.cur.statements if s.startswith("INSERT") and "<=" in s]
    assert copies == [(10, 20), (20, 25)]
    lock = sql.index("LOCK TABLES T WRITE, T_compact_new WRITE")
    assert sql[lock + 1].startswith("INSERT INTO T_compact_new")
    assert params[lock + 1] == (25,)
    assert sql[lock + 2] == "RENAME TABLE T TO T_pre_compact, T_compact_new TO T"
    assert sql[lock + 3] == "UNLOCK TABLES"


def test_compact_table_refuses_when_backup_exists():
    conn = _Connection([])
    conn.cur = _CompactCursor(backup_exists=True)
    with pytest.raises(RuntimeError, match="drop-backup"):
        retention._compact_table(conn, "T", date(2026, 1, 5))
    assert not any(s.startswith("CREATE") for s, _ in conn.cur.statements)


def test_drop_backup_requires_yes(monkeypatch):
    monkeypatch.setattr(archive, "load_archive_settings", lambda env_file: _SETTINGS)
    monkeypatch.setattr(
        retention, "drop_backups", lambda settings: pytest.fail("dropped")
    )
    with pytest.raises(SystemExit):
        retention.main(["drop-backup"])
