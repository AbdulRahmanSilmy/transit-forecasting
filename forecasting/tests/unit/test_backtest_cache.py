import ast
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    backtest_data,
    get_backtest_dataset,
    list_backtest_datasets,
    load_backtest_dataset,
    save_backtest_dataset,
)


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "original_static"
    directory.mkdir()
    for name in (
        "stops.txt",
        "stop_times.txt",
        "trips.txt",
        "shapes.txt",
        "feed_info.txt",
    ):
        (directory / name).write_text(f"{name}\noriginal\n")
    return directory


def _updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [2, 1],
            "feed_timestamp": pd.to_datetime(
                ["2026-09-09 01:00:00", "2026-09-08 23:00:00"]
            ),
            "trip_id": pd.Series(["trip-2", pd.NA], dtype="string"),
            "arrival_delay": pd.Series([pd.NA, -30], dtype="Int64"),
        }
    ).set_index("id")


def test_round_trip_preserves_frames_and_matching_static_files(
    tmp_path: Path, static_dir: Path
) -> None:
    vehicle = _updates()
    trip = vehicle.iloc[::-1].copy()
    original_vehicle = vehicle.copy(deep=True)
    original_trip = trip.copy(deep=True)
    dataset = save_backtest_dataset(
        tmp_path / "backtests",
        vehicle,
        trip,
        static_dir,
        dataset_id="september-2026",
        lookback_hours=48,
        source="database",
    )
    (static_dir / "stops.txt").write_text("changed after saving\n")

    with patch(
        "mysql.connector.connect", side_effect=AssertionError("No database")
    ), patch("urllib.request.urlopen", side_effect=AssertionError("No network")):
        loaded_vehicle, loaded_trip = load_backtest_dataset(dataset)

    pdt.assert_frame_equal(loaded_vehicle, original_vehicle)
    pdt.assert_frame_equal(loaded_trip, original_trip)
    pdt.assert_frame_equal(vehicle, original_vehicle)
    pdt.assert_frame_equal(trip, original_trip)
    assert (dataset / "static_gtfs/stops.txt").read_text() == "stops.txt\noriginal\n"
    metadata = json.loads((dataset / "metadata.json").read_text())
    assert metadata["vehicle_start_utc"] == "2026-09-08T23:00:00+00:00"
    assert metadata["vehicle_end_utc"] == "2026-09-09T01:00:00+00:00"
    assert metadata["vehicle_rows"] == metadata["trip_rows"] == 2
    assert metadata["lookback_hours"] == 48
    assert metadata["source"] == "database"
    assert "password" not in (dataset / "metadata.json").read_text().lower()


def test_catalog_is_newest_first_and_can_load_any_historical_dataset(
    tmp_path: Path, static_dir: Path
) -> None:
    root = tmp_path / "backtests"
    older = save_backtest_dataset(
        root, _updates(), _updates(), static_dir, dataset_id="z"
    )
    newer = save_backtest_dataset(
        root, _updates(), _updates(), static_dir, dataset_id="a"
    )
    metadata_path = older / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["created_at"] = "2020-01-01T00:00:00+00:00"
    metadata_path.write_text(json.dumps(metadata))
    (root / ".backtest-unpublished").mkdir()

    with patch(
        "pandas.read_pickle", side_effect=AssertionError("Catalog reads metadata")
    ):
        catalog = list_backtest_datasets(root)

    assert catalog["dataset_id"].tolist() == [newer.name, older.name]
    assert str(catalog["created_at"].dt.tz) == "UTC"
    assert "files" not in catalog.columns
    pdt.assert_frame_equal(load_backtest_dataset(older)[0], _updates())


def test_absent_cache_returns_empty_catalog_without_creating_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backtests"

    assert list_backtest_datasets(root).empty
    assert not root.exists()


def test_auto_named_snapshots_are_distinct_and_never_overwrite(
    tmp_path: Path, static_dir: Path
) -> None:
    first = save_backtest_dataset(tmp_path, _updates(), _updates(), static_dir)
    second = save_backtest_dataset(tmp_path, _updates(), _updates(), static_dir)

    assert first != second
    with pytest.raises(FileExistsError, match="already exists"):
        save_backtest_dataset(
            tmp_path, _updates(), _updates(), static_dir, dataset_id=first.name
        )
    pdt.assert_frame_equal(load_backtest_dataset(first)[0], _updates())


@pytest.mark.parametrize("dataset_id", ["", ".", "..", "../outside", "/outside", "a/b"])
def test_rejects_invalid_dataset_names(
    tmp_path: Path, static_dir: Path, dataset_id: str
) -> None:
    with pytest.raises(ValueError, match="dataset_id"):
        save_backtest_dataset(
            tmp_path, _updates(), _updates(), static_dir, dataset_id=dataset_id
        )


def test_failed_write_is_not_published(
    tmp_path: Path, static_dir: Path, monkeypatch
) -> None:
    root = tmp_path / "backtests"

    def fail_write(*args, **kwargs):
        raise OSError("Disk full")

    monkeypatch.setattr(pd.DataFrame, "to_pickle", fail_write)
    with pytest.raises(OSError, match="Disk full"):
        save_backtest_dataset(root, _updates(), _updates(), static_dir)

    assert list(root.iterdir()) == []
    assert list_backtest_datasets(root).empty


@pytest.mark.parametrize("filename", ["trip_updates.pkl", "static_gtfs/stops.txt"])
def test_rejects_corruption_before_unpickling(
    tmp_path: Path, static_dir: Path, filename: str
) -> None:
    dataset = save_backtest_dataset(tmp_path, _updates(), _updates(), static_dir)
    (dataset / filename).write_bytes(b"corrupt")

    with patch("pandas.read_pickle", side_effect=AssertionError("Must validate first")):
        with pytest.raises(ValueError, match="checksum mismatch"):
            load_backtest_dataset(dataset)


def test_incomplete_snapshot_is_not_silently_skipped_or_downloaded(
    tmp_path: Path, static_dir: Path
) -> None:
    root = tmp_path / "backtests"
    dataset = save_backtest_dataset(root, _updates(), _updates(), static_dir)
    (dataset / "trip_updates.pkl").unlink()

    with pytest.raises(FileNotFoundError, match="Backtest file is missing"):
        load_backtest_dataset(dataset)
    with pytest.raises(FileNotFoundError, match="Backtest file is missing"):
        list_backtest_datasets(root)


@pytest.mark.parametrize("schema_version", [None, 2])
def test_rejects_unsupported_metadata(
    tmp_path: Path, static_dir: Path, schema_version: int | None
) -> None:
    dataset = save_backtest_dataset(tmp_path, _updates(), _updates(), static_dir)
    metadata_path = dataset / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["schema_version"] = schema_version
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="Unsupported"):
        load_backtest_dataset(dataset)


def test_rejects_missing_static_tables(tmp_path: Path, static_dir: Path) -> None:
    (static_dir / "trips.txt").unlink()

    with pytest.raises(ValueError, match="missing required tables: trips.txt"):
        save_backtest_dataset(tmp_path, _updates(), _updates(), static_dir)


def test_preserves_empty_tables_and_records_no_data_bounds(
    tmp_path: Path, static_dir: Path
) -> None:
    empty = _updates().iloc[:0]
    dataset = save_backtest_dataset(tmp_path, empty, empty, static_dir)

    pdt.assert_frame_equal(load_backtest_dataset(dataset)[0], empty)
    metadata = json.loads((dataset / "metadata.json").read_text())
    assert metadata["vehicle_start_utc"] is None
    assert metadata["trip_end_utc"] is None


@pytest.mark.parametrize("lookback_hours", [0, -1, True, 1.5])
def test_rejects_invalid_lookback_hours(
    tmp_path: Path, static_dir: Path, lookback_hours
) -> None:
    with pytest.raises(ValueError, match="lookback_hours"):
        save_backtest_dataset(
            tmp_path, _updates(), _updates(), static_dir, lookback_hours=lookback_hours
        )


@pytest.mark.parametrize("timestamps", [[None, None], ["bad", "bad"]])
def test_rejects_missing_or_invalid_feed_timestamps(
    tmp_path: Path, static_dir: Path, timestamps
) -> None:
    updates = _updates().assign(feed_timestamp=timestamps)

    with pytest.raises(ValueError):
        save_backtest_dataset(tmp_path, updates, updates, static_dir)


def _notebook_cell(cell_id: str) -> str:
    notebook_path = (
        Path(__file__).resolve().parents[3] / "notebooks/road_segment_congestion.ipynb"
    )
    notebook = json.loads(notebook_path.read_text())
    return "".join(
        next(cell["source"] for cell in notebook["cells"] if cell["id"] == cell_id)
    )


def _notebook_namespace(root: Path) -> dict:
    return {
        "Path": Path,
        "pd": pd,
        "repo_root": root,
        "backtest_cache_dir": root / "data/backtests",
        "display": Mock(),
        "get_backtest_dataset": get_backtest_dataset,
        "list_backtest_datasets": list_backtest_datasets,
    }


def test_notebook_replays_named_historical_snapshot_without_credentials(
    tmp_path: Path, static_dir: Path, monkeypatch
) -> None:
    root = tmp_path / "data/backtests"
    historical = save_backtest_dataset(
        root, _updates(), _updates(), static_dir, dataset_id="historical"
    )
    save_backtest_dataset(root, _updates().iloc[:1], _updates(), static_dir)
    (tmp_path / ".env.road_segment_congestion").write_text("invalid credentials file\n")
    (tmp_path / "forecasting/forecasting").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    namespace = _notebook_namespace(tmp_path)
    download = Mock(side_effect=AssertionError("No database"))
    static_download = Mock(side_effect=AssertionError("No network"))
    monkeypatch.setattr(backtest_data, "download_backtest_data", download)
    monkeypatch.setattr(backtest_data, "download_static_gtfs", static_download)

    exec(_notebook_cell("setup"), namespace)
    tree = ast.parse(_notebook_cell("download"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "backtest_dataset_id"
            for target in statement.targets
        ):
            statement.value = ast.Constant("historical")
    assert not any(
        isinstance(
            node, (ast.If, ast.For, ast.While, ast.Try, ast.FunctionDef, ast.ClassDef)
        )
        for node in ast.walk(tree)
    )
    exec(compile(ast.fix_missing_locations(tree), "<download cell>", "exec"), namespace)
    exec(_notebook_cell("static-data"), namespace)

    assert namespace["backtest_dataset_dir"] == historical
    assert namespace["static_data_dir"] == historical / "static_gtfs"
    pdt.assert_frame_equal(namespace["df_vehicle_updates"], _updates())
    download.assert_not_called()
    static_download.assert_not_called()


def test_download_saves_once_then_reuses_both_feeds_offline(
    tmp_path: Path, static_dir: Path, monkeypatch
) -> None:
    download = Mock(return_value=(_updates(), _updates()))
    static_download = Mock(return_value=static_dir)
    monkeypatch.setattr(backtest_data, "download_backtest_data", download)
    monkeypatch.setattr(backtest_data, "download_static_gtfs", static_download)

    saved = get_backtest_dataset(tmp_path / "data", source="download")
    download.assert_called_once_with(lookback_hours=48, connection_parameters=None)
    static_download.assert_called_once_with(tmp_path / "data/static_gtfs_cache")
    download.side_effect = AssertionError("No second download")
    static_download.side_effect = AssertionError("No second GTFS download")
    loaded = get_backtest_dataset(tmp_path / "data")

    assert loaded.directory == saved.directory
    assert loaded.static_data_dir == saved.static_data_dir
    assert len(list_backtest_datasets(tmp_path / "data/backtests")) == 1
    pdt.assert_frame_equal(loaded.trip_updates, _updates())
