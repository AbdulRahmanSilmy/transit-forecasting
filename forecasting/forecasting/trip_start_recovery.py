"""Recover trip start descriptors lost for post-midnight service.

Until data_ingestion 0.4.1, ingestion parsed ``start_date`` and
``start_time`` with ``%H``, so every trip whose GTFS start time is
``24:00:00`` or later (service after midnight on the previous service day)
was stored with a null ``trip_start_timestamp`` (GitHub issue #36). The raw
strings were not stored, so the descriptor has to be rebuilt.

A journey's start descriptor is the local wall time of its scheduled
departure from the first stop. The feed carries that schedule implicitly:
event time minus delay at the first stop in the journey's earliest
publication. :func:`fill_missing_trip_starts` rebuilds it for trip updates
and :func:`fill_missing_vehicle_trip_starts` copies it onto vehicle
positions of the same journeys.

The rebuilt descriptor is the feed's own schedule. On days when the feed's
schedule sits a whole hour off the timetable (the 2026-11-01 outage, see
:mod:`forecasting.feed_clock`), a rebuilt descriptor inherits that offset.
"""

from __future__ import annotations

import pandas as pd

RECOVERED_COLUMN = "trip_start_recovered"
TRIP_UPDATE_COLUMNS = (
    "trip_id",
    "trip_start_timestamp",
    "stop_sequence",
    "feed_timestamp",
    "arrival_time",
    "arrival_delay",
)
VEHICLE_UPDATE_COLUMNS = (
    "trip_id",
    "trip_start_timestamp",
    "timestamp",
    "feed_timestamp",
)


def fill_missing_trip_starts(
    trip_updates: pd.DataFrame,
    *,
    timezone: str = "America/Vancouver",
    max_gap_hours: float = 6.0,
) -> pd.DataFrame:
    """Fill null trip start descriptors from each journey's first-stop schedule.

    Rows with a null ``trip_start_timestamp`` are grouped by ``trip_id`` into
    journeys: sorted by scheduled time (event time minus delay), a new
    journey starts wherever the schedule jumps by more than
    ``max_gap_hours``. A trip ID runs at most once per service day, so
    journeys on different days are about 24 hours apart. Each journey's
    descriptor is the local wall time of its first-stop scheduled departure
    in its earliest publication (``feed_timestamp``, then ``id``).

    A journey is filled only if it includes the trip's first stop, the
    lowest ``stop_sequence`` the trip ever reports in the frame. Journeys
    first seen mid-route, and rows without a usable schedule, stay null.

    Parameters
    ----------
    trip_updates : pandas.DataFrame
        Trip updates with ``trip_id``, ``trip_start_timestamp``,
        ``stop_sequence``, ``feed_timestamp``, ``arrival_time`` and
        ``arrival_delay``; ``departure_time``, ``departure_delay`` and ``id``
        are used when present. Naive event times are UTC; delays are seconds.
    timezone : str, default "America/Vancouver"
        Agency timezone of the descriptor (local wall time).
    max_gap_hours : float, default 6.0
        Largest gap in scheduled time within one journey.

    Returns
    -------
    pandas.DataFrame
        A copy with null descriptors filled where possible and a boolean
        ``trip_start_recovered`` column marking the filled rows. The
        descriptor keeps its dtype: naive local timestamps for a datetime
        column, ``YYYY-MM-DD HH:MM:SS`` strings otherwise.

    Raises
    ------
    ValueError
        If required columns are missing.
    """
    _require(trip_updates, TRIP_UPDATE_COLUMNS, "trip_updates")
    result = trip_updates.copy()
    result[RECOVERED_COLUMN] = False
    missing = _missing_descriptor(result["trip_start_timestamp"])
    if not missing.any():
        return result

    rows = result.loc[missing, ["trip_id", "stop_sequence", "feed_timestamp"]].copy()
    rows["order"] = result.loc[missing, "id"] if "id" in result else 0
    rows["feed_timestamp"] = _utc(rows["feed_timestamp"])
    rows["sequence"] = pd.to_numeric(rows["stop_sequence"], errors="coerce")
    rows["scheduled"] = _scheduled_utc(result.loc[missing])
    rows = rows.dropna(subset=["trip_id", "sequence", "scheduled"])
    if rows.empty:
        return result

    rows = rows.sort_values(["trip_id", "scheduled"], kind="stable")
    new_journey = (
        rows.groupby("trip_id", sort=False)["scheduled"]
        .diff()
        .gt(pd.Timedelta(hours=max_gap_hours))
    )
    rows["journey"] = new_journey.groupby(rows["trip_id"], sort=False).cumsum()
    keys = ["trip_id", "journey"]

    trip_first_sequence = (
        pd.to_numeric(trip_updates["stop_sequence"], errors="coerce")
        .groupby(trip_updates["trip_id"])
        .min()
    )
    journey_first_sequence = rows.groupby(keys)["sequence"].transform("min")
    first_stop = rows.loc[
        rows["sequence"].eq(journey_first_sequence)
        & rows["sequence"].eq(rows["trip_id"].map(trip_first_sequence))
    ]
    origin = first_stop.sort_values(
        ["feed_timestamp", "order"], kind="stable"
    ).drop_duplicates(keys, keep="first")
    descriptors = (
        origin.set_index(keys)["scheduled"].dt.tz_convert(timezone).dt.tz_localize(None)
    )
    filled = rows.join(descriptors.rename("descriptor"), on=keys)["descriptor"]
    filled = filled.dropna()
    if filled.empty:
        return result

    result.loc[filled.index, "trip_start_timestamp"] = _like(
        filled, result["trip_start_timestamp"]
    )
    result.loc[filled.index, RECOVERED_COLUMN] = True
    return result


def fill_missing_vehicle_trip_starts(
    vehicle_updates: pd.DataFrame,
    trip_updates: pd.DataFrame,
    *,
    timezone: str = "America/Vancouver",
    tolerance_hours: float = 6.0,
) -> pd.DataFrame:
    """Copy start descriptors onto vehicle positions that lack one.

    Each vehicle row with a null ``trip_start_timestamp`` takes the
    descriptor of the trip-update journey with the same ``trip_id`` whose
    start (descriptor as UTC) is nearest its position time, if that is
    within ``tolerance_hours``. Run :func:`fill_missing_trip_starts` on the
    trip updates first so post-midnight journeys have descriptors.

    Parameters
    ----------
    vehicle_updates : pandas.DataFrame
        Vehicle positions with ``trip_id``, ``trip_start_timestamp`` and
        ``timestamp`` (falling back to ``feed_timestamp`` where missing).
        Naive times are UTC.
    trip_updates : pandas.DataFrame
        Trip updates with ``trip_id`` and ``trip_start_timestamp``.
    timezone : str, default "America/Vancouver"
        Agency timezone of the descriptor.
    tolerance_hours : float, default 6.0
        Largest distance between a position time and a journey start. Keep
        it under 12 hours so a position cannot match the previous or next
        day's journey.

    Returns
    -------
    pandas.DataFrame
        A copy with null descriptors filled where matched and a boolean
        ``trip_start_recovered`` column marking the filled rows.

    Raises
    ------
    ValueError
        If required columns are missing.
    """
    _require(vehicle_updates, VEHICLE_UPDATE_COLUMNS, "vehicle_updates")
    _require(trip_updates, ("trip_id", "trip_start_timestamp"), "trip_updates")
    result = vehicle_updates.copy()
    result[RECOVERED_COLUMN] = False
    missing = _missing_descriptor(result["trip_start_timestamp"])
    if not missing.any():
        return result

    rows = result.loc[missing, ["trip_id"]].copy()
    rows["row"] = rows.index
    rows["time"] = _utc(result.loc[missing, "timestamp"]).fillna(
        _utc(result.loc[missing, "feed_timestamp"])
    )
    rows = rows.dropna(subset=["trip_id", "time"])
    trip_ids = rows["trip_id"].unique()
    journeys = trip_updates.loc[
        trip_updates["trip_id"].isin(trip_ids)
        & ~_missing_descriptor(trip_updates["trip_start_timestamp"]),
        ["trip_id", "trip_start_timestamp"],
    ].drop_duplicates()
    if rows.empty or journeys.empty:
        return result
    journeys["descriptor"] = pd.to_datetime(
        journeys["trip_start_timestamp"].astype("string"), errors="coerce"
    )
    journeys = journeys.dropna(subset=["descriptor"])
    journeys["start"] = (
        journeys["descriptor"]
        .dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )
    journeys = journeys.dropna(subset=["start"])

    rows["time"] = rows["time"].astype(journeys["start"].dtype)
    matched = pd.merge_asof(
        rows.sort_values("time"),
        journeys[["trip_id", "start", "descriptor"]].sort_values("start"),
        left_on="time",
        right_on="start",
        by="trip_id",
        direction="nearest",
        tolerance=pd.Timedelta(hours=tolerance_hours),
    ).dropna(subset=["descriptor"])
    if matched.empty:
        return result

    filled = matched.set_index("row")["descriptor"]
    result.loc[filled.index, "trip_start_timestamp"] = _like(
        filled, result["trip_start_timestamp"]
    )
    result.loc[filled.index, RECOVERED_COLUMN] = True
    return result


def _require(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {', '.join(missing)}")


def _missing_descriptor(descriptor: pd.Series) -> pd.Series:
    text = descriptor.astype("string").str.strip()
    return (text.isna() | text.eq("")).astype(bool)


def _utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="coerce")


def _scheduled_utc(frame: pd.DataFrame) -> pd.Series:
    """Event time minus delay: departure where known, else arrival."""

    def scheduled(time_column: str, delay_column: str) -> pd.Series:
        if time_column not in frame or delay_column not in frame:
            return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
        delay = pd.to_numeric(frame[delay_column], errors="coerce")
        return _utc(frame[time_column]) - pd.to_timedelta(delay, unit="s")

    departure = scheduled("departure_time", "departure_delay")
    return departure.fillna(scheduled("arrival_time", "arrival_delay"))


def _like(values: pd.Series, target: pd.Series) -> pd.Series:
    """Express naive local timestamps in the target column's representation."""
    if pd.api.types.is_datetime64_any_dtype(target):
        return values.astype(target.dtype)
    return values.dt.strftime("%Y-%m-%d %H:%M:%S")
