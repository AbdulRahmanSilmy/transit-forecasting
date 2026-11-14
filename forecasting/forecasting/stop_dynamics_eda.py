"""Prepare a single incoming segment for retrospective stop-delay EDA."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .road_segment_congestion import _normalize_direction_ids, _require_columns
from .trip_updates import get_latest_stop_delays

_SEGMENT_KEYS = ["trip_direction_id", "shape_id", "from_stop_id", "to_stop_id"]
_VISIT_KEYS = ["trip_id", "trip_direction_id", "stop_id", "stop_sequence"]


@dataclass(frozen=True)
class StopDynamicsEDA:
    """Auditable observations for one directed incoming segment.

    Attributes
    ----------
    segment : pandas.Series
        Selected engine-compatible segment ID, direction, shape, and stop names.
        IDs are local to the route, direction selection, and static snapshot.
    speed_bins : pandas.DataFrame
        Complete UTC bin index with mean ``observed_speed`` and full-period
        median ``baseline_speed`` in m/s, plus ``probe_count`` and ``bus_count``.
    stop_visits : pandas.DataFrame
        Latest current-stop rows, including excluded visits and their reasons.
        ``arrival_delay`` is in seconds and ``arrival_time`` is UTC.
    upstream_visits : pandas.DataFrame
        Latest preceding-stop rows, independent of current-stop availability.
        Delays and bins use each upstream visit's own reported arrival time.
        Excluded standalone visits retain explicit reasons.
    delay_changes : pandas.DataFrame
        Current visits joined to their own scheduled predecessor. Signed
        ``delta_delay_seconds`` is missing for excluded pairs.
    delay_bins : pandas.DataFrame
        Complete UTC bin index with median current/upstream delays and median
        spatial delay change in seconds, and their observation counts.
    audit : pandas.DataFrame
        Counts by stage and mutually exclusive exclusion reason within a stage.
    coverage : pandas.DataFrame
        Daily UTC candidate/matched update and usable visit/pair counts.
        Raw publication counts and final arrival counts have different clocks.
    recording_start, recording_end : pandas.Timestamp
        First publication in either feed and last trip-update publication (UTC).
        The latter bounds completed stop events, independently of vehicle probes.
    time_bin_size : str
        Fixed UTC bin duration used by the preparation.
    """

    segment: pd.Series
    speed_bins: pd.DataFrame
    stop_visits: pd.DataFrame
    upstream_visits: pd.DataFrame
    delay_changes: pd.DataFrame
    delay_bins: pd.DataFrame
    audit: pd.DataFrame
    coverage: pd.DataFrame
    recording_start: pd.Timestamp
    recording_end: pd.Timestamp
    time_bin_size: str


@dataclass(frozen=True)
class SegmentTimingEDA:
    """Non-overlapping incoming traversal and upstream-stop dwell proxies.

    Attributes
    ----------
    segment : pandas.Series
        Directed segment metadata. Full-cache preparation retains the catalogue
        shape as ``reference_shape_id`` only; historical shape is unconfirmed.
    observations : pandas.DataFrame
        Latest destination visits and their exact upstream endpoints (UTC).
        ``traversal_seconds`` spans upstream departure to destination arrival;
        ``dwell_seconds`` spans upstream arrival to upstream departure.
        They have independent exclusion reasons; unavailable durations remain
        missing. Both use destination arrival as their plotting/binning anchor.
        When provided, ``upstream_feed_timestamp`` retains the upstream final
        publication for train-cutoff availability checks alongside the
        destination ``feed_timestamp``.
    bins : pandas.DataFrame
        Complete UTC grid with duration medians, independent observation counts,
        and full-snapshot baselines in seconds. Baselines weight each valid
        visit once, not each publication or time-bin median.
    audit : pandas.DataFrame
        Counts by metric and mutually exclusive exclusion reason.
    """

    segment: pd.Series
    observations: pd.DataFrame
    bins: pd.DataFrame
    audit: pd.DataFrame


def _segment_catalogue(
    stops: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    route_id: str,
    direction_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enumerate engine-compatible stop pairs without constructing geometry.

    Parameters
    ----------
    stops, stop_times, trips : pandas.DataFrame
        Static GTFS tables.
    route_id : str
        Selected route.
    direction_id : int
        Selected direction.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        Segment catalogue and exact per-trip adjacent visit keys.
    """
    for frame, columns, name in (
        (stops, ("stop_id", "stop_name"), "stops"),
        (stop_times, ("trip_id", "stop_id", "stop_sequence"), "stop_times"),
        (trips, ("route_id", "trip_id", "direction_id", "shape_id"), "trips"),
    ):
        _require_columns(frame, columns, name)
    selected_trips = trips.loc[trips["route_id"].astype("string").eq(route_id)].copy()
    selected_trips["trip_direction_id"] = _normalize_direction_ids(
        selected_trips["direction_id"], "trips"
    )
    if selected_trips["trip_direction_id"].isna().any():
        raise ValueError("Selected GTFS route contains missing directions.")
    selected_trips = selected_trips.loc[
        selected_trips["trip_direction_id"].eq(direction_id)
    ].copy()
    if selected_trips.empty:
        raise ValueError("No GTFS trips match the selected route and direction.")
    for column in ("trip_id", "shape_id"):
        selected_trips[column] = selected_trips[column].astype("string")
        if (
            selected_trips[column].isna().any()
            or selected_trips[column].str.strip().eq("").any()
        ):
            raise ValueError(f"Selected GTFS trips contain missing {column}.")
    if selected_trips["trip_id"].duplicated().any():
        raise ValueError("Selected GTFS trips contain duplicate trip IDs.")
    visits = stop_times.loc[
        stop_times["trip_id"].astype("string").isin(selected_trips["trip_id"]),
        ["trip_id", "stop_id", "stop_sequence"],
    ].copy()
    visits["trip_id"] = visits["trip_id"].astype("string")
    visits["stop_id"] = visits["stop_id"].astype("string")
    visits["stop_sequence"] = pd.to_numeric(visits["stop_sequence"], errors="raise")
    sequences = visits["stop_sequence"]
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("GTFS stop sequences must be nonnegative integers.")
    if visits["stop_id"].isna().any():
        raise ValueError("Selected GTFS visits contain missing stop IDs.")
    visits["stop_sequence"] = sequences.astype("int64")
    if visits.duplicated(["trip_id", "stop_sequence"]).any():
        raise ValueError("GTFS stop times contain duplicate trip/stop sequences.")
    counts = visits.groupby("trip_id").size()
    if not selected_trips["trip_id"].isin(counts.index).all():
        raise ValueError("Selected GTFS trips are missing stop times.")

    # Match the geometry engine's stable trip-length ordering before deduplication.
    ordered_trips = counts.sort_values(ascending=False, kind="stable").index
    rank = pd.Series(range(len(ordered_trips)), index=ordered_trips)
    visits["trip_order"] = visits["trip_id"].map(rank)
    visits = visits.sort_values(["trip_order", "stop_sequence"], kind="stable")
    visits = visits.merge(
        selected_trips[["trip_id", "trip_direction_id", "shape_id"]],
        on="trip_id",
        validate="many_to_one",
    )
    previous = visits.groupby("trip_id", sort=False)[
        ["stop_id", "stop_sequence"]
    ].shift()
    visits["from_stop_id"] = previous["stop_id"]
    visits["from_stop_sequence"] = previous["stop_sequence"]
    pairs = visits.loc[visits["from_stop_id"].notna()].rename(
        columns={"stop_id": "to_stop_id", "stop_sequence": "to_stop_sequence"}
    )
    catalogue = pairs.drop_duplicates(_SEGMENT_KEYS)[
        [*_SEGMENT_KEYS, "from_stop_sequence", "to_stop_sequence"]
    ].reset_index(drop=True)
    catalogue["segment_id"] = np.arange(len(catalogue))
    names = stops[["stop_id", "stop_name"]].copy()
    names["stop_id"] = names["stop_id"].astype("string")
    if names["stop_id"].isna().any() or names["stop_id"].duplicated().any():
        raise ValueError("GTFS stops must have unique, nonmissing stop IDs.")
    names = names.set_index("stop_id")["stop_name"]
    for prefix in ("from", "to"):
        catalogue[f"{prefix}_stop_name"] = catalogue[f"{prefix}_stop_id"].map(names)
        if catalogue[f"{prefix}_stop_name"].isna().any():
            raise ValueError("Selected GTFS segments contain missing stop metadata.")
    pairs = pairs.merge(
        catalogue[[*_SEGMENT_KEYS, "segment_id"]],
        on=_SEGMENT_KEYS,
        validate="many_to_one",
    )
    return catalogue, pairs


def _daily_counts(frame: pd.DataFrame, clock: str) -> pd.Series:
    """Count observations by UTC day.

    Parameters
    ----------
    frame : pandas.DataFrame
        Observations with a UTC datetime column.
    clock : str
        Timestamp column.

    Returns
    -------
    pandas.Series
        Counts indexed by UTC date.
    """
    return frame[clock].dt.floor("D").value_counts(sort=False).sort_index()


def prepare_stop_dynamics_eda(
    vehicle_updates: pd.DataFrame,
    trip_updates: pd.DataFrame,
    stops: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    *,
    route_id: str = "97-KEL",
    trip_direction_id: int = 0,
    segment_id: int = 4,
    time_bin_size: str = "5min",
) -> StopDynamicsEDA:
    """Prepare selected-segment probe speeds and adjacent arrival-delay proxies.

    Segment numbering matches the congestion engine for the same static GTFS,
    route and single-direction selection, without building shapes or geometry.
    Only selected-stop updates are copied; only exact endpoint matches with
    reliable trip starts are sorted for latest-row selection. Inputs are not
    modified. This is retrospective EDA, not a point-in-time backtest.

    Latest delay rows are selected before checking missing estimates or skipped
    visits. Arrival-to-arrival delay change includes dwell, holding and slack.
    Speed bins use vehicle publication times; current and preceding delay bins
    use their respective final reported arrivals. Missing measurements remain
    gaps, never zero values.

    Parameters
    ----------
    vehicle_updates : pandas.DataFrame
        Vehicle probes with route/direction, trip/stop identifiers, feed time,
        current_stop_sequence, position_speed in m/s, and vehicle_id.
    trip_updates : pandas.DataFrame
        Stop predictions with route/direction, trip/start/stop/sequence, feed
        time, arrival_time, and arrival_delay in seconds. Optional stop-level
        schedule_relationship flags 1 (skipped) and 2 (no data) exclude visits.
        Optional departure_time is retained from the same latest row for
        ``prepare_segment_timing_eda``; it is not required for delay analysis.
    stops, stop_times, trips : pandas.DataFrame
        Snapshot static GTFS. Stop coordinates and shapes are not required.
    route_id : str, optional
        GTFS route identifier.
    trip_direction_id : int, optional
        One direction, 0 or 1.
    segment_id : int, optional
        Engine-compatible segment ID in this route/direction's catalogue.
    time_bin_size : str, optional
        Positive fixed UTC bin duration, default five minutes.

    Returns
    -------
    StopDynamicsEDA
        Observations, complete bin tables, and coverage/exclusion diagnostics.

    Raises
    ------
    ValueError
        For invalid inputs or selection, no valid speed probes, or a median
        probe baseline not exceeding the engine's 0.5 m/s eligibility threshold.

    Warns
    -----
    UserWarning
        When observations are excluded, or no usable current visits/pairs exist.
    """
    if not isinstance(route_id, str) or not route_id.strip():
        raise ValueError("route_id must be a non-empty string.")
    if (
        isinstance(trip_direction_id, (bool, np.bool_))
        or not isinstance(trip_direction_id, (int, np.integer))
        or trip_direction_id not in (0, 1)
    ):
        raise ValueError("trip_direction_id must be 0 or 1.")
    if (
        isinstance(segment_id, (bool, np.bool_))
        or not isinstance(segment_id, (int, np.integer))
        or segment_id < 0
    ):
        raise ValueError("segment_id must be a nonnegative integer.")
    if not isinstance(time_bin_size, str):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    duration = pd.Timedelta(time_bin_size)
    if pd.isna(duration) or duration <= pd.Timedelta(0):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    vehicle_columns = (
        "trip_route_id",
        "trip_direction_id",
        "trip_id",
        "stop_id",
        "current_stop_sequence",
        "feed_timestamp",
        "position_speed",
        "vehicle_id",
    )
    update_columns = (
        "trip_route_id",
        "trip_direction_id",
        "trip_id",
        "stop_id",
        "stop_sequence",
        "trip_start_timestamp",
        "feed_timestamp",
        "arrival_time",
        "arrival_delay",
    )
    _require_columns(vehicle_updates, vehicle_columns, "vehicle_updates")
    _require_columns(trip_updates, update_columns, "trip_updates")
    catalogue, schedules = _segment_catalogue(
        stops, stop_times, trips, route_id, trip_direction_id
    )
    if segment_id not in catalogue["segment_id"].values:
        raise ValueError(
            f"No segment {segment_id} for route {route_id!r}, direction "
            f"{trip_direction_id}. Available IDs: {catalogue['segment_id'].tolist()}"
        )
    segment = catalogue.set_index("segment_id", drop=False).loc[segment_id].copy()
    schedules = schedules.loc[schedules["segment_id"].eq(segment_id)].copy()
    endpoint_keys = pd.concat(
        [
            schedules[
                ["trip_id", "trip_direction_id", f"{p}_stop_id", f"{p}_stop_sequence"]
            ].rename(
                columns={
                    f"{p}_stop_id": "stop_id",
                    f"{p}_stop_sequence": "stop_sequence",
                }
            )
            for p in ("from", "to")
        ],
        ignore_index=True,
    ).drop_duplicates(_VISIT_KEYS)
    audit_rows: list[dict[str, str | int]] = []
    coverage_counts: dict[str, pd.Series] = {}
    matched_frames = []
    for stage, source, columns, stop_ids, sequence_column in (
        (
            "probes",
            vehicle_updates,
            vehicle_columns,
            [segment["to_stop_id"]],
            "current_stop_sequence",
        ),
        (
            "updates",
            trip_updates,
            update_columns,
            [segment["from_stop_id"], segment["to_stop_id"]],
            "stop_sequence",
        ),
    ):
        optional = [
            c
            for c in (
                "id",
                "schedule_relationship",
                "trip_schedule_relationship",
                "departure_time",
            )
            if c in source
        ]
        selected = source.loc[
            source["trip_route_id"].astype("string").eq(route_id)
            & source["stop_id"].astype("string").isin(stop_ids),
            [*columns, *optional],
        ].copy()
        selected["trip_direction_id"] = _normalize_direction_ids(
            selected["trip_direction_id"], stage
        )
        selected = selected.loc[
            selected["trip_direction_id"].eq(trip_direction_id)
        ].copy()
        for column in ("trip_id", "stop_id"):
            selected[column] = selected[column].astype("string")
        selected["feed_timestamp"] = pd.to_datetime(
            selected["feed_timestamp"], utc=True, errors="raise"
        )
        selected[sequence_column] = pd.to_numeric(
            selected[sequence_column], errors="raise"
        )
        keys = endpoint_keys
        if stage == "probes":
            keys = keys.loc[keys["stop_id"].eq(segment["to_stop_id"])].rename(
                columns={"stop_sequence": "current_stop_sequence"}
            )
        selected = selected.merge(
            keys,
            on=["trip_id", "trip_direction_id", "stop_id", sequence_column],
            how="left",
            validate="many_to_one",
            indicator=True,
        )
        selected["exclusion_reason"] = "included"
        selected.loc[selected["_merge"].eq("left_only"), "exclusion_reason"] = (
            "unmatched_selected_segment"
        )
        selected.loc[selected["feed_timestamp"].isna(), "exclusion_reason"] = (
            "missing_feed_timestamp"
        )
        if stage == "probes":
            selected["position_speed"] = pd.to_numeric(
                selected["position_speed"], errors="raise"
            )
            if np.isinf(selected["position_speed"].dropna()).any():
                raise ValueError("position_speed must not contain infinite values.")
            bad_speed = selected["position_speed"].isna() | selected[
                "position_speed"
            ].lt(0)
            selected.loc[
                selected["exclusion_reason"].eq("included") & bad_speed,
                "exclusion_reason",
            ] = "missing_or_negative_speed"
        else:
            starts = selected["trip_start_timestamp"].astype("string").str.strip()
            selected.loc[
                selected["exclusion_reason"].eq("included")
                & (starts.isna() | starts.eq("")),
                "exclusion_reason",
            ] = "missing_trip_start"
        included = selected["exclusion_reason"].eq("included")
        coverage_counts[f"candidate_{stage}"] = _daily_counts(
            selected, "feed_timestamp"
        )
        coverage_counts[f"matched_{stage}"] = _daily_counts(
            selected.loc[selected["_merge"].eq("both")], "feed_timestamp"
        )
        audit_rows.extend(
            {"stage": stage, "reason": reason, "count": int(count)}
            for reason, count in selected["exclusion_reason"].value_counts().items()
        )
        matched_frames.append(selected.loc[included].drop(columns="_merge"))
    probes, updates = matched_frames
    if probes.empty:
        raise ValueError(f"No valid vehicle probes match segment {segment_id}.")
    baseline = probes["position_speed"].median()
    if baseline <= 0.5:
        raise ValueError(
            f"Segment {segment_id} median probe baseline is {baseline:g} m/s; "
            "the engine requires a baseline above 0.5 m/s."
        )
    feed_clocks = [
        pd.to_datetime(source["feed_timestamp"], utc=True, errors="raise")
        for source in (vehicle_updates, trip_updates)
    ]
    recording_start = min(clock.min() for clock in feed_clocks if clock.notna().any())
    recording_end = max(clock.max() for clock in feed_clocks if clock.notna().any())
    trip_recording_end = feed_clocks[1].max()
    grid = pd.date_range(
        recording_start.floor(time_bin_size),
        recording_end.floor(time_bin_size),
        freq=time_bin_size,
        name="time_bin",
    )
    probes["time_bin"] = probes["feed_timestamp"].dt.floor(time_bin_size)
    speed_bins = (
        probes.groupby("time_bin")
        .agg(
            observed_speed=("position_speed", "mean"),
            probe_count=("position_speed", "size"),
            bus_count=("vehicle_id", "nunique"),
        )
        .reindex(grid)
    )
    speed_bins["baseline_speed"] = baseline
    speed_bins[["probe_count", "bus_count"]] = (
        speed_bins[["probe_count", "bus_count"]].fillna(0).astype("int64")
    )

    if updates.empty:
        warnings.warn(
            "No reliably matched stop updates; delay rows will be empty.",
            UserWarning,
            stacklevel=2,
        )
        visits = updates.copy()
        visits["trip_instance_key"] = pd.Series(index=visits.index, dtype="string")
    else:
        visits = get_latest_stop_delays(updates, stops, route_id=route_id)
    visits["arrival_time"] = pd.to_datetime(
        visits["arrival_time"], utc=True, errors="raise"
    )
    visits["arrival_delay"] = pd.to_numeric(visits["arrival_delay"], errors="raise")
    if np.isinf(visits["arrival_delay"].dropna()).any():
        raise ValueError("arrival_delay must not contain infinite values.")
    visits["exclusion_reason"] = "included"
    for mask, reason in (
        (visits["arrival_time"].isna(), "missing_arrival_time"),
        (visits["arrival_time"].gt(trip_recording_end), "unfinished_visit"),
        (visits["arrival_delay"].isna(), "missing_latest_delay"),
    ):
        visits.loc[mask, "exclusion_reason"] = reason
    if "schedule_relationship" in visits:
        flags = pd.to_numeric(visits["schedule_relationship"], errors="raise")
        visits.loc[flags.eq(1), "exclusion_reason"] = "skipped_visit"
        visits.loc[flags.eq(2), "exclusion_reason"] = "no_data_visit"
    if "trip_schedule_relationship" in visits:
        flags = pd.to_numeric(visits["trip_schedule_relationship"], errors="raise")
        visits.loc[flags.eq(3), "exclusion_reason"] = "cancelled_trip"
    current = (
        visits.merge(
            schedules[
                [
                    "trip_id",
                    "to_stop_id",
                    "to_stop_sequence",
                    "from_stop_id",
                    "from_stop_sequence",
                ]
            ].rename(
                columns={"to_stop_id": "stop_id", "to_stop_sequence": "stop_sequence"}
            ),
            on=["trip_id", "stop_id", "stop_sequence"],
            validate="many_to_one",
        )
        .sort_values("arrival_time", kind="stable")
        .reset_index(drop=True)
    )
    current.loc[
        current["exclusion_reason"].eq("included")
        & current["arrival_time"].lt(recording_start),
        "exclusion_reason",
    ] = "before_recording_window"
    preceding = (
        visits.merge(
            schedules[["trip_id", "from_stop_id", "from_stop_sequence"]]
            .rename(
                columns={
                    "from_stop_id": "stop_id",
                    "from_stop_sequence": "stop_sequence",
                }
            )
            .drop_duplicates(["trip_id", "stop_id", "stop_sequence"]),
            on=["trip_id", "stop_id", "stop_sequence"],
            validate="many_to_one",
        )
        .sort_values("arrival_time", kind="stable")
        .reset_index(drop=True)
    )
    preceding.loc[
        preceding["exclusion_reason"].eq("included")
        & preceding["arrival_time"].lt(recording_start),
        "exclusion_reason",
    ] = "before_recording_window"
    # Pairing still retains upstream evidence before the standalone plot window.
    upstream = visits[
        [
            "trip_instance_key",
            "stop_id",
            "stop_sequence",
            "arrival_delay",
            "arrival_time",
            "feed_timestamp",
            "exclusion_reason",
        ]
    ].rename(
        columns={
            "stop_id": "from_stop_id",
            "stop_sequence": "from_stop_sequence",
            "arrival_delay": "upstream_arrival_delay",
            "arrival_time": "upstream_arrival_time",
            "feed_timestamp": "upstream_feed_timestamp",
            "exclusion_reason": "upstream_exclusion_reason",
        }
    )
    changes = current.merge(
        upstream,
        on=["trip_instance_key", "from_stop_id", "from_stop_sequence"],
        how="left",
        validate="many_to_one",
    )
    reasons = changes["upstream_exclusion_reason"].fillna("missing_visit")
    changes["exclusion_reason"] = reasons.where(
        reasons.eq("included"), "upstream_" + reasons
    )
    current_invalid = current["exclusion_reason"].ne("included")
    changes.loc[current_invalid, "exclusion_reason"] = (
        "current_" + current.loc[current_invalid, "exclusion_reason"]
    )
    reversed_times = changes["upstream_arrival_time"].ge(changes["arrival_time"])
    changes.loc[
        changes["exclusion_reason"].eq("included") & reversed_times, "exclusion_reason"
    ] = "nonincreasing_arrival_times"
    included_pairs = changes["exclusion_reason"].eq("included")
    changes["delta_delay_seconds"] = (
        changes["arrival_delay"] - changes["upstream_arrival_delay"]
    ).where(included_pairs)
    usable_current = current.loc[current["exclusion_reason"].eq("included")].copy()
    usable_preceding = preceding.loc[
        preceding["exclusion_reason"].eq("included")
    ].copy()
    usable_pairs = changes.loc[included_pairs].copy()
    for frame in (usable_current, usable_preceding, usable_pairs):
        frame["time_bin"] = frame["arrival_time"].dt.floor(time_bin_size)
    delay_bins = pd.concat(
        [
            usable_current.groupby("time_bin").agg(
                median_current_delay_seconds=("arrival_delay", "median"),
                visit_count=("arrival_delay", "size"),
            ),
            usable_pairs.groupby("time_bin").agg(
                median_delta_delay_seconds=("delta_delay_seconds", "median"),
                pair_count=("delta_delay_seconds", "size"),
            ),
            usable_preceding.groupby("time_bin").agg(
                median_upstream_delay_seconds=("arrival_delay", "median"),
                upstream_visit_count=("arrival_delay", "size"),
            ),
        ],
        axis=1,
        sort=True,
    ).reindex(grid)
    count_columns = ["visit_count", "pair_count", "upstream_visit_count"]
    delay_bins[count_columns] = delay_bins[count_columns].fillna(0).astype("int64")
    for stage, frame in (
        ("stop_visits", current),
        ("upstream_visits", preceding),
        ("delay_pairs", changes),
    ):
        audit_rows.extend(
            {"stage": stage, "reason": reason, "count": int(count)}
            for reason, count in frame["exclusion_reason"].value_counts().items()
        )
    coverage_counts["usable_stop_visits"] = _daily_counts(
        usable_current, "arrival_time"
    )
    coverage_counts["usable_delay_pairs"] = _daily_counts(usable_pairs, "arrival_time")
    coverage_counts["usable_upstream_visits"] = _daily_counts(
        usable_preceding, "arrival_time"
    )
    days = pd.date_range(
        recording_start.floor("D"), recording_end.floor("D"), freq="D", name="day_utc"
    )
    coverage = pd.DataFrame(coverage_counts).reindex(days).fillna(0).astype("int64")
    audit = pd.DataFrame(audit_rows, columns=["stage", "reason", "count"])
    if (
        audit["reason"].ne("included").any()
        or usable_current.empty
        or usable_preceding.empty
        or usable_pairs.empty
    ):
        warnings.warn(
            "Some observations were excluded or delay rows have no usable observations; "
            "inspect audit and daily coverage. Final reported delays are proxies.",
            UserWarning,
            stacklevel=2,
        )
    return StopDynamicsEDA(
        segment=segment,
        speed_bins=speed_bins,
        stop_visits=current,
        upstream_visits=preceding,
        delay_changes=changes,
        delay_bins=delay_bins,
        audit=audit,
        coverage=coverage,
        recording_start=recording_start,
        recording_end=trip_recording_end,
        time_bin_size=time_bin_size,
    )


def prepare_segment_timing_eda(analysis: StopDynamicsEDA) -> SegmentTimingEDA:
    """Separate incoming traversal from upstream dwell using final estimates.

    Upstream dwell spans upstream arrival to upstream departure; traversal
    spans that departure to destination arrival. The intervals share only
    their boundary, never elapsed time. Their sum is arrival-to-arrival elapsed
    time. Arrival-delay change also accounts for the scheduled arrival interval,
    so it is not simply the sum of these durations or their median deviations.
    Destination departure is not used.

    Reuse the already matched latest visits, including rows with missing
    arrival delays. Validate timing independently of delay and independently
    for each duration. No older estimate, schedule, or speed replaces a missing
    endpoint. Inputs are not modified.

    Parameters
    ----------
    analysis : StopDynamicsEDA
        Prepared selected segment with optional ``departure_time`` retained
        from trip updates. Exact recording bounds determine completed events.

    Returns
    -------
    SegmentTimingEDA
        Audited per-visit durations, complete UTC bins and full-snapshot medians.
        Positive traversal and nonnegative dwell are eligible; reported zero
        dwell is retained. Final feed estimates are not measured stop events.

    Raises
    ------
    ValueError
        If the input was prepared without departure-time fields or contains
        invalid timestamp/relationship values.

    Warns
    -----
    UserWarning
        If durations are excluded or either metric has no usable observations.
    """
    _require_columns(analysis.upstream_visits, ("departure_time",), "upstream_visits")
    observations = _segment_timing_observations(
        analysis.stop_visits,
        analysis.upstream_visits,
        recording_start=analysis.recording_start,
        recording_end=analysis.recording_end,
    )
    bins, audit = _summarize_segment_timings(
        observations,
        analysis.speed_bins.index,
        analysis.time_bin_size,
    )
    if (
        audit["reason"].ne("included").any()
        or bins["traversal_count"].sum() == 0
        or bins["dwell_count"].sum() == 0
    ):
        warnings.warn(
            "Some segment timings were excluded or have no usable observations; "
            "inspect timing audit. Durations are final feed-estimate proxies.",
            UserWarning,
            stacklevel=2,
        )
    return SegmentTimingEDA(
        segment=analysis.segment.copy(),
        observations=observations,
        bins=bins,
        audit=audit,
    )


def prepare_segment_traversal_eda(
    trip_updates: pd.DataFrame,
    segment: pd.Series,
    *,
    route_id: str = "97-KEL",
    time_bin_size: str = "15min",
) -> SegmentTimingEDA:
    """Prepare full-cache traversal/dwell EDA for an exact directed stop pair.

    Use reliable dated journeys and consecutive numeric realtime sequences,
    without requiring historical trip IDs to match the bundled GTFS. This
    conservative rule does not bridge missing or nonconsecutively numbered
    visits. Reuse latest-row selection and independent timing validation from
    the stop-dynamics EDA. Missing delays, upstream arrivals or inferred
    scheduled allowances do not invalidate otherwise usable traversals.

    Parameters
    ----------
    trip_updates : pandas.DataFrame
        Cached trip reports with route/direction, trip/start identity,
        stop sequence, arrival/departure times, publication times and
        ``arrival_delay`` (which may be missing).
    segment : pandas.Series
        Reference catalogue row with direction, distinct endpoint IDs and names.
    route_id : str, default "97-KEL"
        Route to select.
    time_bin_size : str, default "15min"
        Positive fixed UTC bin duration, anchored by destination arrival.

    Returns
    -------
    SegmentTimingEDA
        All candidate destination visits with independent duration exclusions,
        complete arrival-anchored bins and publication/pair eligibility audit.
        Full-snapshot medians are descriptive, not forecasting baselines.
        Inputs are not modified and historical shape identity is unconfirmed.

    Raises
    ------
    ValueError
        For invalid inputs, conflicting endpoint sequence identities, or no
        usable completed traversal.

    Warns
    -----
    UserWarning
        When endpoint publications or duration pairs are excluded.
    """
    _require_columns(
        trip_updates,
        (
            "trip_route_id",
            "trip_direction_id",
            "trip_id",
            "trip_start_timestamp",
            "stop_id",
            "stop_sequence",
            "arrival_time",
            "departure_time",
            "arrival_delay",
            "feed_timestamp",
        ),
        "trip_updates",
    )
    if not isinstance(route_id, str) or not route_id.strip():
        raise ValueError("route_id must be a nonempty string.")
    if not isinstance(time_bin_size, str):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    duration = pd.Timedelta(time_bin_size)
    if pd.isna(duration) or duration <= pd.Timedelta(0):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    from_id, to_id = str(segment["from_stop_id"]), str(segment["to_stop_id"])
    if from_id == to_id:
        raise ValueError("Traversal EDA requires distinct endpoint stops.")
    direction = _normalize_direction_ids(
        pd.Series([segment["trip_direction_id"]]), "segment"
    ).iloc[0]
    if pd.isna(direction):
        raise ValueError("The segment requires a known direction.")
    route = trip_updates.loc[
        trip_updates["trip_route_id"].astype("string").eq(route_id)
    ].copy()
    directions = _normalize_direction_ids(route["trip_direction_id"], "trip_updates")
    route = route.loc[directions.eq(direction)].copy()
    clocks = pd.to_datetime(route["feed_timestamp"], utc=True, errors="raise")
    if clocks.empty or clocks.isna().any():
        raise ValueError("Selected route/direction requires valid publication times.")
    start, end = clocks.min(), clocks.max()
    selected = route.loc[
        route["stop_id"].astype("string").isin([from_id, to_id])
    ].copy()
    starts = selected["trip_start_timestamp"].astype("string").str.strip()
    reasons = pd.Series("included", index=selected.index, dtype="string")
    reasons.loc[starts.isna() | starts.eq("")] = "missing_trip_start"
    sequence = pd.to_numeric(selected["stop_sequence"], errors="raise")
    invalid = sequence.isna() | sequence.le(0) | sequence.mod(1).ne(0)
    reasons.loc[reasons.eq("included") & invalid] = "invalid_stop_sequence"
    selected["stop_sequence"] = sequence
    publication_audit = (
        reasons.value_counts()
        .rename_axis("reason")
        .reset_index(name="count")
        .assign(metric="endpoint_publications")
    )
    names = pd.DataFrame(
        {
            "stop_id": [from_id, to_id],
            "stop_name": [segment["from_stop_name"], segment["to_stop_name"]],
        }
    )
    latest = get_latest_stop_delays(
        selected.loc[reasons.eq("included")],
        names,
        route_id=route_id,
    )
    if (
        latest.groupby(["trip_instance_key", "stop_sequence"])["stop_id"]
        .nunique()
        .gt(1)
        .any()
    ):
        raise ValueError("Endpoint reports conflict on dated stop-sequence identity.")
    current = latest.loc[latest["stop_id"].eq(to_id)].copy()
    current["from_stop_id"] = from_id
    current["from_stop_sequence"] = current["stop_sequence"] - 1
    observations = _segment_timing_observations(
        current,
        latest.loc[latest["stop_id"].eq(from_id)],
        recording_start=start,
        recording_end=end,
    )
    bin_index = pd.date_range(
        start.floor(time_bin_size),
        end.floor(time_bin_size),
        freq=time_bin_size,
    )
    bins, pair_audit = _summarize_segment_timings(
        observations,
        bin_index,
        time_bin_size,
    )
    audit = pd.concat([publication_audit, pair_audit], ignore_index=True)
    if audit["reason"].ne("included").any():
        warnings.warn(
            "Some full-cache segment timings were excluded; inspect timing audit.",
            UserWarning,
            stacklevel=2,
        )
    if bins["traversal_count"].sum() == 0:
        raise ValueError("No usable completed traversals for the selected stop pair.")
    reference = segment.copy()
    reference["reference_shape_id"] = reference.get("shape_id", pd.NA)
    reference["shape_id"] = pd.NA
    reference["scope"] = "full_cache_directed_stop_pair"
    return SegmentTimingEDA(
        segment=reference,
        observations=observations,
        bins=bins,
        audit=audit,
    )


def _summarize_segment_timings(
    observations: pd.DataFrame,
    bin_index: pd.DatetimeIndex,
    time_bin_size: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize independently eligible durations on a complete arrival grid.

    Parameters
    ----------
    observations : pandas.DataFrame
        Paired visits with duration values and exclusion reasons.
    bin_index : pandas.DatetimeIndex
        Complete UTC grid covering the recording window.
    time_bin_size : str
        Positive fixed bin duration.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        Duration medians, counts and full-snapshot baselines; eligibility audit.
    """
    bins = pd.DataFrame(index=bin_index)
    audit_rows = []
    for metric in ("traversal", "dwell"):
        reasons = observations[f"{metric}_exclusion_reason"]
        usable = observations.loc[reasons.eq("included")].copy()
        usable["time_bin"] = usable["arrival_time"].dt.floor(time_bin_size)
        grouped = usable.groupby("time_bin")[f"{metric}_seconds"]
        bins[f"median_{metric}_seconds"] = grouped.median().reindex(bins.index)
        bins[f"{metric}_count"] = (
            grouped.count().reindex(bins.index, fill_value=0).astype("int64")
        )
        bins[f"baseline_{metric}_seconds"] = usable[f"{metric}_seconds"].median()
        audit_rows.extend(
            {"metric": metric, "reason": reason, "count": int(count)}
            for reason, count in reasons.value_counts().items()
        )
    audit = pd.DataFrame(audit_rows, columns=["metric", "reason", "count"])
    return bins, audit


def _segment_timing_observations(
    current: pd.DataFrame,
    preceding: pd.DataFrame,
    *,
    recording_start: pd.Timestamp,
    recording_end: pd.Timestamp,
) -> pd.DataFrame:
    """Pair final adjacent visits and validate non-overlapping durations.

    Parameters
    ----------
    current : pandas.DataFrame
        Latest destination visits with their scheduled ``from_stop_id`` and
        ``from_stop_sequence``. Arrival-delay validity is not used.
    preceding : pandas.DataFrame
        Latest upstream visits with arrival/departure times and optional
        stop/trip relationship flags.
    recording_start, recording_end : pandas.Timestamp
        UTC bounds for destination arrival anchors and completed events.

    Returns
    -------
    pandas.DataFrame
        Exact dated endpoint pairs with independent traversal/dwell seconds
        and exclusion reasons. Inputs are not modified; missing values stay gaps.
    """
    upstream_columns = [
        "trip_instance_key",
        "stop_id",
        "stop_sequence",
        "arrival_time",
        "departure_time",
    ]
    upstream_columns += [
        c
        for c in (
            "feed_timestamp",
            "schedule_relationship",
            "trip_schedule_relationship",
        )
        if c in preceding
    ]
    upstream = preceding[upstream_columns].rename(
        columns={
            "stop_id": "from_stop_id",
            "stop_sequence": "from_stop_sequence",
            **{
                c: f"upstream_{c}"
                for c in upstream_columns
                if c not in ("trip_instance_key", "stop_id", "stop_sequence")
            },
        }
    )
    observations = current.merge(
        upstream,
        on=["trip_instance_key", "from_stop_id", "from_stop_sequence"],
        how="left",
        validate="many_to_one",
        indicator="upstream_match",
    )
    for column in (
        "arrival_time",
        "upstream_arrival_time",
        "upstream_departure_time",
    ):
        observations[column] = pd.to_datetime(
            observations[column], utc=True, errors="raise"
        )
    arrival = observations["arrival_time"]
    upstream_arrival = observations["upstream_arrival_time"]
    upstream_departure = observations["upstream_departure_time"]
    traversal = (arrival - upstream_departure).dt.total_seconds()
    dwell = (upstream_departure - upstream_arrival).dt.total_seconds()
    common_checks = [
        ("missing_destination_arrival", arrival.isna()),
        ("before_recording_window", arrival.lt(recording_start)),
        ("unfinished_arrival", arrival.gt(recording_end)),
    ]
    common_checks.extend(
        [
            ("missing_upstream_visit", observations["upstream_match"].eq("left_only")),
            ("missing_upstream_departure", upstream_departure.isna()),
        ]
    )
    traversal_checks = [
        (
            "invalid_upstream_chronology",
            upstream_departure.lt(upstream_arrival),
        ),
        ("nonpositive_traversal", traversal.le(0)),
    ]
    for prefix in ("", "upstream_"):
        stop_flag = f"{prefix}schedule_relationship"
        if stop_flag in observations:
            flags = pd.to_numeric(observations[stop_flag], errors="raise")
            common_checks.extend(
                [
                    (f"{prefix}skipped_visit", flags.eq(1)),
                    (f"{prefix}no_data_visit", flags.eq(2)),
                ]
            )
        trip_flag = f"{prefix}trip_schedule_relationship"
        if trip_flag in observations:
            flags = pd.to_numeric(observations[trip_flag], errors="raise")
            common_checks.append((f"{prefix}cancelled_trip", flags.isin([3, 7])))
    dwell_checks = [
        ("missing_upstream_arrival", upstream_arrival.isna()),
        ("negative_dwell", dwell.lt(0)),
        ("unfinished_dwell", upstream_departure.gt(recording_end)),
    ]
    for metric, values, checks in (
        ("traversal", traversal, traversal_checks),
        ("dwell", dwell, dwell_checks),
    ):
        reasons = pd.Series("included", index=observations.index, dtype="string")
        for reason, mask in [*common_checks, *checks]:
            reasons.loc[reasons.eq("included") & mask] = reason
        observations[f"{metric}_exclusion_reason"] = reasons
        observations[f"{metric}_seconds"] = values.where(reasons.eq("included"))
    return observations.drop(columns="upstream_match")
