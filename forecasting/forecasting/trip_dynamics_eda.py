"""Prepare full-journey delay evolution and independent speed-probe context."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from numbers import Real

import numpy as np
import pandas as pd

from .road_segment_congestion import _normalize_direction_ids, _require_columns
from .stop_dynamics_eda import _segment_catalogue
from .trip_updates import get_latest_stop_delays

_VISIT_KEYS = ["trip_id", "trip_direction_id", "stop_id", "stop_sequence"]


@dataclass(frozen=True)
class TripDynamicsEDA:
    """Prepared dated journeys, including missing scheduled observations.

    Attributes
    ----------
    journeys : pandas.DataFrame
        One row per journey with at least one usable final arrival delay,
        sorted by maximum signed delay descending. Includes coverage,
        shape/trip identity, and vehicle-identity support.
    visits : pandas.DataFrame
        All scheduled stops for matched dated journeys, in travel order.
        Unusable ``arrival_delay`` and ``delta_delay_seconds`` remain missing.
        Speeds are in km/h; arrival timestamps are UTC. Reported endpoint
        values and exclusion reasons are retained for audit.
    audit : pandas.DataFrame
        Counts by preparation stage and reason.
    """

    journeys: pd.DataFrame
    visits: pd.DataFrame
    audit: pd.DataFrame


def prepare_trip_dynamics_eda(
    vehicle_updates: pd.DataFrame,
    trip_updates: pd.DataFrame,
    stops: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    *,
    route_id: str = "97-KEL",
    trip_direction_id: int = 0,
) -> TripDynamicsEDA:
    """Prepare maximum-delay journeys and their complete scheduled stop axes.

    Latest-row selection precedes measurement validation. Missing starts are
    excluded, never approximated by publication date. Adjacent delay changes
    use each trip's own scheduled predecessor, without bridging missing stops.
    Incomplete journeys remain eligible, but their observed maxima can understate
    the fully observed maxima. No inputs are modified.

    Parameters
    ----------
    vehicle_updates : pandas.DataFrame
        Vehicle probes with route, direction, trip/start, stop/sequence,
        publication time, ``position_speed`` in m/s, and ``vehicle_id``.
    trip_updates : pandas.DataFrame
        Stop predictions with route, direction, trip/start, stop/sequence,
        publication time, arrival time, and arrival delay in seconds.
    stops, stop_times, trips : pandas.DataFrame
        Matching static GTFS tables. Geometry is not required.
    route_id : str, optional
        Selected GTFS route.
    trip_direction_id : int, optional
        Selected direction, 0 or 1.

    Returns
    -------
    TripDynamicsEDA
        Sorted journey maxima, scheduled observations, and exclusion counts.
        Speed compares each journey's mean incoming-segment probes with the
        full-snapshot median individual probe speed for that directed shape.
        Baselines not exceeding 0.5 m/s remain unavailable, as in the engine.
        Missing speed evidence does not remove a delay-selected journey.

    Raises
    ------
    ValueError
        If configuration or inputs are invalid, or no usable delays remain.

    Warns
    -----
    UserWarning
        If evidence is excluded, coverage is incomplete, or vehicle identity
        is ambiguous. Ambiguous journeys retain delays but not a pooled speed
        presented as the speed of a single bus.
    """
    if not isinstance(route_id, str) or not route_id.strip():
        raise ValueError("route_id must be a non-empty string.")
    if (
        isinstance(trip_direction_id, (bool, np.bool_))
        or not isinstance(trip_direction_id, (int, np.integer))
        or trip_direction_id not in (0, 1)
    ):
        raise ValueError("trip_direction_id must be 0 or 1.")
    _, pairs = _segment_catalogue(stops, stop_times, trips, route_id, trip_direction_id)
    route_trips = trips.loc[trips["route_id"].astype("string").eq(route_id)]
    selected_trips = route_trips.loc[
        _normalize_direction_ids(route_trips["direction_id"], "trips").eq(
            trip_direction_id
        ),
        ["trip_id", "shape_id"],
    ].astype("string")
    scheduled = stop_times.loc[
        stop_times["trip_id"].astype("string").isin(selected_trips["trip_id"]),
        ["trip_id", "stop_id", "stop_sequence"],
    ].copy()
    scheduled[["trip_id", "stop_id"]] = scheduled[["trip_id", "stop_id"]].astype(
        "string"
    )
    scheduled["stop_sequence"] = pd.to_numeric(scheduled["stop_sequence"]).astype(
        "int64"
    )
    scheduled = scheduled.merge(selected_trips, on="trip_id", validate="many_to_one")
    scheduled["trip_direction_id"] = trip_direction_id
    incoming = pairs[
        ["trip_id", "to_stop_id", "to_stop_sequence", "segment_id"]
    ].rename(columns={"to_stop_id": "stop_id", "to_stop_sequence": "stop_sequence"})
    scheduled = scheduled.merge(
        incoming,
        on=["trip_id", "stop_id", "stop_sequence"],
        how="left",
        validate="one_to_one",
    )
    names = stops[["stop_id", "stop_name"]].astype({"stop_id": "string"})
    scheduled = scheduled.merge(names, on="stop_id", validate="many_to_one")
    lookup = scheduled[[*_VISIT_KEYS, "segment_id"]]

    common = [
        "trip_route_id",
        "trip_direction_id",
        "trip_id",
        "trip_start_timestamp",
        "stop_id",
        "feed_timestamp",
    ]
    frames = {}
    audit_rows = []
    recording_start = recording_end = pd.NaT
    for stage, source, sequence, measures in (
        ("updates", trip_updates, "stop_sequence", ["arrival_time", "arrival_delay"]),
        (
            "probes",
            vehicle_updates,
            "current_stop_sequence",
            ["position_speed", "vehicle_id"],
        ),
    ):
        required = [*common, sequence, *measures]
        _require_columns(source, tuple(required), stage)
        optional = [
            c
            for c in ("id", "schedule_relationship", "trip_schedule_relationship")
            if c in source and stage == "updates"
        ]
        frame = source.loc[
            source["trip_route_id"].astype("string").eq(route_id),
            [*required, *optional],
        ].copy()
        frame["trip_direction_id"] = _normalize_direction_ids(
            frame["trip_direction_id"], stage
        )
        frame = frame.loc[frame["trip_direction_id"].eq(trip_direction_id)].copy()
        frame = frame.rename(columns={sequence: "stop_sequence"})
        frame[["trip_id", "stop_id"]] = frame[["trip_id", "stop_id"]].astype("string")
        frame["stop_sequence"] = pd.to_numeric(frame["stop_sequence"], errors="raise")
        frame["feed_timestamp"] = pd.to_datetime(
            frame["feed_timestamp"], utc=True, errors="raise"
        )
        if stage == "updates":
            recording_start = frame["feed_timestamp"].min()
            recording_end = frame["feed_timestamp"].max()
        starts = frame["trip_start_timestamp"].astype("string").str.strip()
        frame["trip_start_timestamp"] = starts.replace("", pd.NA)
        frame["trip_instance_key"] = (
            frame["trip_id"] + "|" + frame["trip_start_timestamp"]
        )
        frame = frame.merge(
            lookup, on=_VISIT_KEYS, how="left", validate="many_to_one", indicator=True
        )
        frame["exclusion_reason"] = "included"
        frame.loc[frame["feed_timestamp"].isna(), "exclusion_reason"] = (
            "missing_feed_timestamp"
        )
        frame.loc[frame["_merge"].eq("left_only"), "exclusion_reason"] = (
            "unmatched_static_visit"
        )
        if stage == "updates":
            frame.loc[frame["trip_instance_key"].isna(), "exclusion_reason"] = (
                "missing_trip_start"
            )
        else:
            frame["position_speed"] = pd.to_numeric(
                frame["position_speed"], errors="raise"
            )
            if np.isinf(frame["position_speed"].dropna()).any():
                raise ValueError("position_speed must not contain infinite values.")
            bad = frame["position_speed"].isna() | frame["position_speed"].lt(0)
            frame.loc[bad, "exclusion_reason"] = "missing_or_negative_speed"
            frame.loc[
                frame["_merge"].eq("both") & frame["segment_id"].isna(),
                "exclusion_reason",
            ] = "no_incoming_segment"
        audit_rows.extend(
            {"stage": stage, "reason": reason, "count": int(count)}
            for reason, count in frame["exclusion_reason"].value_counts().items()
        )
        frames[stage] = frame.loc[frame["exclusion_reason"].eq("included")].drop(
            columns=["_merge", "exclusion_reason"]
        )

    updates, probes = frames["updates"], frames["probes"]
    if updates.empty:
        raise ValueError(
            "No reliably dated trip updates match the selected static GTFS."
        )
    instances = updates[
        ["trip_instance_key", "trip_id", "trip_start_timestamp"]
    ].drop_duplicates("trip_instance_key")
    latest = get_latest_stop_delays(updates, stops, route_id=route_id)
    latest["arrival_time"] = pd.to_datetime(
        latest["arrival_time"], utc=True, errors="raise"
    )
    latest["pair_exclusion_reason"] = "included"
    for mask, reason in (
        (latest["arrival_time"].isna(), "missing_arrival_time"),
        (latest["arrival_time"].gt(recording_end), "unfinished_visit"),
        (latest["arrival_delay"].isna(), "missing_latest_delay"),
    ):
        latest.loc[mask, "pair_exclusion_reason"] = reason
    for column, flags in (
        ("schedule_relationship", {1: "skipped_visit", 2: "no_data_visit"}),
        ("trip_schedule_relationship", {3: "cancelled_trip"}),
    ):
        if column in latest:
            values = pd.to_numeric(latest[column], errors="raise")
            for flag, reason in flags.items():
                latest.loc[values.eq(flag), "pair_exclusion_reason"] = reason
    latest = latest.rename(columns={"arrival_delay": "reported_arrival_delay"})
    visits = (
        instances.merge(scheduled, on="trip_id", validate="many_to_many")
        .merge(
            latest[
                [
                    "trip_instance_key",
                    "stop_id",
                    "stop_sequence",
                    "reported_arrival_delay",
                    "arrival_time",
                    "feed_timestamp",
                    "pair_exclusion_reason",
                ]
            ],
            on=["trip_instance_key", "stop_id", "stop_sequence"],
            how="left",
            validate="one_to_one",
        )
        .sort_values(["trip_instance_key", "stop_sequence"], kind="stable")
        .reset_index(drop=True)
    )
    visits["pair_exclusion_reason"] = visits["pair_exclusion_reason"].fillna(
        "missing_stop_visit"
    )
    visits["exclusion_reason"] = visits["pair_exclusion_reason"]
    visits.loc[
        visits["exclusion_reason"].eq("included")
        & visits["arrival_time"].lt(recording_start),
        "exclusion_reason",
    ] = "arrival_before_recording"
    visits["arrival_delay"] = visits["reported_arrival_delay"].where(
        visits["exclusion_reason"].eq("included")
    )
    visits["position"] = visits.groupby("trip_instance_key", sort=False).cumcount()
    previous = visits.groupby("trip_instance_key", sort=False)[
        [
            "reported_arrival_delay",
            "arrival_time",
            "pair_exclusion_reason",
            "stop_id",
            "stop_sequence",
        ]
    ].shift()
    visits["upstream_arrival_delay"] = previous["reported_arrival_delay"]
    visits["upstream_stop_id"] = previous["stop_id"]
    visits["delta_exclusion_reason"] = "included"
    bad_from = previous["pair_exclusion_reason"].fillna("missing_stop_visit")
    visits.loc[bad_from.ne("included"), "delta_exclusion_reason"] = "from_" + bad_from
    bad_to = visits["exclusion_reason"].ne("included")
    visits.loc[bad_to, "delta_exclusion_reason"] = (
        "to_" + visits.loc[bad_to, "exclusion_reason"]
    )
    visits.loc[
        visits["delta_exclusion_reason"].eq("included")
        & previous["arrival_time"].ge(visits["arrival_time"]),
        "delta_exclusion_reason",
    ] = "nonincreasing_arrival_times"
    visits.loc[visits["position"].eq(0), "delta_exclusion_reason"] = (
        "no_incoming_segment"
    )
    visits["delta_delay_seconds"] = (
        visits["reported_arrival_delay"] - previous["reported_arrival_delay"]
    ).where(visits["delta_exclusion_reason"].eq("included"))

    baselines = probes.groupby("segment_id").agg(
        baseline_speed=("position_speed", "median"),
        baseline_probe_count=("position_speed", "size"),
    )
    baselines["baseline_speed_kmh"] = (
        baselines["baseline_speed"].where(baselines["baseline_speed"].gt(0.5)) * 3.6
    )
    audit_rows.append(
        {
            "stage": "baselines",
            "reason": "ineligible_median_probe_speed",
            "count": int(baselines["baseline_speed_kmh"].isna().sum()),
        }
    )
    missing_starts = probes["trip_instance_key"].isna()
    audit_rows.append(
        {
            "stage": "probe_identity",
            "reason": "missing_trip_start",
            "count": int(missing_starts.sum()),
        }
    )
    identified = probes.loc[~missing_starts].copy()
    identified["vehicle_id"] = (
        identified["vehicle_id"].astype("string").str.strip().replace("", pd.NA)
    )
    vehicle_identity = identified.groupby("trip_instance_key").agg(
        vehicle_count=("vehicle_id", "nunique"), vehicle_id=("vehicle_id", "first")
    )
    vehicle_identity["vehicle_id"] = vehicle_identity["vehicle_id"].where(
        vehicle_identity["vehicle_count"].eq(1)
    )
    speeds = identified.groupby(["trip_instance_key", "stop_sequence"]).agg(
        observed_speed_kmh=("position_speed", "mean"),
        probe_count=("position_speed", "size"),
        first_probe_time=("feed_timestamp", "min"),
        last_probe_time=("feed_timestamp", "max"),
    )
    speeds["observed_speed_kmh"] *= 3.6
    ambiguous = vehicle_identity.index[vehicle_identity["vehicle_count"].gt(1)]
    speeds.loc[
        speeds.index.get_level_values("trip_instance_key").isin(ambiguous),
        "observed_speed_kmh",
    ] = np.nan
    visits = visits.merge(
        speeds,
        on=["trip_instance_key", "stop_sequence"],
        how="left",
        validate="one_to_one",
    ).merge(
        baselines[["baseline_speed_kmh", "baseline_probe_count"]],
        on="segment_id",
        how="left",
        validate="many_to_one",
    )
    for column in ("probe_count", "baseline_probe_count"):
        visits[column] = visits[column].fillna(0).astype("int64")
    journeys = visits.groupby("trip_instance_key", sort=False).agg(
        trip_id=("trip_id", "first"),
        trip_start_timestamp=("trip_start_timestamp", "first"),
        shape_id=("shape_id", "first"),
        trip_direction_id=("trip_direction_id", "first"),
        first_arrival_time=("arrival_time", "min"),
        max_delay_minutes=("arrival_delay", "max"),
        usable_stop_count=("arrival_delay", "count"),
        scheduled_stop_count=("stop_sequence", "size"),
    )
    journeys["max_delay_minutes"] /= 60
    journeys["stop_coverage"] = (
        journeys["usable_stop_count"] / journeys["scheduled_stop_count"]
    )
    journeys["incomplete"] = journeys["usable_stop_count"].lt(
        journeys["scheduled_stop_count"]
    )
    journeys = journeys.join(vehicle_identity)
    journeys["vehicle_count"] = journeys["vehicle_count"].fillna(0).astype("int64")
    journeys["vehicle_identity_status"] = np.select(
        [journeys["vehicle_count"].eq(0), journeys["vehicle_count"].gt(1)],
        ["unavailable", "ambiguous"],
        default="unique",
    )
    audit_rows.extend(
        [
            {
                "stage": "journeys",
                "reason": "no_usable_delay",
                "count": int(journeys["max_delay_minutes"].isna().sum()),
            },
            {
                "stage": "journeys",
                "reason": "ambiguous_vehicle_identity",
                "count": int(journeys["vehicle_count"].gt(1).sum()),
            },
            {
                "stage": "journeys",
                "reason": "unavailable_vehicle_identity",
                "count": int(journeys["vehicle_count"].eq(0).sum()),
            },
        ]
    )
    journeys = journeys.loc[journeys["max_delay_minutes"].notna()].reset_index()
    journeys = journeys.sort_values(
        ["max_delay_minutes", "trip_instance_key"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    if journeys.empty:
        raise ValueError(
            "No usable final arrival delays remain; inspect static matching and visit validity."
        )
    for stage, column in (
        ("visits", "exclusion_reason"),
        ("delay_changes", "delta_exclusion_reason"),
    ):
        audit_rows.extend(
            {"stage": stage, "reason": reason, "count": int(count)}
            for reason, count in visits[column].value_counts().items()
        )
    audit = pd.DataFrame(audit_rows)
    if (
        journeys["incomplete"].any()
        or audit.loc[
            ~audit["reason"].isin(["included", "no_incoming_segment"]), "count"
        ]
        .gt(0)
        .any()
        or (visits["segment_id"].notna() & visits["probe_count"].eq(0)).any()
    ):
        warnings.warn(
            "Journey evidence is incomplete or excluded; inspect audit, stop coverage, "
            "and vehicle identity. Observed maxima may understate full-journey maxima; "
            "missing speed evidence is not uncongested traffic.",
            UserWarning,
            stacklevel=2,
        )
    return TripDynamicsEDA(journeys=journeys, visits=visits, audit=audit)


def select_trip_dynamics_examples(
    journeys: pd.DataFrame,
    *,
    target_max_delay_minutes: float | None = None,
    max_delay_quantile: float = 0.90,
    k: int = 5,
    reference_trip_instance_key: str | None = None,
) -> pd.DataFrame:
    """Select journeys nearest a target maximum signed delay.

    Parameters
    ----------
    journeys : pandas.DataFrame
        Prepared catalogue, with unique ``trip_instance_key``, finite
        ``max_delay_minutes``, and ``stop_coverage``.
    target_max_delay_minutes : float or None, optional
        Explicit finite target in minutes. None resolves the target from
        ``max_delay_quantile`` using this catalogue.
    max_delay_quantile : float, optional
        Quantile in [0, 1], default 0.90; ignored for an explicit target.
    k : int, optional
        Positive maximum number of examples, default 5.
    reference_trip_instance_key : str or None, optional
        Full dated journey key from the catalogue. When supplied, its maximum
        delay overrides both target controls. Return this journey first,
        followed by the k - 1 other journeys closest to its maximum delay.
        None preserves maximum-delay/quantile selection.

    Returns
    -------
    pandas.DataFrame
        Copies of the closest rows with ``target_max_delay_minutes`` and
        ``absolute_difference_minutes``. There is no tolerance cutoff.
        Other than the guaranteed first reference journey, ties favor higher
        stop coverage, then lexical trip-instance order.

    Raises
    ------
    ValueError
        If controls or the catalogue are invalid, the catalogue is empty,
        or the reference key does not identify an eligible journey.

    Warns
    -----
    UserWarning
        If fewer than k candidate journeys exist.
    """
    _require_columns(
        journeys,
        ("trip_instance_key", "max_delay_minutes", "stop_coverage"),
        "journeys",
    )
    if journeys.empty:
        raise ValueError("The journey catalogue is empty.")
    if (
        journeys["trip_instance_key"].isna().any()
        or journeys["trip_instance_key"].duplicated().any()
    ):
        raise ValueError("Journey keys must be unique and nonmissing.")
    values = journeys[["max_delay_minutes", "stop_coverage"]]
    if not all(pd.api.types.is_numeric_dtype(values[c]) for c in values):
        raise ValueError("Journey maxima and coverage must be numeric.")
    if (
        not np.isfinite(values.to_numpy(dtype=float)).all()
        or not journeys["stop_coverage"].between(0, 1).all()
    ):
        raise ValueError(
            "Journey maxima must be finite and coverage must be within [0, 1]."
        )
    if (
        isinstance(k, (bool, np.bool_))
        or not isinstance(k, (int, np.integer))
        or k <= 0
    ):
        raise ValueError("k must be a positive integer.")
    if reference_trip_instance_key is not None:
        if (
            not isinstance(reference_trip_instance_key, str)
            or not reference_trip_instance_key.strip()
        ):
            raise ValueError(
                "reference_trip_instance_key must be a non-empty string or None."
            )
        reference = journeys.loc[
            journeys["trip_instance_key"].eq(reference_trip_instance_key)
        ]
        if reference.empty:
            raise ValueError(
                f"Unknown reference_trip_instance_key {reference_trip_instance_key!r}. "
                "Choose a full dated trip_instance_key from the journey catalogue."
            )
        target = float(reference["max_delay_minutes"].iloc[0])
    elif target_max_delay_minutes is None:
        if (
            isinstance(max_delay_quantile, (bool, np.bool_))
            or not isinstance(max_delay_quantile, Real)
            or not np.isfinite(max_delay_quantile)
            or not 0 <= max_delay_quantile <= 1
        ):
            raise ValueError("max_delay_quantile must be finite and within [0, 1].")
        target = float(journeys["max_delay_minutes"].quantile(max_delay_quantile))
    else:
        if (
            isinstance(target_max_delay_minutes, (bool, np.bool_))
            or not isinstance(target_max_delay_minutes, Real)
            or not np.isfinite(target_max_delay_minutes)
        ):
            raise ValueError("target_max_delay_minutes must be finite.")
        target = float(target_max_delay_minutes)
    ranked = journeys.assign(
        target_max_delay_minutes=target,
        absolute_difference_minutes=(journeys["max_delay_minutes"] - target).abs(),
    ).sort_values(
        ["absolute_difference_minutes", "stop_coverage", "trip_instance_key"],
        ascending=[True, False, True],
        kind="stable",
    )
    if reference_trip_instance_key is not None:
        is_reference = ranked["trip_instance_key"].eq(reference_trip_instance_key)
        ranked = pd.concat([ranked.loc[is_reference], ranked.loc[~is_reference]])
    if k > len(ranked):
        warnings.warn(
            f"Requested {k} journeys, but only {len(ranked)} candidates are available.",
            UserWarning,
            stacklevel=2,
        )
    return ranked.head(k).reset_index(drop=True)
