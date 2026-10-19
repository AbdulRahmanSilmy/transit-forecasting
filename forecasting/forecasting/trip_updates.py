"""Utilities for associating GTFS trip updates with vehicle updates."""

import numpy as np
import pandas as pd


def get_trip_updates_for_trip(
    df_vu_trip: pd.DataFrame,
    df_trip_updates: pd.DataFrame,
    offset: pd.Timedelta | None = None,
) -> pd.DataFrame:
    """Assign the latest eligible trip update to each vehicle update.

    For each vehicle-update row, this function selects a trip-update row with
    matching ``trip_id`` and ``stop_id`` whose ``feed_timestamp`` is no later
    than the vehicle-update timestamp (or that timestamp minus ``offset``).
    Among eligible rows, it uses the first row in ``df_trip_updates``; callers
    should therefore provide trip updates in newest-first order.

    The input vehicle-update frame is copied. The returned frame includes
    ``arrival_delay``, ``departure_delay``, ``arrival_time``, and
    ``departure_time`` from the selected trip update, or NaN values when no
    eligible update exists. The result is sorted by ``feed_timestamp``
    ascending.

    Parameters
    ----------
    df_vu_trip : pandas.DataFrame
        Vehicle updates for a trip. Must include ``trip_id``, ``stop_id``,
        and ``feed_timestamp``.
    df_trip_updates : pandas.DataFrame
        Trip updates. Must include ``trip_id``, ``stop_id``, ``feed_timestamp``,
        ``arrival_delay``, ``departure_delay``, ``arrival_time``, and
        ``departure_time``. Rows should be ordered newest first.
    offset : pandas.Timedelta or None, optional
        Amount subtracted from each vehicle-update timestamp before selecting
        eligible trip updates. Defaults to ``None``.

    Returns
    -------
    pandas.DataFrame
        A copy of the vehicle updates with the four trip-update fields added
        and rows sorted by ``feed_timestamp`` ascending.
    """
    result = df_vu_trip.copy()
    update_fields = (
        "arrival_delay",
        "departure_delay",
        "arrival_time",
        "departure_time",
    )
    for field in update_fields:
        result[field] = None

    for i in range(len(result)):
        row = result.iloc[i]
        if offset is not None:
            eligible = df_trip_updates[
                df_trip_updates["feed_timestamp"] <= row["feed_timestamp"] - offset
            ]
        else:
            eligible = df_trip_updates[
                df_trip_updates["feed_timestamp"] <= row["feed_timestamp"]
            ]

        eligible = eligible[
            (eligible["trip_id"] == row["trip_id"])
            & (eligible["stop_id"] == row["stop_id"])
        ]
        if not eligible.empty:
            latest = eligible.iloc[0]
            for field in update_fields:
                result.at[result.index[i], field] = latest[field]
        else:
            for field in update_fields:
                result.at[result.index[i], field] = np.nan

    return result.sort_values("feed_timestamp", ascending=True)
