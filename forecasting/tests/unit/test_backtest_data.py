import os
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from forecasting import (
    BacktestDataset,
    backtest_data,
    download_backtest_data,
    get_backtest_dataset,
)


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_downloads_vehicle_and_trip_updates_and_closes_connection(monkeypatch) -> None:
    connection = _FakeConnection()
    connect_calls = []
    query_calls = []
    vehicle_updates = pd.DataFrame({"id": [2, 1]})
    trip_updates = pd.DataFrame({"id": [4, 3]})

    def fake_connect(**parameters):
        connect_calls.append(parameters)
        return connection

    def fake_read_sql_query(query, conn, params):
        query_calls.append((query, conn, params))
        if "TRANSIT_VEHICLE_TABLE" in query:
            return vehicle_updates
        return trip_updates

    monkeypatch.setattr(backtest_data.mysql.connector, "connect", fake_connect)
    monkeypatch.setattr(backtest_data.pd, "read_sql_query", fake_read_sql_query)

    result_vehicle_updates, result_trip_updates = download_backtest_data(
        connection_parameters={
            "host": "example.test",
            "user": "reader",
            "password": "test-password",
            "database": "transitdb",
            "port": "3307",
        }
    )

    assert result_vehicle_updates is vehicle_updates
    assert result_trip_updates is trip_updates
    assert connect_calls == [
        {
            "host": "example.test",
            "user": "reader",
            "password": "test-password",
            "database": "transitdb",
            "port": 3307,
        }
    ]
    assert len(query_calls) == 2
    assert "TRANSIT_VEHICLE_TABLE" in query_calls[0][0]
    assert "TRANSIT_TRIP_TABLE" in query_calls[1][0]
    assert all(call[2] == (3,) for call in query_calls)
    assert all(call[1] is connection for call in query_calls)
    assert connection.closed


def test_download_requires_database_environment_variables(monkeypatch) -> None:
    for variable in (
        "TRANSIT_DB_HOST",
        "TRANSIT_DB_USER",
        "TRANSIT_DB_PASSWORD",
        "TRANSIT_DB_NAME",
        "TRANSIT_DB_PORT",
    ):
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(RuntimeError, match="TRANSIT_DB_HOST"):
        download_backtest_data()


@pytest.mark.parametrize("lookback_hours", [0, -1, 1.5, True])
def test_download_rejects_invalid_lookback_hours(lookback_hours) -> None:
    with pytest.raises(ValueError, match="lookback_hours"):
        download_backtest_data(lookback_hours=lookback_hours)


@pytest.mark.parametrize(
    ("dataset_id", "selected"), [(None, "newest"), ("older", "older")]
)
def test_get_dataset_selects_cache_without_credentials_or_downloads(
    tmp_path: Path, monkeypatch, dataset_id: str | None, selected: str
) -> None:
    vehicle, trip = pd.DataFrame({"id": [1]}), pd.DataFrame({"id": [2]})
    load = Mock(return_value=(vehicle, trip))
    monkeypatch.setattr(
        backtest_data,
        "list_backtest_datasets",
        Mock(return_value=pd.DataFrame({"dataset_id": ["newest", "older"]})),
    )
    monkeypatch.setattr(backtest_data, "load_backtest_dataset", load)
    monkeypatch.setattr(
        backtest_data,
        "_connection_parameters_from_env_file",
        Mock(side_effect=AssertionError("Cache must not read credentials")),
    )
    monkeypatch.setattr(
        backtest_data,
        "download_backtest_data",
        Mock(side_effect=AssertionError("Cache must not download")),
    )

    result = get_backtest_dataset(tmp_path, dataset_id=dataset_id)

    assert isinstance(result, BacktestDataset)
    assert result.directory == tmp_path / "backtests" / selected
    assert result.static_data_dir == result.directory / "static_gtfs"
    assert result.vehicle_updates is vehicle
    assert result.trip_updates is trip
    load.assert_called_once_with(result.directory)


@pytest.mark.parametrize(
    ("saved_ids", "dataset_id", "message"),
    [
        ([], None, "No saved backtests"),
        (["saved"], "missing", "Unknown backtest dataset"),
    ],
)
def test_get_dataset_reports_missing_cache_without_fallback(
    tmp_path: Path, monkeypatch, saved_ids, dataset_id, message: str
) -> None:
    monkeypatch.setattr(
        backtest_data,
        "list_backtest_datasets",
        Mock(return_value=pd.DataFrame({"dataset_id": saved_ids})),
    )
    download = Mock(side_effect=AssertionError("No fallback download"))
    monkeypatch.setattr(backtest_data, "download_backtest_data", download)

    with pytest.raises(FileNotFoundError, match=message):
        get_backtest_dataset(tmp_path, dataset_id=dataset_id)

    download.assert_not_called()


def test_get_dataset_propagates_corrupt_cache_errors(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        backtest_data,
        "list_backtest_datasets",
        Mock(return_value=pd.DataFrame({"dataset_id": ["saved"]})),
    )
    monkeypatch.setattr(
        backtest_data,
        "load_backtest_dataset",
        Mock(side_effect=ValueError("checksum mismatch")),
    )
    download = Mock(side_effect=AssertionError("No fallback download"))
    monkeypatch.setattr(backtest_data, "download_backtest_data", download)

    with pytest.raises(ValueError, match="checksum mismatch"):
        get_backtest_dataset(tmp_path)

    download.assert_not_called()


@pytest.fixture
def dataset_downloads(tmp_path: Path, monkeypatch):
    download = Mock(return_value=(pd.DataFrame({"id": [1]}), pd.DataFrame({"id": [2]})))
    static = Mock(return_value=tmp_path / "static-source")
    save = Mock(return_value=tmp_path / "backtests/new-snapshot")
    monkeypatch.setattr(backtest_data, "download_backtest_data", download)
    monkeypatch.setattr(backtest_data, "download_static_gtfs", static)
    monkeypatch.setattr(backtest_data, "save_backtest_dataset", save)
    return download, static, save


def test_get_dataset_downloads_and_saves_matching_static_gtfs(
    tmp_path: Path, dataset_downloads
) -> None:
    download, static, save = dataset_downloads

    result = get_backtest_dataset(tmp_path, source="download", lookback_hours=24)

    download.assert_called_once_with(lookback_hours=24, connection_parameters=None)
    static.assert_called_once_with(tmp_path / "static_gtfs_cache")
    save.assert_called_once_with(
        tmp_path / "backtests",
        *download.return_value,
        static.return_value,
        lookback_hours=24,
        source="database",
    )
    assert result.directory == save.return_value
    assert result.vehicle_updates is download.return_value[0]
    assert result.trip_updates is download.return_value[1]


def test_get_dataset_reads_file_overrides_without_changing_environment(
    tmp_path: Path, monkeypatch, dataset_downloads
) -> None:
    for variable in backtest_data._CONNECTION_ENV_VARS.values():
        monkeypatch.setenv(variable, "environment")
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "# Database settings\n\nexport TRANSIT_DB_HOST='file.example.test'\n"
        "TRANSIT_DB_PASSWORD='two words'\n"
    )
    original_environment = dict(os.environ)

    get_backtest_dataset(tmp_path, source="download", env_file=env_file)

    parameters = dataset_downloads[0].call_args.kwargs["connection_parameters"]
    assert parameters == {
        "host": "file.example.test",
        "password": "two words",
        "user": "environment",
        "database": "environment",
        "port": "environment",
    }
    assert dict(os.environ) == original_environment


@pytest.mark.parametrize(
    "content",
    [
        "UNKNOWN=value",
        "TRANSIT_DB_HOST=two words",
        "TRANSIT_DB_HOST=",
        "TRANSIT_DB_HOST='unclosed",
    ],
)
def test_get_dataset_reports_malformed_environment_files(
    tmp_path: Path, dataset_downloads, content: str
) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text(content)

    with pytest.raises(ValueError):
        get_backtest_dataset(tmp_path, source="download", env_file=env_file)

    for operation in dataset_downloads:
        operation.assert_not_called()


def test_get_dataset_missing_optional_file_uses_existing_environment(
    tmp_path: Path, dataset_downloads
) -> None:
    get_backtest_dataset(tmp_path, source="download", env_file=tmp_path / "absent.env")

    dataset_downloads[0].assert_called_once_with(
        lookback_hours=48, connection_parameters=None
    )


@pytest.mark.parametrize("failing_operation", [0, 1])
def test_get_dataset_does_not_save_failed_downloads(
    tmp_path: Path, dataset_downloads, failing_operation: int
) -> None:
    dataset_downloads[failing_operation].side_effect = OSError("Download failed")

    with pytest.raises(OSError, match="Download failed"):
        get_backtest_dataset(tmp_path, source="download")

    dataset_downloads[2].assert_not_called()


def test_get_dataset_rejects_unknown_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Choose source"):
        get_backtest_dataset(tmp_path, source="other")
