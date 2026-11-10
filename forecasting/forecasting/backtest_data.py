"""Download or replay self-contained GTFS backtest datasets."""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import mysql.connector
import pandas as pd

from .backtest_cache import (
    _get_available_dataset_directory,
    list_backtest_datasets,
    load_backtest_dataset,
    save_backtest_dataset,
)
from .static_gtfs import download_static_gtfs

_CONNECTION_ENV_VARS = {
    "host": "TRANSIT_DB_HOST",
    "user": "TRANSIT_DB_USER",
    "password": "TRANSIT_DB_PASSWORD",
    "database": "TRANSIT_DB_NAME",
    "port": "TRANSIT_DB_PORT",
}
_REQUIRED_CONNECTION_KEYS = tuple(_CONNECTION_ENV_VARS)

_VEHICLE_UPDATES_QUERY = """
SELECT *
FROM TRANSIT_VEHICLE_TABLE
WHERE feed_timestamp >= NOW() - INTERVAL %(lookback_hours)s HOUR
ORDER BY id DESC
"""
_TRIP_UPDATES_QUERY = """
SELECT *
FROM TRANSIT_TRIP_TABLE
WHERE feed_timestamp >= NOW() - INTERVAL %(lookback_hours)s HOUR
ORDER BY id DESC
"""


@dataclass(frozen=True)
class BacktestDataset:
    """A selected snapshot ready for analysis.

    Parameters
    ----------
    directory : pathlib.Path
        Saved dataset directory.
    vehicle_updates, trip_updates : pandas.DataFrame
        Realtime updates in their original order and dtypes.
    """

    directory: Path
    vehicle_updates: pd.DataFrame
    trip_updates: pd.DataFrame

    @property
    def static_data_dir(self) -> Path:
        """Return the directory containing this snapshot's static GTFS."""
        return self.directory / "static_gtfs"


def _connection_parameters_from_env_file(
    env_file: str | Path | None,
) -> dict[str, str | None] | None:
    """Read optional database settings without changing the process environment.

    Parameters
    ----------
    env_file : str or pathlib.Path or None
        Optional shell-style settings file. If absent, the downloader uses
        the existing environment. File values override environment values.

    Returns
    -------
    dict[str, str or None] or None
        Connection settings, or None to use the downloader's environment lookup.

    Raises
    ------
    ValueError
        If an existing file contains an unknown variable or malformed value.
    """
    if env_file is None or not Path(env_file).is_file():
        return None
    path = Path(env_file)
    values = {
        variable: os.getenv(variable) for variable in _CONNECTION_ENV_VARS.values()
    }
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        variable, separator, encoded_value = line.partition("=")
        if not separator or variable not in values:
            raise ValueError(
                f"Invalid environment setting on line {line_number} of {path}."
            )
        parsed_value = shlex.split(encoded_value, posix=True)
        if len(parsed_value) != 1:
            raise ValueError(f"Invalid value on line {line_number} of {path}.")
        values[variable] = parsed_value[0]
    return {key: values[variable] for key, variable in _CONNECTION_ENV_VARS.items()}


def get_backtest_dataset(
    data_dir: str | Path,
    *,
    source: Literal["cache", "download"] = "cache",
    dataset_id: str | None = None,
    env_file: str | Path | None = None,
    vehicle_query: str | None = None,
    vehicle_query_params: Mapping[str, Any] | None = None,
    trip_query: str | None = None,
    trip_query_params: Mapping[str, Any] | None = None,
) -> BacktestDataset:
    """Load a saved backtest or explicitly download and cache a new snapshot.

    Parameters
    ----------
    data_dir : str or pathlib.Path
        Data root containing ``backtests/`` and ``static_gtfs_cache/``.
    source : {'cache', 'download'}, optional
        Defaults to offline replay. Download mode fetches both realtime tables
        and current static GTFS, then publishes a new self-contained snapshot.
    dataset_id : str or None, optional
        Saved dataset to select in cache mode, or unique name for a new
        snapshot in download mode. None selects the most recently saved
        dataset in cache mode, not necessarily the most recent data period;
        in download mode it generates a UTC timestamp ID. A supplied download
        name is validated and checked for uniqueness before reading credentials
        or making database/network calls. Use letters, digits, dots, dashes,
        or underscores, starting with a letter or digit.
    env_file : str or pathlib.Path or None, optional
        Optional database settings file, read only in download mode.
        If absent, use the existing ``TRANSIT_DB_*`` environment variables.
        Settings are never written to the snapshot or process environment.
    vehicle_query, trip_query : str or None, optional
        SQL overrides for the vehicle and trip downloads, respectively.
        Each defaults to the existing all-route query when None. Use named
        placeholders such as ``%(lookback_hours)s`` for bound values. Select
        the realtime columns required by the analysis, including
        ``feed_timestamp`` for saving the snapshot. Queries are ignored in
        cache mode; they do not filter an existing snapshot.
    vehicle_query_params, trip_query_params : collections.abc.Mapping or None, optional
        Independent named values for the corresponding SQL query. Mappings
        are copied, not modified. For a default query, None supplies
        ``{"lookback_hours": 48}``; an explicit mapping must include a positive
        integer ``lookback_hours``. Custom queries receive only their supplied
        values, with None meaning no parameters. Ignored in cache mode.
        Snapshot metadata records a shared positive integer ``lookback_hours``
        only when both mappings specify the same value; otherwise it is None.

    Returns
    -------
    BacktestDataset
        Selected directory, realtime tables, and matching static GTFS path.

    Raises
    ------
    FileNotFoundError
        If no cached datasets exist or the requested dataset is absent.
    FileExistsError
        If the requested download name is already occupied. The destination
        is checked again when saving; existing snapshots are never overwritten.
    ValueError
        If the source, download name, downloaded inputs, settings, or cached
        files are invalid.
    RuntimeError
        If database environment settings are missing in download mode.
    OSError
        If reading, downloading, or saving fails. Cache errors never trigger
        a substitute download.
    """
    data_root = Path(data_dir)
    cache_dir = data_root / "backtests"
    if source == "cache":
        catalog = list_backtest_datasets(cache_dir)
        if catalog.empty:
            raise FileNotFoundError(
                f"No saved backtests in {cache_dir}. Use source='download' first."
            )
        selected_id = (
            catalog.iloc[0]["dataset_id"] if dataset_id is None else dataset_id
        )
        if selected_id not in catalog["dataset_id"].tolist():
            raise FileNotFoundError(
                f"Unknown backtest dataset {selected_id!r}. "
                "Select an ID from list_backtest_datasets()."
            )
        directory = cache_dir / selected_id
        vehicle_updates, trip_updates = load_backtest_dataset(directory)
    elif source == "download":
        if dataset_id is not None:
            _get_available_dataset_directory(cache_dir, dataset_id)
        vehicle_query_params = _get_query_parameters(
            vehicle_query,
            vehicle_query_params,
            name="vehicle_query",
            default_lookback_hours=48,
        )
        trip_query_params = _get_query_parameters(
            trip_query,
            trip_query_params,
            name="trip_query",
            default_lookback_hours=48,
        )
        vehicle_updates, trip_updates = download_backtest_data(
            connection_parameters=_connection_parameters_from_env_file(env_file),
            vehicle_query=vehicle_query,
            vehicle_query_params=vehicle_query_params,
            trip_query=trip_query,
            trip_query_params=trip_query_params,
        )
        lookbacks = [
            parameters.get("lookback_hours") if parameters is not None else None
            for parameters in (vehicle_query_params, trip_query_params)
        ]
        lookback_hours = None
        if (
            all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in lookbacks
            )
            and lookbacks[0] == lookbacks[1]
        ):
            lookback_hours = lookbacks[0]
        static_data_dir = download_static_gtfs(data_root / "static_gtfs_cache")
        directory = save_backtest_dataset(
            cache_dir,
            vehicle_updates,
            trip_updates,
            static_data_dir,
            dataset_id=dataset_id,
            lookback_hours=lookback_hours,
            source="database",
        )
    else:
        raise ValueError("Choose source: 'cache' or 'download'.")
    return BacktestDataset(directory, vehicle_updates, trip_updates)


def _get_connection_parameters(
    connection_parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve and validate database connection parameters.

    Parameters
    ----------
    connection_parameters : collections.abc.Mapping or None
        Optional MySQL connection parameters. If omitted, values are read from
        the ``TRANSIT_DB_*`` environment variables.

    Returns
    -------
    dict[str, Any]
        Validated parameters accepted by ``mysql.connector.connect``.

    Raises
    ------
    RuntimeError
        If required environment variables are not set.
    ValueError
        If required parameters are missing or the port is not an integer.
    """
    if connection_parameters is None:
        missing = [
            env_var
            for env_var in _CONNECTION_ENV_VARS.values()
            if os.getenv(env_var) is None
        ]
        if missing:
            raise RuntimeError(
                "Missing database environment variables: " + ", ".join(missing)
            )
        parameters = {
            key: os.environ[env_var] for key, env_var in _CONNECTION_ENV_VARS.items()
        }
    else:
        parameters = dict(connection_parameters)

    missing_keys = [
        key
        for key in _REQUIRED_CONNECTION_KEYS
        if key not in parameters or parameters[key] is None
    ]
    if missing_keys:
        raise ValueError(
            "Missing database connection parameters: " + ", ".join(missing_keys)
        )

    try:
        parameters["port"] = int(parameters["port"])
    except (TypeError, ValueError) as exc:
        raise ValueError("The database port must be an integer.") from exc

    return parameters


def _get_query_parameters(
    query: str | None,
    query_params: Mapping[str, Any] | None,
    *,
    name: str,
    default_lookback_hours: int,
) -> dict[str, Any] | None:
    """Validate SQL and copy its named parameters, resolving default values.

    Parameters
    ----------
    query : str or None
        Custom SQL, or None to use the corresponding default query.
    query_params : collections.abc.Mapping or None
        Named values to bind, or None for query-specific defaults.
    name : str
        Query argument name used in validation errors.
    default_lookback_hours : int
        Window used only when both query and query_params are None.

    Returns
    -------
    dict[str, Any] or None
        Independent parameters, or None for parameterless custom SQL.

    Raises
    ------
    ValueError
        If the query, mapping keys, or default-query lookback are invalid.
    """
    if query is not None and (not isinstance(query, str) or not query.strip()):
        raise ValueError(f"{name} must be a nonempty SQL string or None.")
    if query_params is None:
        return {"lookback_hours": default_lookback_hours} if query is None else None
    if not isinstance(query_params, Mapping) or any(
        not isinstance(key, str) for key in query_params
    ):
        raise ValueError(f"{name}_params must be a mapping with string keys or None.")
    parameters = dict(query_params)
    if query is None:
        lookback_hours = parameters.get("lookback_hours")
        if (
            isinstance(lookback_hours, bool)
            or not isinstance(lookback_hours, int)
            or lookback_hours <= 0
        ):
            raise ValueError(
                f"{name}_params must contain a positive integer lookback_hours "
                "for the default query."
            )
    return parameters


def download_backtest_data(
    connection_parameters: Mapping[str, Any] | None = None,
    *,
    vehicle_query: str | None = None,
    vehicle_query_params: Mapping[str, Any] | None = None,
    trip_query: str | None = None,
    trip_query_params: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Download recent vehicle and trip updates from the transit database.

    Default query windows are measured relative to the database server's
    current time. Connection parameters are read from ``TRANSIT_DB_HOST``,
    ``TRANSIT_DB_USER``, ``TRANSIT_DB_PASSWORD``, ``TRANSIT_DB_NAME``, and
    ``TRANSIT_DB_PORT`` unless supplied explicitly.

    To retain a download, use ``save_backtest_dataset`` with its matching
    static GTFS, then ``load_backtest_dataset`` for offline historical replay.
    This function itself always queries the database.

    Parameters
    ----------
    connection_parameters : collections.abc.Mapping or None, optional
        MySQL connection parameters accepted by ``mysql.connector.connect``.
        When omitted, the ``TRANSIT_DB_*`` environment variables are used.
    vehicle_query, trip_query : str or None, optional
        SQL overrides for each table. None uses the existing query selecting
        all columns and routes within the lookback window, newest ID first.
        Include the desired time window, filters, columns, and ordering in
        custom SQL, using named placeholders such as ``%(route_id)s``.
    vehicle_query_params, trip_query_params : collections.abc.Mapping or None, optional
        Independent named values bound by the database driver to each query.
        Mappings are copied, not modified. For a default query, None supplies
        ``{"lookback_hours": 3}``; an explicit mapping must include a positive
        integer ``lookback_hours``. For custom SQL, any named parameters are
        supported and None means no parameters. Values are never interpolated
        into the SQL by this function.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        Vehicle updates followed by trip updates, in each query's result
        order. Default queries order newest first by the database row ID.

    Raises
    ------
    ValueError
        If connection parameters, SQL strings, parameter mappings, or a
        default query's ``lookback_hours`` value are invalid.
    RuntimeError
        If the required database environment variables are not set.
    mysql.connector.Error
        If connecting to or querying the database fails.
    """
    vehicle_query_params = _get_query_parameters(
        vehicle_query,
        vehicle_query_params,
        name="vehicle_query",
        default_lookback_hours=3,
    )
    trip_query_params = _get_query_parameters(
        trip_query,
        trip_query_params,
        name="trip_query",
        default_lookback_hours=3,
    )
    parameters = _get_connection_parameters(connection_parameters)
    connection = mysql.connector.connect(**parameters)
    try:
        df_vehicle_updates = pd.read_sql_query(
            _VEHICLE_UPDATES_QUERY if vehicle_query is None else vehicle_query,
            connection,
            params=vehicle_query_params,
        )
        df_trip_updates = pd.read_sql_query(
            _TRIP_UPDATES_QUERY if trip_query is None else trip_query,
            connection,
            params=trip_query_params,
        )
    finally:
        connection.close()

    return df_vehicle_updates, df_trip_updates
