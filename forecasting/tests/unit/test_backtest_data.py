import pandas as pd
import pytest

from forecasting import backtest_data, download_backtest_data


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
