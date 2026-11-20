from datetime import date, datetime

import pandas as pd
import pytest

from forecasting import r2_archive

_SETTINGS = {
    "R2_ACCOUNT_ID": "acct",
    "R2_BUCKET": "bucket",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
}


def test_read_r2_settings_from_shared_env_file(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# shared settings\nTRANSIT_DB_HOST=pi\n"
        + "".join(f"export {k}='{v}'\n" for k, v in _SETTINGS.items())
    )
    assert r2_archive.read_r2_settings(path, environ={}) == {
        "account_id": "acct",
        "bucket": "bucket",
        "access_key_id": "key",
        "secret_access_key": "secret",
    }


def test_read_r2_settings_falls_back_to_environment(tmp_path) -> None:
    settings = r2_archive.read_r2_settings(tmp_path / "absent.env", environ=_SETTINGS)
    assert settings["bucket"] == "bucket"


def test_read_r2_settings_reports_missing_values() -> None:
    with pytest.raises(ValueError, match="R2_SECRET_ACCESS_KEY"):
        r2_archive.read_r2_settings(
            None,
            environ={k: v for k, v in _SETTINGS.items() if k != "R2_SECRET_ACCESS_KEY"},
        )


def test_archive_uri_matches_exporter_layout() -> None:
    assert r2_archive.archive_uri("bucket", "vehicle_updates", date(2026, 3, 4)) == (
        "r2://bucket/archive/vehicle_updates/date=2026-03-04/data.parquet"
    )


def test_unknown_table_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown archive table"):
        r2_archive.archive_uri("bucket", "TRANSIT_TRIP_TABLE", date(2026, 3, 4))


def test_aware_bounds_are_converted_to_naive_utc() -> None:
    value = pd.Timestamp("2026-11-01 17:00", tz="America/Vancouver")
    assert r2_archive._to_naive_utc(value) == datetime(2026, 11, 2, 1, 0)
    assert r2_archive._to_naive_utc("2026-11-01 17:00") == datetime(2026, 11, 1, 17, 0)


def test_load_rejects_reversed_window() -> None:
    with pytest.raises(ValueError, match="end must not be earlier"):
        r2_archive.load_archive_updates(
            "trip_updates", "2026-11-02", "2026-11-01", env_file=None
        )


def test_sql_literal_escapes_quotes() -> None:
    assert r2_archive._sql_literal("97-'KEL") == "'97-''KEL'"
