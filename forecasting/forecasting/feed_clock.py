"""Measure the offset between realtime event times and the public timetable.

Since its restart after the 2026-11-01/02 outage, Transit BC's GTFS-Realtime
feed labels vehicles with trips (``trip_id`` and start descriptor) whose
public timetable is one hour **later** than the vehicles' actual movements
(GitHub issue #13). Event times are physical: header timestamps match real
time, and vehicle GPS positions are at the reported stop at the reported
arrival time. What is offset is the trip label, so ``arrival_time -
arrival_delay`` (the feed's schedule) runs an hour before the timetable the
label refers to.

Event times therefore stay untouched. Each journey's offset is measured
against its own start descriptor (local wall time, which matches the static
timetable), so analyses can choose which clock keys recurring patterns:

- **feed clock:** the feed's schedule, aligned with physical event times;
- **timetable clock:** feed schedule plus offset, the time of the trip the
  vehicle is labelled with.

:func:`timetable_offset_summary` reports the offsets by month, so any future
change at the source (including a fix) shows up without hard-coded dates.
"""

from __future__ import annotations

import pandas as pd


def journey_timetable_offsets(
    first_scheduled_departure: pd.Series,
    descriptor: pd.Series,
    *,
    timezone: str = "America/Vancouver",
    tolerance_minutes: float = 1.0,
) -> tuple[pd.Series, pd.Series]:
    """Whole-hour offsets from feed schedules to start descriptors.

    Parameters
    ----------
    first_scheduled_departure : pandas.Series
        Aware UTC scheduled departure at each journey's first stop, from the
        feed (event time minus delay).
    descriptor : pandas.Series
        Naive local trip start descriptor, aligned with the first argument.
    timezone : str, default "America/Vancouver"
        Descriptor timezone.
    tolerance_minutes : float, default 1.0
        Allowed residual after rounding to whole hours.

    Returns
    -------
    offset_minutes : pandas.Series
        Descriptor minus feed schedule, in whole hours expressed as minutes;
        NaN when unknown or not a whole number of hours.
    mismatch : pandas.Series
        True where both values exist but differ by more than the tolerance
        from every whole hour.
    """
    local = first_scheduled_departure.dt.tz_convert(timezone).dt.tz_localize(None)
    difference = pd.to_datetime(descriptor, errors="coerce") - local
    hours = (difference / pd.Timedelta(hours=1)).round()
    residual = (difference - hours * pd.Timedelta(hours=1)).abs()
    within = residual.le(pd.Timedelta(minutes=tolerance_minutes))
    offset_minutes = (hours * 60).where(within).astype(float)
    mismatch = difference.notna() & ~within
    return offset_minutes, mismatch


def timetable_offset_summary(visits: pd.DataFrame) -> pd.DataFrame:
    """Summarize journey timetable offsets by service month.

    Parameters
    ----------
    visits : pandas.DataFrame
        Prepared visits with ``is_first_stop``, ``service_day``,
        ``timetable_offset_minutes`` and ``timetable_clock_mismatch``.

    Returns
    -------
    pandas.DataFrame
        One row per service month: ``journeys``, ``share_offset_0``,
        ``share_offset_60`` (timetable an hour after the feed),
        ``share_other_offset`` (other whole hours), ``share_unknown`` (no
        descriptor or first-stop schedule) and ``share_mismatch`` (not a whole
        number of hours).
    """
    first = visits.loc[visits["is_first_stop"]]
    offset = first["timetable_offset_minutes"]
    mismatch = first["timetable_clock_mismatch"].astype(bool)
    frame = pd.DataFrame(
        {
            "month": first["service_day"].dt.to_period("M").astype("string"),
            "offset_0": offset.eq(0),
            "offset_60": offset.eq(60),
            "other": offset.notna() & ~offset.isin([0, 60]),
            "unknown": offset.isna() & ~mismatch,
            "mismatch": mismatch,
        }
    )
    grouped = frame.groupby("month")
    summary = grouped[["offset_0", "offset_60", "other", "unknown", "mismatch"]].mean()
    summary.columns = [
        "share_offset_0",
        "share_offset_60",
        "share_other_offset",
        "share_unknown",
        "share_mismatch",
    ]
    summary.insert(0, "journeys", grouped.size())
    return summary.rename_axis("service_month").reset_index()
