"""Utilities for selecting and associating GTFS trip updates."""

import warnings

import numpy as np
import pandas as pd


def get_latest_stop_delays(
    df_trip_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    *,
    route_id: str,
    stop_name: str,
    agency_timezone: str = "America/Vancouver",
) -> pd.DataFrame:
    """Select the latest reported arrival delay for each trip's stop visit.

    Match all stop IDs with the requested name on the requested route, then
    select the row with the greatest ``feed_timestamp`` per trip instance
    and stop. If ``stop_sequence`` is available, repeated visits to a stop
    within a trip are kept separate. Database ``id`` breaks timestamp ties
    when available; otherwise the first input row wins.

    Trip instances are identified by ``trip_id`` and the agency-local
    ``trip_start_timestamp`` descriptor. Missing start descriptors trigger
    a warning and are grouped by the publication date in ``agency_timezone``.
    This fallback is approximate, particularly for trips spanning midnight.

    Selection happens before considering missing delays: a missing delay in
    the latest row is retained, not replaced by an older prediction. The
    final reported estimate is a delay proxy, not an independently observed
    arrival, and may still be provisional at the end of a recording window.
    Neither input frame is modified.

    Parameters
    ----------
    df_trip_updates : pandas.DataFrame
        Updates containing ``trip_route_id``, ``trip_id``, ``stop_id``,
        ``trip_start_timestamp``, ``feed_timestamp``, and ``arrival_delay``.
        Delays are in seconds. Naive feed timestamps are interpreted as UTC.
        Start descriptors are used as identifiers, not UTC observations.
    df_stops : pandas.DataFrame
        GTFS stops containing ``stop_id`` and ``stop_name``.
    route_id : str
        GTFS route identifier, such as ``"97-KEL"``.
    stop_name : str
        Exact stop name. All matching platforms are included.
    agency_timezone : str, optional
        Timezone used for publication-date grouping when start descriptors
        are absent. Defaults to ``"America/Vancouver"``.

    Returns
    -------
    pandas.DataFrame
        Latest rows sorted by UTC ``feed_timestamp`` ascending, with
        normalized string trip/stop IDs, ``trip_instance_key``, ``stop_name``,
        and ``delay_minutes`` added. Missing delays remain missing.

    Raises
    ------
    ValueError
        If required columns are missing, no matching stops or route updates
        exist, or matching rows contain invalid timestamps, trip IDs, or
        delays.

    Warns
    -----
    UserWarning
        If missing start descriptors require approximate daily grouping.
    """
    required_updates = (
        "trip_route_id",
        "trip_id",
        "stop_id",
        "trip_start_timestamp",
        "feed_timestamp",
        "arrival_delay",
    )
    for name, frame, required in (
        ("df_trip_updates", df_trip_updates, required_updates),
        ("df_stops", df_stops, ("stop_id", "stop_name")),
    ):
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(
                f"{name} is missing required columns: {', '.join(missing)}"
            )

    stop_ids = (
        df_stops.loc[df_stops["stop_name"].eq(stop_name), "stop_id"]
        .dropna()
        .astype("string")
    )
    if stop_ids.empty:
        raise ValueError(f"No GTFS stops were found with name {stop_name!r}.")

    updates = df_trip_updates.loc[
        df_trip_updates["trip_route_id"].astype("string").eq(str(route_id))
        & df_trip_updates["stop_id"].astype("string").isin(stop_ids)
    ].copy()
    if updates.empty:
        raise ValueError(
            f"No trip updates were found for route {route_id!r} "
            f"at stop {stop_name!r}."
        )

    updates["trip_id"] = updates["trip_id"].astype("string")
    updates["stop_id"] = updates["stop_id"].astype("string")
    if updates["trip_id"].isna().any() or updates["trip_id"].str.strip().eq("").any():
        raise ValueError("Matching trip updates contain missing trip IDs.")
    updates["feed_timestamp"] = pd.to_datetime(
        updates["feed_timestamp"], utc=True, errors="raise"
    )
    if updates["feed_timestamp"].isna().any():
        raise ValueError("Matching trip updates contain missing feed timestamps.")
    updates["arrival_delay"] = pd.to_numeric(updates["arrival_delay"], errors="raise")
    if np.isinf(updates["arrival_delay"].dropna()).any():
        raise ValueError("Matching trip updates contain infinite arrival delays.")

    start_descriptors = (
        updates["trip_start_timestamp"].astype("string").str.strip().replace("", pd.NA)
    )
    missing_starts = start_descriptors.isna()
    if missing_starts.any():
        warnings.warn(
            f"{int(missing_starts.sum())} matching trip updates have no start "
            f"descriptor; grouping those rows by {agency_timezone} publication "
            "date is approximate and may split trips spanning midnight.",
            UserWarning,
            stacklevel=2,
        )
        publication_dates = (
            updates["feed_timestamp"]
            .dt.tz_convert(agency_timezone)
            .dt.strftime("%Y-%m-%d")
        )
        start_descriptors = start_descriptors.fillna(
            "missing-start-" + publication_dates
        )
    updates["trip_instance_key"] = updates["trip_id"] + "|" + start_descriptors
    visit_columns = ["trip_instance_key", "stop_id"]
    if "stop_sequence" in updates.columns:
        visit_columns.append("stop_sequence")
    order_columns = ["feed_timestamp"]
    if "id" in updates.columns:
        order_columns.append("id")
    latest = (
        updates.sort_values(order_columns, ascending=False, kind="stable")
        .drop_duplicates(visit_columns, keep="first")
        .sort_values("feed_timestamp", kind="stable")
        .reset_index(drop=True)
    )
    latest["stop_name"] = stop_name
    latest["delay_minutes"] = latest["arrival_delay"] / 60.0
    return latest


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
