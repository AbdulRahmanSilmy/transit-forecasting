"""Temporary correction for realtime timestamps stored with a clock shift.

Since the ingestion restart after the 2026-11-01/02 feed outage, stored
realtime instants are one hour earlier than true UTC (correct before it). The
root cause is under investigation (see the GitHub issue on shifted
timestamps, GitHub issue #13). Until the storage path is fixed and the archive re-exported, the
study harness adds the known shift back for rows written inside configured
windows, and checks the result against the agency's trip start descriptors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

TRIP_UPDATE_TIME_COLUMNS = ("feed_timestamp", "arrival_time", "departure_time")


def correct_timestamp_shifts(
    frame: pd.DataFrame,
    shifts: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str] = TRIP_UPDATE_TIME_COLUMNS,
) -> pd.DataFrame:
    """Add a known clock shift back to rows written inside given windows.

    A row's write time is its stored ``feed_timestamp`` (rows are ingested as
    they are published). Every listed timestamp column of a selected row is
    shifted by the same amount. Missing values stay missing.

    Parameters
    ----------
    frame : pandas.DataFrame
        Realtime table with ``feed_timestamp``; not modified.
    shifts : sequence of mapping
        Each has ``from_stored`` (inclusive, stored UTC clock), ``to_stored``
        (exclusive, or None for open-ended) and ``add_hours``. Windows must not
        overlap.
    columns : sequence of str, optional
        Timestamp columns to shift; absent columns are skipped.

    Returns
    -------
    pandas.DataFrame
        Copy with shifted columns as timezone-aware UTC timestamps.

    Raises
    ------
    ValueError
        If windows overlap or are empty.
    """
    corrected = frame.copy()
    written = pd.to_datetime(corrected["feed_timestamp"], utc=True)
    selected_any = pd.Series(False, index=corrected.index)
    for column in columns:
        if column in corrected:
            corrected[column] = pd.to_datetime(corrected[column], utc=True)
    for shift in shifts:
        start = pd.Timestamp(shift["from_stored"], tz="UTC")
        end = shift.get("to_stored")
        end = pd.Timestamp(end, tz="UTC") if end is not None else None
        if end is not None and end <= start:
            raise ValueError(f"Empty timestamp shift window: {shift}.")
        selected = written.ge(start) & (written.lt(end) if end is not None else True)
        if (selected & selected_any).any():
            raise ValueError("Timestamp shift windows overlap.")
        selected_any |= selected
        delta = pd.Timedelta(hours=float(shift["add_hours"]))
        for column in columns:
            if column in corrected:
                corrected.loc[selected, column] = (
                    corrected.loc[selected, column] + delta
                )
    return corrected


def schedule_clock_agreement(
    visits: pd.DataFrame,
    *,
    timezone: str = "America/Vancouver",
    tolerance_minutes: float = 1.0,
) -> pd.DataFrame:
    """Compare first-stop scheduled departures with the trip start descriptor.

    The trip start descriptor is the agency's local start time and is stored
    unshifted, so a journey's origin-feed scheduled departure at its first stop,
    converted to local wall time, should equal it. A systematic one-hour
    disagreement indicates shifted realtime timestamps.

    Parameters
    ----------
    visits : pandas.DataFrame
        Prepared visits with ``trip_instance_key`` (``trip_id|start``),
        ``is_first_stop``, ``scheduled_departure`` and ``service_day``.
    timezone : str, default "America/Vancouver"
        Agency timezone.
    tolerance_minutes : float, default 1.0
        Maximum absolute difference counted as agreement.

    Returns
    -------
    pandas.DataFrame
        One row per service month: ``journeys``, ``agreement`` (share within
        tolerance) and ``median_difference_minutes`` (scheduled minus
        descriptor).
    """
    first = visits.loc[
        visits["is_first_stop"] & visits["scheduled_departure"].notna()
    ].copy()
    descriptor = pd.to_datetime(
        first["trip_instance_key"].str.split("|").str[1], errors="coerce"
    )
    local = first["scheduled_departure"].dt.tz_convert(timezone).dt.tz_localize(None)
    first["difference_minutes"] = (local - descriptor).dt.total_seconds() / 60
    first = first.loc[first["difference_minutes"].notna()]
    first["agrees"] = first["difference_minutes"].abs().le(tolerance_minutes)
    month = first["service_day"].dt.to_period("M").astype("string")
    return (
        first.groupby(month)
        .agg(
            journeys=("agrees", "size"),
            agreement=("agrees", "mean"),
            median_difference_minutes=("difference_minutes", "median"),
        )
        .rename_axis("service_month")
        .reset_index()
    )
