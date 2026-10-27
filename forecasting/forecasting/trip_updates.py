"""Utilities for selecting and associating GTFS trip updates."""

import warnings
from collections.abc import Sequence

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
    updates, visit_columns = _prepare_stop_delay_updates(
        df_trip_updates,
        df_stops,
        route_id=route_id,
        stop_name=stop_name,
        agency_timezone=agency_timezone,
    )
    return (
        updates.drop_duplicates(visit_columns, keep="first")
        .sort_values("feed_timestamp", kind="stable")
        .reset_index(drop=True)
    )


def _prepare_stop_delay_updates(
    df_trip_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    *,
    route_id: str,
    stop_name: str,
    agency_timezone: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Normalize and order a stop's prediction history without mutating inputs.

    Parameters
    ----------
    df_trip_updates : pandas.DataFrame
        Realtime trip updates.
    df_stops : pandas.DataFrame
        Static GTFS stops.
    route_id : str
        Route to select.
    stop_name : str
        Exact stop name to select.
    agency_timezone : str
        Timezone for approximate grouping of missing trip start descriptors.

    Returns
    -------
    tuple[pandas.DataFrame, list[str]]
        Validated updates, newest first with database ID breaking timestamp
        ties, and columns identifying a trip's stop visit.
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
    updates = updates.sort_values(order_columns, ascending=False, kind="stable")
    updates["stop_name"] = stop_name
    updates["delay_minutes"] = updates["arrival_delay"] / 60.0
    return updates, visit_columns


def get_stop_delay_horizons(
    df_trip_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    *,
    route_id: str,
    stop_name: str,
    horizons_minutes: Sequence[int] = range(1, 31),
    agency_timezone: str = "America/Vancouver",
) -> pd.DataFrame:
    """Compare historical predictions with each stop visit's final delay.

    Use the same visit grouping and final-row selection as
    ``get_latest_stop_delays``. The final row's ``arrival_delay`` is the truth
    proxy, and its ``arrival_time`` anchors the look-back intervals. For each
    interval, select the latest update published at or before that cutoff,
    never a later update or an interpolated value. Timestamp ties use the
    greatest database ``id`` when available, otherwise the first input row.

    Visits with missing final delays or arrival times, or final arrival times
    after the input's last publication, are excluded with a warning. This
    removes obviously unfinished visits, but does not establish ground truth.
    A missing delay in the selected historical row remains missing; an older
    nonmissing delay is not substituted. Neither input frame is modified.

    Parameters
    ----------
    df_trip_updates : pandas.DataFrame
        Updates accepted by ``get_latest_stop_delays``, plus ``arrival_time``
        as a datetime or datetime string. Naive timestamps are treated as UTC.
        Filter this frame to a single direction/platform before calling when
        needed; different platforms and stop sequences are never pooled.
    df_stops : pandas.DataFrame
        GTFS stops containing ``stop_id`` and ``stop_name``.
    route_id : str
        GTFS route identifier.
    stop_name : str
        Exact stop name.
    horizons_minutes : collections.abc.Sequence of int, optional
        Unique positive look-back intervals, defaulting to every minute from
        1 through 30.
    agency_timezone : str, optional
        Timezone for missing-start grouping, default ``"America/Vancouver"``.

    Returns
    -------
    pandas.DataFrame
        One row per eligible visit and horizon, including trip/stop identifiers,
        ``reference_arrival_time``, ``reference_feed_timestamp``,
        ``true_delay_minutes``, ``horizon_minutes``, ``cutoff_time``,
        ``estimate_feed_timestamp``, ``estimated_delay_minutes``,
        ``estimate_age_minutes`` (age at the cutoff), ``error_minutes``
        (estimate minus truth), and ``absolute_error_minutes``. Missing
        predictions remain present as NaN rows, allowing coverage measurement.

    Raises
    ------
    ValueError
        If horizons or matching data are invalid, ``arrival_time`` is absent,
        or no visits have usable final references.

    Warns
    -----
    UserWarning
        If approximate daily grouping is needed or references are excluded.
    """
    horizons = list(horizons_minutes)
    if (
        not horizons
        or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            or value <= 0
            for value in horizons
        )
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError("horizons_minutes must contain unique positive integers.")

    updates, visit_columns = _prepare_stop_delay_updates(
        df_trip_updates,
        df_stops,
        route_id=route_id,
        stop_name=stop_name,
        agency_timezone=agency_timezone,
    )
    if "arrival_time" not in updates.columns:
        raise ValueError("df_trip_updates is missing required columns: arrival_time")
    updates["_stop_visit_id"] = updates.groupby(
        visit_columns, dropna=False, sort=False
    ).ngroup()
    references = updates.drop_duplicates(visit_columns, keep="first").copy()
    references["arrival_time"] = pd.to_datetime(
        references["arrival_time"], utc=True, errors="raise"
    )
    observation_end = pd.to_datetime(
        df_trip_updates["feed_timestamp"], utc=True, errors="raise"
    ).max()
    missing_reference = (
        references["arrival_time"].isna() | references["arrival_delay"].isna()
    )
    unfinished_reference = references["arrival_time"].gt(observation_end)
    excluded = missing_reference | unfinished_reference
    if excluded.any():
        warnings.warn(
            f"Excluded {int(excluded.sum())} stop visits: "
            f"{int(missing_reference.sum())} with missing final delay/arrival "
            f"and {int(unfinished_reference.sum())} with final arrival after "
            "the recording window.",
            UserWarning,
            stacklevel=2,
        )
    references = references.loc[~excluded]
    if references.empty:
        raise ValueError("No stop visits have usable final delay/arrival references.")

    reference_columns = [
        "_stop_visit_id",
        "trip_id",
        "trip_start_timestamp",
        "trip_instance_key",
        "stop_id",
        "stop_name",
        "feed_timestamp",
        "arrival_time",
        "delay_minutes",
    ]
    reference_columns.extend(
        column
        for column in ("stop_sequence", "trip_direction_id")
        if column in references.columns
    )
    references = references[reference_columns].rename(
        columns={
            "feed_timestamp": "reference_feed_timestamp",
            "arrival_time": "reference_arrival_time",
            "delay_minutes": "true_delay_minutes",
        }
    )
    targets = references.merge(
        pd.DataFrame({"horizon_minutes": sorted(horizons)}), how="cross"
    )
    targets["cutoff_time"] = targets["reference_arrival_time"] - pd.to_timedelta(
        targets["horizon_minutes"], unit="min"
    )
    history = (
        updates.drop_duplicates(["_stop_visit_id", "feed_timestamp"], keep="first")[
            ["_stop_visit_id", "feed_timestamp", "delay_minutes"]
        ]
        .rename(
            columns={
                "feed_timestamp": "estimate_feed_timestamp",
                "delay_minutes": "estimated_delay_minutes",
            }
        )
        .sort_values("estimate_feed_timestamp", kind="stable")
    )
    result = pd.merge_asof(
        targets.sort_values("cutoff_time", kind="stable"),
        history,
        left_on="cutoff_time",
        right_on="estimate_feed_timestamp",
        by="_stop_visit_id",
        direction="backward",
        allow_exact_matches=True,
    )
    result["estimate_age_minutes"] = (
        result["cutoff_time"] - result["estimate_feed_timestamp"]
    ).dt.total_seconds() / 60.0
    result["error_minutes"] = (
        result["estimated_delay_minutes"] - result["true_delay_minutes"]
    )
    result["absolute_error_minutes"] = result["error_minutes"].abs()
    return (
        result.sort_values(
            ["reference_arrival_time", "_stop_visit_id", "horizon_minutes"],
            kind="stable",
        )
        .drop(columns="_stop_visit_id")
        .reset_index(drop=True)
    )


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
