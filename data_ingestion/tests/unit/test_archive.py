from __future__ import annotations

from datetime import date

import pytest

from data_ingestion import archive

# pylint: disable=missing-docstring,protected-access

_R2 = {
    "R2_ACCOUNT_ID": "acct",
    "R2_BUCKET": "bucket",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
}
_TRANSIT_DB = {
    "TRANSIT_DB_HOST": "pi.local",
    "TRANSIT_DB_PORT": "3307",
    "TRANSIT_DB_USER": "transit",
    "TRANSIT_DB_PASSWORD": "pw",
    "TRANSIT_DB_NAME": "transitdb",
}
_MYSQL = {
    "MYSQL_HOST": "mysql",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "transit",
    "MYSQL_PASSWORD": "pw",
    "MYSQL_DATABASE": "transitdb",
}


def test_read_env_file_parses_comments_exports_and_quotes(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nexport TRANSIT_DB_HOST='a b'\nR2_BUCKET=bucket\nEMPTY=\n"
    )
    assert archive.read_env_file(path) == {
        "TRANSIT_DB_HOST": "a b",
        "R2_BUCKET": "bucket",
        "EMPTY": "",
    }


def test_read_env_file_missing_file_is_empty(tmp_path):
    assert archive.read_env_file(tmp_path / "absent.env") == {}
    assert archive.read_env_file(None) == {}


@pytest.mark.parametrize("content", ["NO_SEPARATOR", "KEY=two words"])
def test_read_env_file_rejects_malformed_lines(tmp_path, content):
    path = tmp_path / ".env"
    path.write_text(content)
    with pytest.raises(ValueError):
        archive.read_env_file(path)


def test_load_settings_accepts_laptop_transit_db_names():
    settings = archive.load_archive_settings(environ={**_TRANSIT_DB, **_R2})
    assert settings.mysql == {
        "host": "pi.local",
        "port": "3307",
        "user": "transit",
        "password": "pw",
        "database": "transitdb",
    }
    assert settings.r2["bucket"] == "bucket"


def test_load_settings_accepts_pi_docker_mysql_names():
    settings = archive.load_archive_settings(environ={**_MYSQL, **_R2})
    assert settings.mysql["host"] == "mysql"
    assert settings.mysql["port"] == "3306"


def test_load_settings_file_overrides_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text("TRANSIT_DB_HOST=from-file\n")
    settings = archive.load_archive_settings(path, environ={**_TRANSIT_DB, **_R2})
    assert settings.mysql["host"] == "from-file"


def test_load_settings_reports_missing_values():
    with pytest.raises(ValueError, match="R2_BUCKET"):
        archive.load_archive_settings(
            environ={
                **_TRANSIT_DB,
                **{k: v for k, v in _R2.items() if k != "R2_BUCKET"},
            }
        )


def test_load_settings_rejects_non_integer_port():
    with pytest.raises(ValueError, match="port"):
        archive.load_archive_settings(
            environ={**_TRANSIT_DB, "TRANSIT_DB_PORT": "abc", **_R2}
        )


def test_archive_uri_uses_daily_hive_partition():
    assert archive.archive_uri("bucket", "trip_updates", date(2026, 1, 2)) == (
        "r2://bucket/archive/trip_updates/date=2026-01-02/data.parquet"
    )


def test_unknown_table_is_rejected():
    with pytest.raises(ValueError, match="Unknown archive table"):
        archive.archive_uri("bucket", "other", date(2026, 1, 2))


def test_source_query_selects_half_open_utc_day():
    query = archive.source_query("vehicle_updates", date(2026, 12, 31))
    assert query == (
        "SELECT * FROM TRANSIT_VEHICLE_TABLE "
        "WHERE feed_timestamp >= '2026-12-31 00:00:00' "
        "AND feed_timestamp < '2027-01-01 00:00:00'"
    )


def test_day_range_is_end_exclusive():
    assert archive.day_range(date(2026, 1, 30), date(2026, 2, 2)) == [
        date(2026, 1, 30),
        date(2026, 1, 31),
        date(2026, 2, 1),
    ]
    assert not archive.day_range(date(2026, 2, 2), date(2026, 2, 2))


def test_sql_literal_escapes_quotes():
    assert archive._sql_literal("it's") == "'it''s'"


class _FakeConnection:
    """Record statements and answer the two count queries in order."""

    def __init__(self, counts):
        self.statements = []
        self._counts = list(counts)

    def execute(self, sql):
        self.statements.append(sql)
        return self

    def fetchone(self):
        return (self._counts.pop(0),)


def test_export_day_copies_sorted_rows_and_verifies_count():
    con = _FakeConnection([5, 5])
    result = archive.export_day(con, "bucket", "trip_updates", date(2026, 1, 2))
    assert result.rows == 5
    copy = next(sql for sql in con.statements if sql.startswith("COPY"))
    assert "ORDER BY trip_route_id, trip_direction_id, id" in copy
    assert "'r2://bucket/archive/trip_updates/date=2026-01-02/data.parquet'" in copy
    assert "COMPRESSION zstd" in copy


def test_export_day_raises_when_uploaded_count_differs():
    con = _FakeConnection([5, 4])
    with pytest.raises(RuntimeError, match="expected 5"):
        archive.export_day(con, "bucket", "trip_updates", date(2026, 1, 2))
