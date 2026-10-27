"""Describe scheduled service without removing engine segment inventory."""

from pathlib import Path

import pandas as pd

from .road_segment_congestion import CongestionResult, _require_columns

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def get_congestion_service_activity(
    result: CongestionResult,
    static_data_dir: str | Path,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Count scheduled segment traversals overlapping a selected UTC period.

    Both weekly calendars and dated additions/removals are respected. Trip
    intervals use agency-local GTFS service days, including times beyond
    24:00. A trip is counted when any of its scheduled interval overlaps the
    period; this describes variant activity, not exact bus locations.
    No engine segments or observations are removed.

    Parameters
    ----------
    result : CongestionResult
        Engine result whose segments are being displayed.
    static_data_dir : str or pathlib.Path
        Matching snapshot containing agency, trips, stop times, and at least
        one of ``calendar.txt`` or ``calendar_dates.txt``.
    start, end : str or pandas.Timestamp
        Inclusive selected period. Naive bounds are interpreted as UTC.

    Returns
    -------
    pandas.DataFrame
        ``segment_id``, ``scheduled_trips``, and ``service_active`` for every
        engine segment. Counts describe scheduled, not observed, trips.

    Raises
    ------
    FileNotFoundError
        If required schedule files or both calendar files are absent.
    ValueError
        If bounds, timezone, service definitions, or trip metadata are invalid.
    """
    start = pd.to_datetime(start, utc=True)
    end = pd.to_datetime(end, utc=True)
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError("Service period requires valid start <= end.")
    root = Path(static_data_dir)
    agency = pd.read_csv(root / "agency.txt", dtype="string")
    _require_columns(agency, ("agency_timezone",), "agency")
    zones = agency["agency_timezone"].dropna().unique()
    if len(zones) != 1:
        raise ValueError("Service activity requires one unambiguous agency timezone.")
    zone = str(zones[0])

    trips = pd.read_csv(root / "trips.txt", dtype="string")
    _require_columns(
        trips,
        ("trip_id", "route_id", "shape_id", "direction_id", "service_id"),
        "trips",
    )
    trips = trips.loc[trips["route_id"].eq(result.route_id)].copy()
    trips["trip_direction_id"] = pd.to_numeric(trips["direction_id"], errors="raise")
    trips = trips.loc[
        trips["trip_direction_id"].isin(result.segments["trip_direction_id"])
    ].copy()
    if trips.empty or result.segments.empty:
        raise ValueError("Schedule must contain trips for the engine segments.")
    if trips[["trip_id", "shape_id", "service_id"]].isna().any().any():
        raise ValueError("Selected trips have missing service or trip metadata.")
    if trips["trip_id"].duplicated().any():
        raise ValueError("Selected schedule trip IDs must be unique.")

    stop_times = pd.read_csv(root / "stop_times.txt", dtype="string")
    _require_columns(
        stop_times,
        ("trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"),
        "stop_times",
    )
    stop_times = stop_times.loc[stop_times["trip_id"].isin(trips["trip_id"])].copy()
    stop_times["stop_sequence"] = pd.to_numeric(
        stop_times["stop_sequence"], errors="raise"
    )
    if (
        stop_times[["stop_id", "stop_sequence"]].isna().any().any()
        or stop_times.duplicated(["trip_id", "stop_sequence"]).any()
    ):
        raise ValueError("Selected stop visits must have unique, valid sequences.")
    stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])
    first = stop_times.groupby("trip_id").head(1)[["trip_id", "departure_time"]]
    last = stop_times.groupby("trip_id").tail(1)[["trip_id", "arrival_time"]]
    trips = trips.merge(first, on="trip_id", how="left", validate="one_to_one").merge(
        last, on="trip_id", how="left", validate="one_to_one"
    )
    trips["departure"] = pd.to_timedelta(trips["departure_time"], errors="raise")
    trips["arrival"] = pd.to_timedelta(trips["arrival_time"], errors="raise")
    if (
        trips[["departure", "arrival"]].isna().any().any()
        or trips["departure"].lt(pd.Timedelta(0)).any()
        or trips["arrival"].lt(trips["departure"]).any()
    ):
        raise ValueError("Trips require valid first departure and last arrival times.")

    calendar_path = root / "calendar.txt"
    exceptions_path = root / "calendar_dates.txt"
    if not calendar_path.is_file() and not exceptions_path.is_file():
        raise FileNotFoundError(
            "Service activity requires calendar.txt or calendar_dates.txt."
        )
    calendar = pd.DataFrame(
        columns=["service_id", *_WEEKDAYS, "start_date", "end_date"]
    )
    if calendar_path.is_file():
        calendar = pd.read_csv(calendar_path, dtype="string")
        _require_columns(
            calendar, ("service_id", *_WEEKDAYS, "start_date", "end_date"), "calendar"
        )
        calendar = calendar.loc[calendar["service_id"].isin(trips["service_id"])].copy()
        if (
            calendar["service_id"].duplicated().any()
            or not calendar[list(_WEEKDAYS)].isin(["0", "1"]).all().all()
        ):
            raise ValueError("Weekly services require unique IDs and 0/1 weekdays.")
        for column in ("start_date", "end_date"):
            calendar[column] = pd.to_datetime(
                calendar[column], format="%Y%m%d", errors="raise"
            )
        if (
            calendar[["start_date", "end_date"]].isna().any().any()
            or calendar["start_date"].gt(calendar["end_date"]).any()
        ):
            raise ValueError("Weekly services require valid date ranges.")
    exceptions = pd.DataFrame(columns=["service_id", "date", "exception_type"])
    if exceptions_path.is_file():
        exceptions = pd.read_csv(exceptions_path, dtype="string")
        _require_columns(
            exceptions, ("service_id", "date", "exception_type"), "calendar_dates"
        )
        exceptions = exceptions.loc[
            exceptions["service_id"].isin(trips["service_id"])
        ].copy()
        exceptions["date"] = pd.to_datetime(
            exceptions["date"], format="%Y%m%d", errors="raise"
        )
        if (
            exceptions["date"].isna().any()
            or exceptions.duplicated(["service_id", "date"]).any()
            or not exceptions["exception_type"].isin(["1", "2"]).all()
        ):
            raise ValueError(
                "Dated services require unique dates and valid exceptions."
            )
    known_services = set(calendar["service_id"]) | set(exceptions["service_id"])
    missing_services = set(trips["service_id"]) - known_services
    if missing_services:
        raise ValueError(f"Missing service definitions: {sorted(missing_services)}")

    lookback_days = int(trips["arrival"].max() // pd.Timedelta(days=1))
    first_day = start.tz_convert(zone).tz_localize(None).normalize() - pd.Timedelta(
        days=lookback_days + 1
    )
    last_day = end.tz_convert(zone).tz_localize(None).normalize() + pd.Timedelta(days=1)
    counts = pd.Series(0, index=trips["trip_id"], dtype="int64")
    for day in pd.date_range(first_day, last_day, freq="D"):
        active = set()
        if not calendar.empty:
            active.update(
                calendar.loc[
                    calendar["start_date"].le(day)
                    & calendar["end_date"].ge(day)
                    & calendar[_WEEKDAYS[day.weekday()]].eq("1"),
                    "service_id",
                ]
            )
        dated = exceptions.loc[exceptions["date"].eq(day)]
        active.difference_update(
            dated.loc[dated["exception_type"].eq("2"), "service_id"]
        )
        active.update(dated.loc[dated["exception_type"].eq("1"), "service_id"])
        selected = trips.loc[trips["service_id"].isin(active)]
        # GTFS measures service time from local noon minus twelve hours.
        origin = (day + pd.Timedelta(hours=12)).tz_localize(zone) - pd.Timedelta(
            hours=12
        )
        overlaps = (origin + selected["departure"]).le(end) & (
            origin + selected["arrival"]
        ).ge(start)
        counts.loc[selected.loc[overlaps, "trip_id"]] += 1

    stop_times["from_stop_id"] = stop_times.groupby("trip_id")["stop_id"].shift()
    edges = stop_times.rename(columns={"stop_id": "to_stop_id"}).merge(
        trips[["trip_id", "shape_id", "trip_direction_id"]], on="trip_id"
    )
    keys = ["trip_direction_id", "shape_id", "from_stop_id", "to_stop_id"]
    membership = edges.merge(
        result.segments[["segment_id", *keys]], on=keys, validate="many_to_one"
    ).drop_duplicates(["trip_id", "segment_id"])
    if set(membership["segment_id"]) != set(result.segments["segment_id"]):
        raise ValueError("Schedule does not describe every engine segment.")
    membership["scheduled_trips"] = membership["trip_id"].map(counts)
    activity = membership.groupby("segment_id", as_index=False)["scheduled_trips"].sum()
    activity["service_active"] = activity["scheduled_trips"].gt(0)
    return activity
