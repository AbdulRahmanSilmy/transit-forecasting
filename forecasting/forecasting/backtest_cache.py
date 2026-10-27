"""Persist self-contained, explicitly selected historical backtest datasets."""

import json
import re
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

from .static_gtfs import _OPTIONAL_FILES, _REQUIRED_FILES

_TABLE_FILES = {"vehicle": "vehicle_updates.pkl", "trip": "trip_updates.pkl"}
_STATIC_FILES = (*_REQUIRED_FILES, *_OPTIONAL_FILES, "gtfs.zip")
_REQUIRED_SNAPSHOT_FILES = {
    *_TABLE_FILES.values(),
    *(f"static_gtfs/{name}" for name in _REQUIRED_FILES),
}
_ALLOWED_SNAPSHOT_FILES = {
    *_TABLE_FILES.values(),
    *(f"static_gtfs/{name}" for name in _STATIC_FILES),
}
_CATALOG_COLUMNS = [
    "dataset_id",
    "created_at",
    "source",
    "lookback_hours",
    "vehicle_rows",
    "trip_rows",
    "vehicle_start_utc",
    "vehicle_end_utc",
    "trip_start_utc",
    "trip_end_utc",
]


def _file_digest(path: Path) -> str:
    """Calculate a file's SHA-256 digest with bounded memory.

    Parameters
    ----------
    path : pathlib.Path
        File to read.

    Returns
    -------
    str
        Hexadecimal digest.
    """
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_metadata(dataset_dir: Path) -> dict[str, Any]:
    """Read and validate the manifest and presence of its recorded files.

    Parameters
    ----------
    dataset_dir : pathlib.Path
        Published dataset directory.

    Returns
    -------
    dict[str, Any]
        Validated manifest. File contents are checked only when loading.

    Raises
    ------
    ValueError
        If the manifest is malformed or uses an unsupported schema.
    FileNotFoundError
        If the manifest or a recorded file is absent.
    """
    manifest = dataset_dir / "metadata.json"
    metadata = json.loads(manifest.read_text())
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError(f"Unsupported backtest metadata schema: {manifest}")
    missing = [key for key in _CATALOG_COLUMNS if key not in metadata]
    if missing or metadata["dataset_id"] != dataset_dir.name:
        raise ValueError(f"Incomplete or mismatched backtest metadata: {manifest}")
    created_at = pd.to_datetime(metadata["created_at"], utc=True, errors="raise")
    if not isinstance(created_at, pd.Timestamp) or pd.isna(created_at):
        raise ValueError(f"Invalid backtest creation timestamp: {manifest}")
    metadata["created_at"] = created_at
    files = metadata.get("files")
    if (
        not isinstance(files, dict)
        or not _REQUIRED_SNAPSHOT_FILES.issubset(files)
        or not set(files).issubset(_ALLOWED_SNAPSHOT_FILES)
    ):
        raise ValueError(f"Invalid backtest file inventory: {manifest}")
    for relative_path, checksum in files.items():
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError(f"Invalid backtest file checksum: {manifest}")
        if not (dataset_dir / relative_path).is_file():
            raise FileNotFoundError(
                f"Backtest file is missing: {dataset_dir / relative_path}"
            )
    return metadata


def save_backtest_dataset(
    cache_dir: str | Path,
    df_vehicle_updates: pd.DataFrame,
    df_trip_updates: pd.DataFrame,
    static_data_dir: str | Path,
    *,
    dataset_id: str | None = None,
    lookback_hours: int | None = None,
    source: str = "manual",
) -> Path:
    """Save an immutable realtime/static backtest snapshot atomically.

    Parameters
    ----------
    cache_dir : str or pathlib.Path
        Parent directory, conventionally ``data/backtests``.
    df_vehicle_updates, df_trip_updates : pandas.DataFrame
        Downloaded or imported updates with ``feed_timestamp`` columns.
        Row order, index, columns, and dtypes are preserved without mutation.
    static_data_dir : str or pathlib.Path
        Static GTFS matching the realtime period. Required and available
        optional GTFS files are copied, not moved or downloaded.
    dataset_id : str or None, optional
        Unique directory name using letters, digits, dots, dashes, or
        underscores. Defaults to a UTC creation timestamp with microseconds.
    lookback_hours : int or None, optional
        Requested download window; leave unset for historical imports.
    source : str, optional
        Provenance label, for example ``"database"`` or ``"manual"``.

    Returns
    -------
    pathlib.Path
        Published directory containing both pickle files, ``static_gtfs/``,
        and ``metadata.json`` with UTC data bounds, row counts, and checksums.

    Raises
    ------
    FileExistsError
        If the dataset name already exists; snapshots are never overwritten.
    ValueError
        If the ID, lookback, timestamps, or static GTFS inputs are invalid.
    OSError
        If reading, copying, or publishing the snapshot fails. Incomplete
        writes are not published as datasets.
    """
    created_at = pd.Timestamp.now(tz="UTC")
    if dataset_id is None:
        dataset_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    if not isinstance(dataset_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", dataset_id
    ):
        raise ValueError("dataset_id must be a single nonempty directory name.")
    if lookback_hours is not None and (
        isinstance(lookback_hours, bool)
        or not isinstance(lookback_hours, int)
        or lookback_hours <= 0
    ):
        raise ValueError("lookback_hours must be a positive integer or None.")
    destination = Path(cache_dir) / dataset_id
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Backtest dataset already exists: {destination}")
    static_dir = Path(static_data_dir)
    missing = [name for name in _REQUIRED_FILES if not (static_dir / name).is_file()]
    if missing:
        raise ValueError(
            "Static GTFS is missing required tables: " + ", ".join(missing)
        )
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "created_at": created_at.isoformat(),
        "source": source,
        "lookback_hours": lookback_hours,
    }
    frames = {"vehicle": df_vehicle_updates, "trip": df_trip_updates}
    for name, frame in frames.items():
        if "feed_timestamp" not in frame.columns:
            raise ValueError(f"{name} updates are missing feed_timestamp.")
        timestamps = pd.to_datetime(frame["feed_timestamp"], utc=True, errors="raise")
        if timestamps.isna().any():
            raise ValueError(f"{name} updates contain missing feed timestamps.")
        metadata[f"{name}_rows"] = len(frame)
        metadata[f"{name}_start_utc"] = (
            timestamps.min().isoformat() if len(frame) else None
        )
        metadata[f"{name}_end_utc"] = (
            timestamps.max().isoformat() if len(frame) else None
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".backtest-", dir=destination.parent) as staging:
        staging_dir = Path(staging)
        for name, frame in frames.items():
            frame.to_pickle(staging_dir / _TABLE_FILES[name])
        snapshot_static_dir = staging_dir / "static_gtfs"
        snapshot_static_dir.mkdir()
        for name in _STATIC_FILES:
            if (static_dir / name).is_file():
                copy2(static_dir / name, snapshot_static_dir / name)
        files = [
            *_TABLE_FILES.values(),
            *(
                f"static_gtfs/{path.name}"
                for path in sorted(snapshot_static_dir.iterdir())
            ),
        ]
        metadata["files"] = {name: _file_digest(staging_dir / name) for name in files}
        (staging_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n"
        )
        staging_dir.rename(destination)
    return destination


def load_backtest_dataset(
    dataset_dir: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load a trusted local snapshot without database or network access.

    All recorded realtime and static files are checksum-validated before
    unpickling. Checksums detect accidental corruption, not malicious input;
    only load pickle snapshots created by you or another trusted source.
    No missing or corrupt data is silently replaced by a fresh download.

    Parameters
    ----------
    dataset_dir : str or pathlib.Path
        A directory returned by ``save_backtest_dataset``.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        Vehicle and trip updates in their original order and dtypes.
        Matching static files reside in ``dataset_dir / "static_gtfs"``.

    Raises
    ------
    FileNotFoundError
        If the dataset or a recorded file is absent.
    ValueError
        If metadata, checksums, or saved DataFrame row counts are invalid.
    """
    snapshot = Path(dataset_dir)
    metadata = _read_metadata(snapshot)
    for name, expected in metadata["files"].items():
        if _file_digest(snapshot / name) != expected:
            raise ValueError(f"Backtest file checksum mismatch: {snapshot / name}")
    frames = {}
    for name, filename in _TABLE_FILES.items():
        frame = pd.read_pickle(snapshot / filename)
        if (
            not isinstance(frame, pd.DataFrame)
            or len(frame) != metadata[f"{name}_rows"]
        ):
            raise ValueError(f"Invalid saved {name} updates: {snapshot / filename}")
        frames[name] = frame
    return frames["vehicle"], frames["trip"]


def list_backtest_datasets(cache_dir: str | Path) -> pd.DataFrame:
    """List saved datasets newest-first without reading large pickle files.

    Parameters
    ----------
    cache_dir : str or pathlib.Path
        Parent directory containing published backtest snapshots.

    Returns
    -------
    pandas.DataFrame
        Dataset IDs, UTC creation/data timestamps, source, requested lookback,
        and row counts. An absent or empty cache returns an empty catalog.
        Hidden staging directories are omitted.

    Raises
    ------
    ValueError
        If a published dataset has invalid metadata.
    FileNotFoundError
        If a published dataset is incomplete; it is not silently skipped.
    """
    cache_root = Path(cache_dir)
    records = []
    if cache_root.exists():
        for path in sorted(cache_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                metadata = _read_metadata(path)
                records.append({key: metadata[key] for key in _CATALOG_COLUMNS})
    catalog = pd.DataFrame(records, columns=_CATALOG_COLUMNS)
    if not catalog.empty:
        catalog = catalog.sort_values(
            ["created_at", "dataset_id"], ascending=False, kind="stable"
        ).reset_index(drop=True)
    return catalog
