from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from forecasting import benchmark_download_speed as benchmark


def _connection_parameters():
    return {
        "host": "example.test",
        "user": "reader",
        "password": "test-password",
        "database": "transitdb",
        "port": "3306",
    }


def test_benchmark_uses_fixed_windows_and_saves_real_measurements(
    tmp_path: Path, monkeypatch
) -> None:
    endpoint = datetime(2026, 11, 10, 12)
    monkeypatch.setattr(benchmark, "_database_time", Mock(return_value=endpoint))
    clock = iter(
        [value for trial in range(9) for value in (trial * 10, trial * 10 + 2)]
    )
    monkeypatch.setattr(benchmark, "perf_counter", lambda: next(clock))
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        params = kwargs["vehicle_query_params"]
        rows = int((endpoint - params["window_start"]).total_seconds() / 3600)
        return pd.DataFrame({"id": range(rows)}), pd.DataFrame({"id": range(2 * rows)})

    monkeypatch.setattr(benchmark, "download_backtest_data", download)
    directory = tmp_path / "results"
    result = benchmark.run_download_benchmark(
        directory,
        connection_parameters=_connection_parameters(),
        hours=[168, 1, 24],
    )

    assert result["lookback_hours"].tolist()[:3] == [1, 24, 168]
    assert result["lookback_hours"].tolist()[3:6] != [1, 24, 168]
    assert len(calls) == 9
    assert result["elapsed_seconds"].tolist() == [2] * 9
    assert result["total_rows"].tolist() == (result["lookback_hours"] * 3).tolist()
    assert result["window_end"].eq(endpoint).all()
    for call in calls:
        assert call["connection_parameters"]["port"] == 3306
        params = call["vehicle_query_params"]
        assert params == call["trip_query_params"]
        assert params["window_end"] == endpoint
        assert params["route_id"] == "97-KEL"
        assert params["direction_id"] == 0
        for query in (call["vehicle_query"], call["trip_query"]):
            assert "SELECT *" in query
            assert "feed_timestamp >= %(window_start)s" in query
            assert "feed_timestamp < %(window_end)s" in query
            assert "trip_route_id = %(route_id)s" in query
            assert "trip_direction_id = %(direction_id)s" in query
            assert "ORDER BY id DESC" in query
    saved = pd.read_csv(directory / "timings.csv")
    assert len(saved) == 9
    assert "password" not in saved.columns
    assert "test-password" not in (directory / "timings.csv").read_text()
    summary = pd.read_csv(directory / "summary.csv")
    assert summary["trials"].tolist() == [3, 3, 3]
    html = (directory / "scaling.html").read_text()
    assert "plotly.js" in html
    assert "O(n) reference" in html
    assert "97-KEL" in html


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hours": []},
        {"hours": [0]},
        {"hours": [169]},
        {"hours": [1, 1]},
        {"hours": [True]},
        {"hours": [1.5]},
        {"repeats": 0},
        {"repeats": True},
        {"direction_id": 2},
        {"direction_id": True},
        {"route_id": " "},
    ],
)
def test_invalid_settings_fail_before_connecting(tmp_path, monkeypatch, kwargs) -> None:
    connect = Mock()
    monkeypatch.setattr(benchmark.mysql.connector, "connect", connect)
    with pytest.raises(ValueError):
        benchmark.run_download_benchmark(tmp_path / "results", **kwargs)
    connect.assert_not_called()
    assert not (tmp_path / "results").exists()


def test_existing_output_directory_is_not_overwritten(tmp_path, monkeypatch) -> None:
    clock = Mock()
    monkeypatch.setattr(benchmark, "_database_time", clock)
    with pytest.raises(FileExistsError):
        benchmark.run_download_benchmark(
            tmp_path, connection_parameters=_connection_parameters()
        )
    clock.assert_not_called()


def test_failed_download_preserves_completed_trials(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark, "_database_time", Mock(return_value=datetime(2026, 11, 10))
    )
    monkeypatch.setattr(
        benchmark,
        "download_backtest_data",
        Mock(
            side_effect=[
                (pd.DataFrame({"id": [1]}), pd.DataFrame()),
                RuntimeError("SQL failed"),
            ]
        ),
    )
    directory = tmp_path / "results"
    with pytest.raises(RuntimeError, match="SQL failed"):
        benchmark.run_download_benchmark(
            directory,
            connection_parameters=_connection_parameters(),
            hours=[1, 168],
            repeats=1,
        )
    saved = pd.read_csv(directory / "timings.csv")
    assert saved["lookback_hours"].tolist() == [1]
    assert not (directory / "scaling.html").exists()


@pytest.mark.parametrize("fails", [False, True])
def test_database_clock_closes_resources(monkeypatch, fails) -> None:
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    endpoint = datetime(2026, 11, 10)
    cursor.fetchone.return_value = (endpoint,)
    if fails:
        cursor.execute.side_effect = RuntimeError("Clock query failed")
    monkeypatch.setattr(
        benchmark.mysql.connector, "connect", Mock(return_value=connection)
    )
    if fails:
        with pytest.raises(RuntimeError, match="Clock query failed"):
            benchmark._database_time({})
    else:
        assert benchmark._database_time({}) == endpoint
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("power", [1, 2])
def test_summary_and_plot_distinguish_linear_and_quadratic_data(power) -> None:
    records = [
        {
            "lookback_hours": hours,
            "vehicle_rows": rows,
            "trip_rows": rows,
            "total_rows": 2 * rows,
            "elapsed_seconds": factor * rows**power,
        }
        for hours, rows in [(1, 10), (24, 20), (168, 40)]
        for factor in [0.9, 1, 1.1]
    ]
    summary = benchmark.summarize_download_timings(pd.DataFrame(records))
    expected_seconds = np.array([10, 20, 40]) ** power
    np.testing.assert_allclose(summary["median_seconds"], expected_seconds)
    np.testing.assert_allclose(
        summary["milliseconds_per_row"], 1000 * expected_seconds / [20, 40, 80]
    )
    figure = benchmark.make_download_scaling_plot(summary)
    np.testing.assert_allclose(figure.data[0].error_y.array, 0.1 * expected_seconds)
    np.testing.assert_allclose(
        figure.data[1].y, expected_seconds[0] * np.array([1, 2, 4])
    )
    assert any(
        f"p = {power:.2f}" in annotation.text
        for annotation in figure.layout.annotations
    )


def test_empty_windows_are_not_used_as_scaling_evidence() -> None:
    timings = pd.DataFrame(
        {
            "lookback_hours": [1, 24],
            "vehicle_rows": [0, 1],
            "trip_rows": [0, 1],
            "total_rows": [0, 2],
            "elapsed_seconds": [1, 2],
        }
    )
    summary = benchmark.summarize_download_timings(timings)
    assert pd.isna(summary.loc[0, "milliseconds_per_row"])
    figure = benchmark.make_download_scaling_plot(summary)
    assert list(figure.data[1].x) == [2]
    assert any(
        "At least three" in annotation.text for annotation in figure.layout.annotations
    )
    with pytest.raises(ValueError, match="No nonempty"):
        benchmark.make_download_scaling_plot(summary.iloc[:1])


def test_default_windows_cover_one_hour_through_one_week() -> None:
    assert min(benchmark.DEFAULT_HOURS) == 1
    assert max(benchmark.DEFAULT_HOURS) == 7 * 24


def test_main_loads_notebook_environment_file(tmp_path, monkeypatch) -> None:
    load = Mock(return_value=_connection_parameters())
    run = Mock()
    monkeypatch.setattr(benchmark, "_connection_parameters_from_env_file", load)
    monkeypatch.setattr(benchmark, "run_download_benchmark", run)
    benchmark.main(["--output-dir", str(tmp_path / "results"), "--repeats", "1"])
    load.assert_called_once_with(Path(".env.road_segment_congestion"))
    assert run.call_args.kwargs["hours"] == benchmark.DEFAULT_HOURS
    assert run.call_args.kwargs["repeats"] == 1
