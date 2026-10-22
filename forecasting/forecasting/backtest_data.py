"""Download recent GTFS-Realtime updates for transit backtesting."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import mysql.connector
import pandas as pd

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
WHERE feed_timestamp >= NOW() - INTERVAL %s HOUR
ORDER BY id DESC
"""
_TRIP_UPDATES_QUERY = """
SELECT *
FROM TRANSIT_TRIP_TABLE
WHERE feed_timestamp >= NOW() - INTERVAL %s HOUR
ORDER BY id DESC
"""


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


def download_backtest_data(
    lookback_hours: int = 3,
    connection_parameters: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Download recent vehicle and trip updates from the transit database.

    The query window is measured relative to the database server's current
    time. Connection parameters are read from ``TRANSIT_DB_HOST``,
    ``TRANSIT_DB_USER``, ``TRANSIT_DB_PASSWORD``, ``TRANSIT_DB_NAME``, and
    ``TRANSIT_DB_PORT`` unless supplied explicitly.

    Parameters
    ----------
    lookback_hours : int, optional
        Number of hours of recent updates to download. Defaults to ``3``.
    connection_parameters : collections.abc.Mapping or None, optional
        MySQL connection parameters accepted by ``mysql.connector.connect``.
        When omitted, the ``TRANSIT_DB_*`` environment variables are used.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        Vehicle updates followed by trip updates, each ordered newest first
        by the database row ID.

    Raises
    ------
    ValueError
        If ``lookback_hours`` is not a positive integer or connection
        parameters are invalid.
    RuntimeError
        If the required database environment variables are not set.
    mysql.connector.Error
        If connecting to or querying the database fails.
    """
    if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int):
        raise ValueError("lookback_hours must be a positive integer.")
    if lookback_hours <= 0:
        raise ValueError("lookback_hours must be a positive integer.")

    parameters = _get_connection_parameters(connection_parameters)
    connection = mysql.connector.connect(**parameters)
    try:
        df_vehicle_updates = pd.read_sql_query(
            _VEHICLE_UPDATES_QUERY,
            connection,
            params=(lookback_hours,),
        )
        df_trip_updates = pd.read_sql_query(
            _TRIP_UPDATES_QUERY,
            connection,
            params=(lookback_hours,),
        )
    finally:
        connection.close()

    return df_vehicle_updates, df_trip_updates
