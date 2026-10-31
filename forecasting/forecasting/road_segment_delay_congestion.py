"""Estimate directed segment delay accumulation from final reported proxies."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from shapely.geometry import LineString, MultiLineString

from .road_segment_congestion import (
    _build_route_segments,
    _normalize_direction_ids,
    _require_columns,
)
from .trip_updates import get_latest_stop_delays

_SEGMENT_KEYS = ["segment_id", "trip_direction_id", "shape_id"]
_VISIT_KEYS = ["trip_id", "trip_direction_id", "stop_id", "stop_sequence"]
_ENDPOINT_FIELDS = [
    "stop_id",
    "stop_sequence",
    "arrival_delay",
    "arrival_time",
    "feed_timestamp",
    "available_at",
    "exclusion_reason",
]


@dataclass(frozen=True)
class DelayCongestionResult:
    """Inspectable outputs of the delay-proxy congestion engine.

    Attributes
    ----------
    route_id : str
        Selected GTFS route identifier.
    representative_trip_id : str
        Reference trip used by the shared geometry builder.
    route_geometry : LineString or MultiLineString
        Selected shapes in longitude/latitude coordinates.
    segments : pandas.DataFrame
        Directed, shape-specific stop pairs, including unobserved segments.
    stop_visits : pandas.DataFrame
        Latest reported visit rows, with availability and exclusion reasons.
        Missing latest estimates are never replaced by older estimates.
    traversals : pandas.DataFrame
        One candidate per trip instance and scheduled adjacent stop pair.
        Excluded candidates retain their reason and have no delta or time bin.
    congestion : pandas.DataFrame
        Observed segment/bin medians, traversal counts, delay spread, sign
        shares, last proxy arrival (``observed_at``), and ``available_at``.
    audit : pandas.DataFrame
        Stage/reason counts for retained and excluded records.
    observation_end : pandas.Timestamp
        Last input publication or receipt time, used to exclude unfinished
        visits. Arrival timestamps are final reported proxies, not GPS truth.
    """

    route_id: str
    representative_trip_id: str
    route_geometry: LineString | MultiLineString
    segments: pd.DataFrame
    stop_visits: pd.DataFrame
    traversals: pd.DataFrame
    congestion: pd.DataFrame
    audit: pd.DataFrame
    observation_end: pd.Timestamp


def run_delay_congestion_engine(
    df_trip_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    df_shapes: pd.DataFrame,
    *,
    route_id: str = "97-KEL",
    trip_direction_id: int = 0,
    time_bin_size: str = "5min",
) -> DelayCongestionResult:
    """Difference adjacent final arrival-delay proxies within each trip.

    A positive delta means added delay, zero means no added delay, and a
    negative delta means recovery relative to schedule. Arrival-to-arrival
    changes include upstream dwell and holding; they are not pure road
    traffic measurements. No speed baseline or absolute-delay threshold is
    used. Inputs are not modified.

    Latest-row selection uses publication time, then greatest database ID
    when available (otherwise first input row). Selection precedes missing
    estimate checks. Missing trip-start descriptors are excluded explicitly
    rather than approximately joining daily or midnight-spanning trips.
    Missing, skipped, unfinished, or temporally reversed endpoints never
    produce a delta. A completed traversal is available only after both
    endpoint publications/receipts and its downstream proxy arrival.

    Parameters
    ----------
    df_trip_updates : pandas.DataFrame
        Realtime rows containing route, direction, trip/start, stop/sequence,
        publication time, ``arrival_delay`` in seconds, and ``arrival_time``
        as datetime values or strings. Optional ``read_timestamp`` adds
        receipt-time availability; without it replay uses publication times
        with an explicit warning. Naive timestamps are interpreted as UTC.
    df_stops, df_stop_times, df_trips, df_shapes : pandas.DataFrame
        Matching static GTFS tables used by the speed engine's segment builder.
    route_id : str, optional
        GTFS route identifier, default ``"97-KEL"``.
    trip_direction_id : int, optional
        One direction, 0 or 1. The initial notebook selects 0.
    time_bin_size : str, optional
        Positive fixed UTC bin duration, default ``"5min"``. Traversals are
        assigned by their downstream final reported arrival timestamp.

    Returns
    -------
    DelayCongestionResult
        Auditable visits, candidate traversals, and observed segment bins.

    Raises
    ------
    ValueError
        If inputs are invalid or no usable completed adjacent pairs remain.

    Warns
    -----
    UserWarning
        If records are excluded or receipt timing is unavailable.
    """
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
        ),
        "df_trip_updates",
    )
    if isinstance(trip_direction_id, (bool, np.bool_)) or trip_direction_id not in (
        0,
        1,
    ):
        raise ValueError("trip_direction_id must be 0 or 1.")
    bin_duration = pd.Timedelta(time_bin_size)
    if pd.isna(bin_duration) or bin_duration <= pd.Timedelta(0):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    route_id = str(route_id)
    reference, geometry, segments, lookup = _build_route_segments(
        df_stops, df_stop_times, df_trips, df_shapes, route_id, trip_direction_id
    )
    updates = df_trip_updates.loc[
        df_trip_updates["trip_route_id"].astype("string").eq(route_id)
    ].copy()
    updates["trip_direction_id"] = _normalize_direction_ids(
        updates["trip_direction_id"], "df_trip_updates"
    )
    updates = updates.loc[updates["trip_direction_id"].eq(trip_direction_id)].copy()
    if updates.empty:
        raise ValueError("No trip updates match the selected route and direction.")
    updates["feed_timestamp"] = pd.to_datetime(
        updates["feed_timestamp"], utc=True, errors="raise"
    )
    if updates["feed_timestamp"].isna().any():
        raise ValueError("Matching trip updates contain missing feed timestamps.")
    if "read_timestamp" not in updates or updates["read_timestamp"].isna().any():
        warnings.warn(
            "Missing trip-update receipt timestamps; publication times are used "
            "for those records. Exact receipt-time replay is unverified.",
            UserWarning,
            stacklevel=2,
        )
    receipt = (
        pd.to_datetime(updates["read_timestamp"], utc=True, errors="raise")
        if "read_timestamp" in updates
        else updates["feed_timestamp"]
    )
    updates["available_at"] = pd.concat(
        [updates["feed_timestamp"], receipt], axis=1
    ).max(axis=1)
    observation_end = updates["available_at"].max()
    missing_starts = (
        updates["trip_start_timestamp"]
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
        .isna()
    )
    audit_rows = [
        {
            "stage": "updates",
            "reason": "selected_route_direction",
            "count": len(updates),
        },
        {
            "stage": "updates",
            "reason": "missing_trip_start",
            "count": int(missing_starts.sum()),
        },
    ]
    if missing_starts.any():
        warnings.warn(
            f"Excluded {int(missing_starts.sum())} updates with missing trip starts "
            "to avoid ambiguous daily/midnight trip pairing.",
            UserWarning,
            stacklevel=2,
        )
    updates = updates.loc[~missing_starts].copy()
    if updates.empty:
        raise ValueError("No trip updates have reliable trip-start identifiers.")
    sequences = pd.to_numeric(updates["stop_sequence"], errors="raise")
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("Stop sequences must be nonnegative integers.")
    updates["stop_sequence"] = sequences.astype("int64")
    visits = get_latest_stop_delays(updates, df_stops, route_id=route_id)
    visits["arrival_time"] = pd.to_datetime(
        visits["arrival_time"], utc=True, errors="raise"
    )
    lookup = lookup.rename(columns={"current_stop_sequence": "stop_sequence"})
    matched = visits.merge(
        lookup, on=_VISIT_KEYS, how="left", validate="many_to_one", indicator=True
    )
    visits = matched.drop(columns="_merge")
    visits["exclusion_reason"] = "included"
    visits.loc[visits["arrival_time"].gt(observation_end), "exclusion_reason"] = (
        "unfinished_visit"
    )
    visits.loc[visits["arrival_time"].isna(), "exclusion_reason"] = (
        "missing_arrival_time"
    )
    visits.loc[visits["arrival_delay"].isna(), "exclusion_reason"] = (
        "missing_latest_delay"
    )
    visits.loc[matched["_merge"].eq("left_only"), "exclusion_reason"] = (
        "unmatched_static_visit"
    )
    visits["available_at"] = visits[["available_at", "arrival_time"]].max(axis=1)

    scheduled = lookup.sort_values(["trip_id", "stop_sequence"]).copy()
    previous = scheduled.groupby("trip_id", sort=False)[
        ["stop_id", "stop_sequence"]
    ].shift()
    scheduled["from_stop_id"] = previous["stop_id"]
    scheduled["from_stop_sequence"] = previous["stop_sequence"]
    scheduled = scheduled.loc[scheduled["segment_id"].notna()].rename(
        columns={"stop_id": "to_stop_id", "stop_sequence": "to_stop_sequence"}
    )
    instances = visits[
        ["trip_id", "trip_instance_key", "trip_start_timestamp"]
    ].drop_duplicates(["trip_id", "trip_instance_key"])
    candidates = instances.merge(scheduled, on="trip_id", validate="many_to_many")
    for prefix in ("from", "to"):
        endpoints = visits[["trip_instance_key", *_ENDPOINT_FIELDS]].rename(
            columns={column: f"{prefix}_{column}" for column in _ENDPOINT_FIELDS}
        )
        candidates = candidates.merge(
            endpoints,
            on=["trip_instance_key", f"{prefix}_stop_id", f"{prefix}_stop_sequence"],
            how="left",
            validate="many_to_one",
        )
    candidates = candidates.merge(
        segments[["segment_id", "shape_id"]], on="segment_id", validate="many_to_one"
    )
    candidates["segment_id"] = candidates["segment_id"].astype("int64")
    candidates["exclusion_reason"] = "included"
    for prefix in ("from", "to"):
        reason = candidates[f"{prefix}_exclusion_reason"].fillna("missing_stop_visit")
        mask = candidates["exclusion_reason"].eq("included") & reason.ne("included")
        candidates.loc[mask, "exclusion_reason"] = prefix + "_" + reason.loc[mask]
    reversed_times = candidates["to_arrival_time"].le(candidates["from_arrival_time"])
    candidates.loc[
        candidates["exclusion_reason"].eq("included") & reversed_times,
        "exclusion_reason",
    ] = "nonincreasing_arrival_times"
    included = candidates["exclusion_reason"].eq("included")
    candidates["delta_delay_seconds"] = (
        candidates["to_arrival_delay"] - candidates["from_arrival_delay"]
    ).where(included)
    candidates["crossed_at"] = candidates["to_arrival_time"].where(included)
    candidates["available_at"] = (
        candidates[["from_available_at", "to_available_at"]].max(axis=1).where(included)
    )
    candidates["time_bin"] = candidates["crossed_at"].dt.floor(time_bin_size)
    for stage, frame in (("visits", visits), ("traversals", candidates)):
        counts = frame["exclusion_reason"].value_counts()
        audit_rows.extend(
            {"stage": stage, "reason": reason, "count": int(count)}
            for reason, count in counts.items()
        )
        excluded_count = int(frame["exclusion_reason"].ne("included").sum())
        if excluded_count:
            warnings.warn(
                f"Excluded {excluded_count} {stage}; inspect the result audit "
                "and exclusion_reason columns for missing or mismatched endpoints.",
                UserWarning,
                stacklevel=2,
            )
    usable = candidates.loc[included].copy()
    if usable.empty:
        raise ValueError(
            "No completed adjacent stop pairs have usable latest delay proxies. "
            "Check trip starts, endpoint coverage, and the matching static GTFS."
        )
    usable["adds_delay"] = usable["delta_delay_seconds"].gt(0)
    usable["recovers_delay"] = usable["delta_delay_seconds"].lt(0)
    usable["unchanged_delay"] = usable["delta_delay_seconds"].eq(0)
    congestion = usable.groupby([*_SEGMENT_KEYS, "time_bin"], as_index=False).agg(
        median_delta_delay_seconds=("delta_delay_seconds", "median"),
        traversal_count=("delta_delay_seconds", "size"),
        delta_delay_std_seconds=("delta_delay_seconds", "std"),
        min_delta_delay_seconds=("delta_delay_seconds", "min"),
        max_delta_delay_seconds=("delta_delay_seconds", "max"),
        added_delay_share=("adds_delay", "mean"),
        recovered_delay_share=("recovers_delay", "mean"),
        unchanged_delay_share=("unchanged_delay", "mean"),
        observed_at=("crossed_at", "max"),
        available_at=("available_at", "max"),
    )
    return DelayCongestionResult(
        route_id,
        reference,
        geometry,
        segments,
        visits,
        candidates.sort_values(["trip_instance_key", "to_stop_sequence"]).reset_index(
            drop=True
        ),
        congestion,
        pd.DataFrame(audit_rows),
        observation_end,
    )
