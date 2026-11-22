"""Measure the offset between realtime event times and the public timetable.

British Columbia has kept UTC-7 all year since 2026-03-08, so there was no
fall-back on 2026-11-01. A timezone database older than that change converts
later UTC times with UTC-8, so every local time from 2026-11-01 onwards looks
one hour early and each journey's feed schedule sits one hour before its
start descriptor (GitHub issue #13). The feed itself was correct, apart from
schedule-only data published during the 2026-11-01 outage.

Event times stay untouched. Each journey's whole-hour offset is measured
against its own start descriptor (local wall time, which matches the static
timetable), so analyses can choose which clock keys recurring patterns:

- **feed clock:** the feed's schedule converted to local time;
- **timetable clock:** feed schedule plus offset, the local time of the trip
  named in the start descriptor.

With an up-to-date timezone database the two clocks agree (offset 0) for
almost every journey. The timetable clock also stays correct when the local
conversion is wrong, so it is the safer choice for calendar features.
:func:`timetable_offset_summary` reports the offsets by month, so any future
offset shows up without hard-coded dates.
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
