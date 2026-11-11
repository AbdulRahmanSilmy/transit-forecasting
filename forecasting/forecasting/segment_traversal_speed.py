"""Full-segment speeds from final reported stop times."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .road_segment_congestion import (
    CongestionResult,
    _normalize_direction_ids,
    _require_columns,
)
from .trip_updates import get_latest_stop_delays


def get_segment_traversal_speeds(
    result: CongestionResult,
    df_trip_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    *,
    segment_id: int,
) -> pd.DataFrame:
    """Calculate one speed per bus traversal of a directed segment.

    Divide the segment's shape-path length by the elapsed time from the final
    reported upstream departure to the final reported downstream arrival.
    Stop times are feed-estimate proxies, not independently measured events.
    Upstream dwell is excluded; holding and traffic within the segment remain.

    Match adjacent scheduled visits within the same trip/start descriptor,
    direction, and shape. Latest-row selection precedes missing-time checks,
    so an older prediction never replaces a missing final estimate. A bus ID
    must be unambiguous among the engine's matched probes for that trip
    instance. Repeated trips by the same physical bus remain separate rows.
    Neither the engine result nor any input frame is modified.

    Parameters
    ----------
    result : CongestionResult
        Speed-engine output providing directed segment geometry and matched
        probes with ``vehicle_id`` and ``trip_start_timestamp``.
    df_trip_updates : pandas.DataFrame
        Realtime trip updates with arrival/departure times, stop sequences,
        direction, start descriptors, and fields for ``get_latest_stop_delays``.
        Missing arrival delays do not invalidate otherwise usable times.
        Naive event/publication timestamps are interpreted as UTC.
    df_stops, df_stop_times, df_trips : pandas.DataFrame
        The same static GTFS tables used to construct ``result``.
    segment_id : int
        A segment ID from ``result.segments``.

    Returns
    -------
    pandas.DataFrame
        Completed traversals sorted by downstream ``arrival_time`` (UTC),
        including bus/trip identity, endpoint sequences, ``distance_meters``,
        ``travel_time_seconds``, ``speed_kmh``, and ``baseline_speed_kmh``.
        Distance sums spherical great-circle lengths along the shape vertices.
        The baseline is the median of returned traversal speeds, not the
        engine's instantaneous-speed baseline or a past-only forecast.

    Raises
    ------
    ValueError
        If required inputs or geometry are invalid, the segment is unknown,
        or no completed, unambiguously identified traversals remain.

    Warns
    -----
    UserWarning
        For excluded missing start descriptors or unusable traversals.
        A traversal is unfinished if its reported arrival exceeds the last
        selected trip-update publication/receipt timestamp.
    """
    if isinstance(segment_id, (bool, np.bool_)) or not isinstance(
        segment_id, (int, np.integer)
    ):
        raise ValueError("segment_id must be an integer.")
    selected = result.segments.loc[result.segments["segment_id"].eq(segment_id)]
    if len(selected) != 1:
        raise ValueError(f"Unknown or ambiguous segment_id {segment_id}.")
    segment = selected.iloc[0]
    _require_columns(
        df_trips, ("trip_id", "route_id", "direction_id", "shape_id"), "df_trips"
    )
    _require_columns(
        df_stop_times, ("trip_id", "stop_id", "stop_sequence"), "df_stop_times"
    )
    _require_columns(
        df_trip_updates,
        (
            "trip_route_id",
            "trip_direction_id",
            "trip_id",
            "trip_start_timestamp",
            "stop_id",
            "stop_sequence",
            "feed_timestamp",
            "arrival_delay",
            "arrival_time",
            "departure_time",
        ),
        "df_trip_updates",
    )
    _require_columns(
        result.vehicle_probes,
        ("trip_id", "trip_start_timestamp", "vehicle_id", "trip_direction_id"),
        "vehicle_probes",
    )

    coordinates = np.radians(np.asarray(segment["geometry"].coords))
    longitude, latitude = coordinates[:, 0], coordinates[:, 1]
    haversine = (
        np.sin(np.diff(latitude) / 2) ** 2
        + np.cos(latitude[:-1])
        * np.cos(latitude[1:])
        * np.sin(np.diff(longitude) / 2) ** 2
    )
    distance_meters = float(
        (2 * 6_371_008.8 * np.arcsin(np.sqrt(np.clip(haversine, 0, 1)))).sum()
    )
    if not np.isfinite(distance_meters) or distance_meters <= 0:
        raise ValueError("Selected segment must have positive finite path length.")

    trips = df_trips.loc[
        df_trips["route_id"].astype("string").eq(result.route_id)
        & df_trips["shape_id"].astype("string").eq(str(segment["shape_id"]))
    ].copy()
    directions = _normalize_direction_ids(trips["direction_id"], "df_trips")
    trip_ids = trips.loc[directions.eq(segment["trip_direction_id"]), "trip_id"].astype(
        "string"
    )
    scheduled = df_stop_times.loc[
        df_stop_times["trip_id"].astype("string").isin(trip_ids),
        ["trip_id", "stop_id", "stop_sequence"],
    ].copy()
    scheduled[["trip_id", "stop_id"]] = scheduled[["trip_id", "stop_id"]].astype(
        "string"
    )
    sequences = pd.to_numeric(scheduled["stop_sequence"], errors="raise")
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("GTFS stop sequences must be nonnegative integers.")
    scheduled["stop_sequence"] = sequences.astype("int64")
    if scheduled.duplicated(["trip_id", "stop_sequence"]).any():
        raise ValueError("GTFS stop times contain duplicate trip/stop sequences.")
    scheduled = scheduled.sort_values(["trip_id", "stop_sequence"])
    previous = scheduled.groupby("trip_id")[["stop_id", "stop_sequence"]].shift()
    scheduled[["from_stop_id", "from_stop_sequence"]] = previous
    scheduled = scheduled.rename(
        columns={"stop_id": "to_stop_id", "stop_sequence": "to_stop_sequence"}
    )
    scheduled = scheduled.loc[
        scheduled["from_stop_id"].eq(str(segment["from_stop_id"]))
        & scheduled["to_stop_id"].eq(str(segment["to_stop_id"]))
    ]
    if scheduled.empty:
        raise ValueError("Selected segment has no matching adjacent GTFS stop pairs.")

    updates = df_trip_updates.loc[
        df_trip_updates["trip_route_id"].astype("string").eq(result.route_id)
        & df_trip_updates["trip_id"].astype("string").isin(scheduled["trip_id"])
    ].copy()
    directions = _normalize_direction_ids(
        updates["trip_direction_id"], "df_trip_updates"
    )
    updates = updates.loc[directions.eq(segment["trip_direction_id"])].copy()
    if updates.empty:
        raise ValueError("No trip updates match the selected segment's trips.")
    updates["feed_timestamp"] = pd.to_datetime(
        updates["feed_timestamp"], utc=True, errors="raise"
    )
    if updates["feed_timestamp"].isna().any():
        raise ValueError("Matching trip updates contain missing feed timestamps.")
    observation_end = updates["feed_timestamp"].max()
    if "read_timestamp" in updates:
        receipts = pd.to_datetime(updates["read_timestamp"], utc=True, errors="raise")
        observation_end = (
            max(observation_end, receipts.max())
            if receipts.notna().any()
            else observation_end
        )
    missing_starts = (
        updates["trip_start_timestamp"]
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
        .isna()
    )
    if missing_starts.any():
        warnings.warn(
            f"Excluded {int(missing_starts.sum())} trip updates with missing trip "
            "starts; daily or midnight-spanning instances cannot be paired safely.",
            UserWarning,
            stacklevel=2,
        )
    updates = updates.loc[~missing_starts].copy()
    if updates.empty:
        raise ValueError("No matching trip updates have reliable trip starts.")
    sequences = pd.to_numeric(updates["stop_sequence"], errors="raise")
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("Realtime stop sequences must be nonnegative integers.")
    updates["stop_sequence"] = sequences.astype("int64")
    visits = get_latest_stop_delays(updates, df_stops, route_id=result.route_id)
    for column in ("arrival_time", "departure_time"):
        visits[column] = pd.to_datetime(visits[column], utc=True, errors="raise")
    visits["skipped"] = False
    if "schedule_relationship" in visits:
        visits["skipped"] = pd.to_numeric(
            visits["schedule_relationship"], errors="raise"
        ).isin([1, 2])
    if "trip_schedule_relationship" in visits:
        visits["skipped"] |= pd.to_numeric(
            visits["trip_schedule_relationship"], errors="raise"
        ).isin([3, 7])
    instances = visits[
        ["trip_id", "trip_instance_key", "trip_start_timestamp"]
    ].drop_duplicates("trip_instance_key")
    traversals = instances.merge(scheduled, on="trip_id", validate="many_to_many")
    endpoint_fields = [
        "stop_id",
        "stop_sequence",
        "arrival_time",
        "departure_time",
        "skipped",
    ]
    for prefix in ("from", "to"):
        endpoints = visits[["trip_instance_key", *endpoint_fields]].rename(
            columns={column: f"{prefix}_{column}" for column in endpoint_fields}
        )
        traversals = traversals.merge(
            endpoints,
            on=["trip_instance_key", f"{prefix}_stop_id", f"{prefix}_stop_sequence"],
            how="left",
            validate="many_to_one",
        )

    probes = result.vehicle_probes.loc[
        result.vehicle_probes["trip_id"].astype("string").isin(scheduled["trip_id"])
        & result.vehicle_probes["trip_direction_id"].eq(segment["trip_direction_id"])
    ].copy()
    starts = (
        probes["trip_start_timestamp"].astype("string").str.strip().replace("", pd.NA)
    )
    probes["trip_instance_key"] = probes["trip_id"].astype("string") + "|" + starts
    probes["vehicle_id"] = (
        probes["vehicle_id"].astype("string").str.strip().replace("", pd.NA)
    )
    identities = probes.groupby("trip_instance_key", as_index=False).agg(
        vehicle_id=("vehicle_id", "first"),
        vehicle_count=("vehicle_id", "nunique"),
    )
    traversals = traversals.merge(
        identities, on="trip_instance_key", how="left", validate="many_to_one"
    )
    traversals["travel_time_seconds"] = (
        traversals["to_arrival_time"] - traversals["from_departure_time"]
    ).dt.total_seconds()
    reasons = pd.Series("included", index=traversals.index, dtype="string")
    for reason, mask in (
        ("missing_upstream_departure", traversals["from_departure_time"].isna()),
        ("missing_downstream_arrival", traversals["to_arrival_time"].isna()),
        ("skipped_upstream_visit", traversals["from_skipped"].eq(True)),
        ("skipped_downstream_visit", traversals["to_skipped"].eq(True)),
        ("nonpositive_travel_time", traversals["travel_time_seconds"].le(0)),
        ("unfinished_traversal", traversals["to_arrival_time"].gt(observation_end)),
        ("missing_vehicle_identity", traversals["vehicle_id"].isna()),
        ("ambiguous_vehicle_identity", traversals["vehicle_count"].gt(1)),
    ):
        reasons.loc[reasons.eq("included") & mask] = reason
    excluded = reasons.ne("included")
    if excluded.any():
        counts = reasons.loc[excluded].value_counts()
        detail = ", ".join(f"{reason}={count}" for reason, count in counts.items())
        warnings.warn(
            f"Excluded {int(excluded.sum())} segment traversals: {detail}.",
            UserWarning,
            stacklevel=2,
        )
    traversals = traversals.loc[~excluded].copy()
    if traversals.empty:
        raise ValueError(
            "No completed segment traversals have final endpoint times and "
            "an unambiguous bus identity."
        )
    traversals = traversals.rename(
        columns={
            "from_departure_time": "departure_time",
            "to_arrival_time": "arrival_time",
        }
    )
    traversals["segment_id"] = segment_id
    traversals["trip_direction_id"] = segment["trip_direction_id"]
    traversals["shape_id"] = segment["shape_id"]
    traversals["distance_meters"] = distance_meters
    traversals["speed_kmh"] = distance_meters / traversals["travel_time_seconds"] * 3.6
    traversals["baseline_speed_kmh"] = traversals["speed_kmh"].median()
    columns = [
        "segment_id",
        "trip_direction_id",
        "shape_id",
        "vehicle_id",
        "trip_id",
        "trip_instance_key",
        "trip_start_timestamp",
        "from_stop_id",
        "to_stop_id",
        "from_stop_sequence",
        "to_stop_sequence",
        "departure_time",
        "arrival_time",
        "distance_meters",
        "travel_time_seconds",
        "speed_kmh",
        "baseline_speed_kmh",
    ]
    return (
        traversals[columns]
        .sort_values(["arrival_time", "vehicle_id", "trip_instance_key"], kind="stable")
        .reset_index(drop=True)
    )
