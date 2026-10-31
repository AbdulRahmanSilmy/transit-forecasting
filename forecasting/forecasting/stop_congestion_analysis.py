"""Descriptive stop-delay comparisons with historically available congestion."""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .road_segment_congestion import CongestionResult, _require_columns


@dataclass(frozen=True)
class StopCongestionAnalysis:
    """Inspectable inputs and comparisons for a stop congestion feasibility study.

    Attributes
    ----------
    paths : pandas.DataFrame
        Each visit's ordered, shape-specific incoming segments through the stop.
    observations : pandas.DataFrame
        Engine bins with receipt/publication availability and probe provenance.
        Engine full-history speed baselines are deliberately unchanged.
    segment_comparisons : pandas.DataFrame
        Latest available bin for each visit, horizon, and upstream segment,
        including position-aware remaining-path membership.
    comparisons : pandas.DataFrame
        One row per visit, horizon, scope, and freshness limit. Means describe
        observed segments only; coverage denominators describe the whole scope.
    audit : pandas.DataFrame
        Visit counts, exclusions, and timestamp quality counts.
    reference_horizons : pandas.DataFrame
        All original visit/horizon rows, including excluded visits, with
        ``path_available`` and ``path_exclusion_reason``. Excluded visits
        retain their delay curves but never receive substituted congestion.
    """

    paths: pd.DataFrame
    observations: pd.DataFrame
    segment_comparisons: pd.DataFrame
    comparisons: pd.DataFrame
    audit: pd.DataFrame
    reference_horizons: pd.DataFrame


def _upstream_paths(
    targets: pd.DataFrame,
    result: CongestionResult,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve each visit's own directed upstream path.

    Parameters
    ----------
    targets : pandas.DataFrame
        Validated visit/horizon rows with stable visit identifiers.
    result : CongestionResult
        Engine segments to match by exact shape and directed stop pair.
    stop_times, trips : pandas.DataFrame
        Selected static GTFS tables.

    Returns
    -------
    tuple of pandas.DataFrame and pandas.DataFrame
        Ordered segment rows and per-visit path exclusion reasons.
    """
    _require_columns(stop_times, ("trip_id", "stop_id", "stop_sequence"), "stop_times")
    _require_columns(
        trips, ("trip_id", "route_id", "direction_id", "shape_id"), "trips"
    )
    trips = trips.copy()
    stop_times = stop_times.copy()
    segments = result.segments.copy()
    for frame, columns in (
        (trips, ("trip_id", "route_id", "shape_id")),
        (stop_times, ("trip_id", "stop_id")),
        (segments, ("shape_id", "from_stop_id", "to_stop_id")),
    ):
        for column in columns:
            frame[column] = frame[column].astype("string")
    trips = trips.loc[
        trips["route_id"].eq(result.route_id)
        & pd.to_numeric(trips["direction_id"], errors="raise").eq(0)
    ]
    if trips["trip_id"].duplicated().any():
        raise ValueError("Selected static GTFS trips must have unique trip IDs.")
    stop_times["stop_sequence"] = pd.to_numeric(
        stop_times["stop_sequence"], errors="raise"
    )
    sequences = stop_times["stop_sequence"]
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("Static GTFS stop sequences must be nonnegative integers.")
    if stop_times.duplicated(["trip_id", "stop_sequence"]).any():
        raise ValueError("Static GTFS trip/stop sequences must be unique.")
    keys = ["shape_id", "from_stop_id", "to_stop_id"]
    segments = segments.loc[segments["trip_direction_id"].eq(0)]
    if segments.duplicated(keys).any():
        raise ValueError("Engine direction-0 segment keys must be unique.")
    rows = []
    exclusions = []
    for visit in targets.drop_duplicates("visit_index").itertuples(index=False):
        trip = trips.loc[trips["trip_id"].eq(visit.trip_id)]
        if trip.empty:
            exclusions.append(
                {
                    "visit_index": visit.visit_index,
                    "path_exclusion_reason": "Trip ID is absent from the selected route/direction's static GTFS.",
                }
            )
            continue
        ordered = stop_times.loc[stop_times["trip_id"].eq(visit.trip_id)].sort_values(
            "stop_sequence"
        )
        endpoint = ordered.loc[ordered["stop_id"].eq(visit.stop_id)]
        if hasattr(visit, "stop_sequence"):
            endpoint = endpoint.loc[endpoint["stop_sequence"].eq(visit.stop_sequence)]
        if len(endpoint) != 1:
            exclusions.append(
                {
                    "visit_index": visit.visit_index,
                    "path_exclusion_reason": "Target stop/sequence is missing or ambiguous in this trip's static stop times.",
                }
            )
            continue
        ordered = ordered.loc[
            ordered["stop_sequence"].le(endpoint.iloc[0]["stop_sequence"])
        ]
        path = pd.DataFrame(
            {
                "from_stop_id": ordered["stop_id"].iloc[:-1].to_numpy(),
                "to_stop_id": ordered["stop_id"].iloc[1:].to_numpy(),
                "path_stop_sequence": ordered["stop_sequence"].iloc[1:].to_numpy(),
            }
        )
        path["shape_id"] = trip.iloc[0]["shape_id"]
        path = path.merge(
            segments[keys + ["segment_id", "from_stop_name", "to_stop_name"]],
            on=keys,
            how="left",
            validate="many_to_one",
        )
        if path.empty:
            exclusions.append(
                {
                    "visit_index": visit.visit_index,
                    "path_exclusion_reason": "The target is the trip origin and has no incoming segment.",
                }
            )
            continue
        if path["segment_id"].isna().any():
            exclusions.append(
                {
                    "visit_index": visit.visit_index,
                    "path_exclusion_reason": "Upstream stop pairs have no exact shape/direction match in the engine.",
                }
            )
            continue
        path["segment_id"] = path["segment_id"].astype("int64")
        path["path_order"] = np.arange(1, len(path) + 1)
        path["visit_index"] = visit.visit_index
        rows.append(path)
    if exclusions:
        warnings.warn(
            f"Excluded {len(exclusions)} visits without an exact direction-0 upstream "
            "path in the selected static GTFS and engine result.",
            UserWarning,
            stacklevel=2,
        )
    if not rows:
        raise ValueError("No visits have an exact direction-0 upstream path.")
    return pd.concat(rows, ignore_index=True), pd.DataFrame(
        exclusions, columns=["visit_index", "path_exclusion_reason"]
    )


def _vehicle_times(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize provenance without receipt or measurement look-ahead.

    Parameters
    ----------
    frame : pandas.DataFrame
        Vehicle records with publication and trip-instance descriptors.

    Returns
    -------
    pandas.DataFrame
        A copy with UTC timestamps, availability, and normalized instance keys.
    """
    frame = frame.copy()
    frame["feed_timestamp"] = pd.to_datetime(frame["feed_timestamp"], utc=True)
    for column in ("read_timestamp", "timestamp"):
        if column not in frame or frame[column].isna().any():
            warnings.warn(
                f"Missing vehicle {column}; feed publication time is used for "
                "those records. Exact receipt/measurement timing is unverified.",
                UserWarning,
                stacklevel=2,
            )
        frame[column] = (
            pd.to_datetime(frame[column], utc=True).fillna(frame["feed_timestamp"])
            if column in frame
            else frame["feed_timestamp"]
        )
    if frame["feed_timestamp"].isna().any():
        raise ValueError("Vehicle records contain missing feed timestamps.")
    frame["available_at"] = frame[
        ["feed_timestamp", "read_timestamp", "timestamp"]
    ].max(axis=1)
    starts = frame["trip_start_timestamp"].astype("string").str.strip()
    frame["_instance"] = (
        frame["trip_id"].astype("string") + "|" + starts.replace("", pd.NA)
    )
    return frame


def _observations(result: CongestionResult, time_bin_size: str) -> pd.DataFrame:
    """Attach availability and trailing descriptors to unchanged engine bins.

    Parameters
    ----------
    result : CongestionResult
        Existing engine output and its contributing vehicle probes.
    time_bin_size : str
        Engine aggregation interval.

    Returns
    -------
    pandas.DataFrame
        Complete bins with availability, measurement age provenance, and
        trailing descriptors computed once per observation.
    """
    probes = _vehicle_times(result.vehicle_probes)
    keys = ["segment_id", "time_bin"]
    provenance = probes.groupby(keys, as_index=False).agg(
        observed_at=("timestamp", "max"),
        available_at=("available_at", "max"),
        last_feed_timestamp=("feed_timestamp", "max"),
        probe_count=("feed_timestamp", "size"),
        bus_count=("vehicle_id", "nunique"),
        probe_instances=("_instance", lambda values: tuple(values.dropna().unique())),
        unknown_probe_instances=("_instance", lambda values: int(values.isna().sum())),
    )
    observations = result.congestion.merge(
        provenance, on=keys, how="left", validate="one_to_one"
    )
    if observations["available_at"].isna().any():
        raise ValueError("Every engine bin must have contributing probe provenance.")
    observations["bin_end"] = pd.to_datetime(
        observations["time_bin"], utc=True
    ) + pd.Timedelta(time_bin_size)
    observations["available_at"] = observations[["available_at", "bin_end"]].max(axis=1)
    histories = []
    for _, history in observations.groupby("segment_id", sort=False):
        history = history.sort_values(["available_at", "time_bin"]).copy()
        history["trailing_mean_congestion"] = (
            history.set_index("available_at")["congestion_index"]
            .rolling("15min", closed="right", min_periods=1)
            .mean()
            .to_numpy()
        )
        previous = pd.merge_asof(
            pd.DataFrame(
                {"prior_cutoff": history["available_at"] - pd.Timedelta("15min")}
            ),
            history[["available_at", "congestion_index"]].rename(
                columns={"congestion_index": "prior_congestion"}
            ),
            left_on="prior_cutoff",
            right_on="available_at",
            direction="backward",
            tolerance=pd.Timedelta("15min"),
        )
        history["congestion_change"] = (
            history["congestion_index"].to_numpy()
            - previous["prior_congestion"].to_numpy()
        )
        histories.append(history)
    if not histories:
        raise ValueError("No congestion bins with eligible engine baselines exist.")
    return pd.concat(histories, ignore_index=True)


def _positions(
    targets: pd.DataFrame,
    vehicles: pd.DataFrame,
    stop_times: pd.DataFrame,
    route_id: str,
    max_age: pd.Timedelta,
) -> pd.DataFrame:
    """Select fresh, status-aware positions for the same trip instance.

    Parameters
    ----------
    targets : pandas.DataFrame
        Visit/horizon rows with normalized instance keys and cutoffs.
    vehicles : pandas.DataFrame
        Original vehicle updates, including stationary/origin positions.
    stop_times : pandas.DataFrame
        Static trip/stop lookup for validating realtime positions.
    route_id : str
        Selected route.
    max_age : pandas.Timedelta
        Inclusive maximum position measurement age.

    Returns
    -------
    pandas.DataFrame
        Targets with the latest known position and explicit freshness flags.
    """
    _require_columns(
        vehicles,
        (
            "trip_route_id",
            "trip_direction_id",
            "trip_id",
            "trip_start_timestamp",
            "stop_id",
            "current_stop_sequence",
            "current_status",
            "feed_timestamp",
        ),
        "vehicle_updates",
    )
    selected = vehicles.loc[
        vehicles["trip_route_id"].astype("string").eq(route_id)
        & pd.to_numeric(vehicles["trip_direction_id"], errors="raise").eq(0)
        & vehicles["trip_id"].astype("string").isin(targets["trip_id"])
    ]
    positions = _vehicle_times(selected)
    positions["trip_id"] = positions["trip_id"].astype("string")
    positions["stop_id"] = positions["stop_id"].astype("string")
    positions["current_stop_sequence"] = pd.to_numeric(
        positions["current_stop_sequence"], errors="raise"
    )
    lookup = stop_times[["trip_id", "stop_id", "stop_sequence"]].copy()
    for column in ("trip_id", "stop_id"):
        lookup[column] = lookup[column].astype("string")
    lookup = lookup.rename(columns={"stop_sequence": "current_stop_sequence"})
    positions = positions.merge(
        lookup,
        on=["trip_id", "stop_id", "current_stop_sequence"],
        how="inner",
        validate="many_to_one",
    )
    status_names = {"INCOMING_AT": 0, "STOPPED_AT": 1, "IN_TRANSIT_TO": 2}
    positions["position_status"] = pd.to_numeric(
        positions["current_status"].replace(status_names), errors="raise"
    )
    order = ["available_at", "feed_timestamp"]
    if "id" in positions:
        order.append("id")
    positions = positions.loc[
        positions["_instance"].notna() & positions["position_status"].isin([0, 1, 2])
    ].sort_values(order, kind="stable")
    # A late receipt of an older publication must not move the bus backwards.
    newest_feed = positions.groupby("_instance")["feed_timestamp"].cummax()
    positions = positions.loc[positions["feed_timestamp"].eq(newest_feed)]
    fields = [
        "_instance",
        "available_at",
        "timestamp",
        "current_stop_sequence",
        "position_status",
    ]
    selected = pd.merge_asof(
        targets.sort_values("cutoff_time"),
        positions[fields].rename(
            columns={
                "available_at": "position_available_at",
                "timestamp": "position_observed_at",
            }
        ),
        left_on="cutoff_time",
        right_on="position_available_at",
        by="_instance",
        direction="backward",
    )
    selected["position_age_minutes"] = (
        selected["cutoff_time"] - selected["position_observed_at"]
    ).dt.total_seconds() / 60
    selected["position_known"] = (
        selected["position_age_minutes"].between(0, max_age.total_seconds() / 60)
        & selected["_instance"].notna()
    )
    return selected


def prepare_stop_congestion_analysis(
    result: CongestionResult,
    delay_horizons: pd.DataFrame,
    vehicle_updates: pd.DataFrame,
    stop_times: pd.DataFrame,
    trips: pd.DataFrame,
    *,
    time_bin_size: str = "5min",
    freshness_minutes: Sequence[int] = (5, 15, 30),
    position_max_age: str = "15min",
) -> StopCongestionAnalysis:
    """Compare stop prediction residuals with available upstream congestion.

    Full-history engine baselines are intentionally retained. This is a
    feasibility comparison, not a fully point-in-time baseline backtest.
    Only complete bins available by the evaluation cutoff are selected.
    A bin's availability is the latest of its end and every contributing
    probe's feed, receipt, and measurement times. No shapes are pooled.

    Parameters
    ----------
    result : CongestionResult
        Existing engine output; baseline speeds and indices are not refitted.
    delay_horizons : pandas.DataFrame
        Direction-0 output of ``get_stop_delay_horizons`` with stable
        ``visit_index`` values, for one platform. Missing trip starts retain
        corridor comparisons but cannot supply a reliable remaining path.
    vehicle_updates : pandas.DataFrame
        Original vehicle updates, including origin and stationary positions.
    stop_times, trips : pandas.DataFrame
        Static GTFS from the same selected backtest snapshot.
    time_bin_size : str, optional
        Engine bin width, default ``"5min"``. Must match the supplied result.
    freshness_minutes : sequence of int, optional
        Positive unique observation-age limits, default 5, 15, and 30 minutes.
    position_max_age : str, optional
        Maximum measurement age for remaining-path positions, default 15 min.

    Returns
    -------
    StopCongestionAnalysis
        Ordered paths, bin provenance, segment comparisons, scoped summaries,
        audit counts, and all reference horizons with path exclusion reasons.
        ``remaining_delay_error_minutes`` is reference
        minus prediction: positive means BC Transit underestimated lateness.
        Trailing descriptors end at the selected bin's availability time.

    Raises
    ------
    ValueError
        If identifiers, directions, duplicate targets, bins, or limits are
        invalid, or no exact upstream paths/congestion observations exist.

    Warns
    -----
    UserWarning
        For excluded static paths, approximate timing, or unknown positions.
    """
    required = (
        "visit_index",
        "trip_id",
        "trip_start_timestamp",
        "stop_id",
        "trip_direction_id",
        "horizon_minutes",
        "cutoff_time",
        "reference_arrival_time",
        "estimated_delay_minutes",
        "true_delay_minutes",
    )
    _require_columns(delay_horizons, required, "delay_horizons")
    if delay_horizons.empty:
        raise ValueError("delay_horizons must contain at least one visit.")
    limits = list(freshness_minutes)
    if (
        not limits
        or len(set(limits)) != len(limits)
        or any(
            isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v <= 0
            for v in limits
        )
    ):
        raise ValueError("freshness_minutes must contain unique positive integers.")
    bin_width = pd.Timedelta(time_bin_size)
    position_age = pd.Timedelta(position_max_age)
    if pd.isna(bin_width) or bin_width <= pd.Timedelta(0):
        raise ValueError("time_bin_size must be a positive fixed duration.")
    if pd.isna(position_age) or position_age <= pd.Timedelta(0):
        raise ValueError("position_max_age must be a positive duration.")
    targets = delay_horizons.copy()
    for column in ("trip_id", "stop_id"):
        targets[column] = targets[column].astype("string")
    if (
        targets[list(required[:4])]
        .drop(columns="trip_start_timestamp")
        .isna()
        .any()
        .any()
    ):
        raise ValueError("Visit, trip, and stop identifiers must not be missing.")
    if not pd.to_numeric(targets["trip_direction_id"], errors="raise").eq(0).all():
        raise ValueError("This analysis requires direction 0 only.")
    if targets["stop_id"].nunique() != 1:
        raise ValueError("Select exactly one Parkinson platform.")
    if targets.duplicated(["visit_index", "horizon_minutes"]).any():
        raise ValueError("Each visit/horizon pair must be unique.")
    identity = [
        "trip_id",
        "trip_start_timestamp",
        "stop_id",
        "reference_arrival_time",
        "true_delay_minutes",
    ]
    if "stop_sequence" in targets:
        identity.append("stop_sequence")
    if targets.groupby("visit_index")[identity].nunique(dropna=False).gt(1).any().any():
        raise ValueError(
            "A visit_index must identify the same stop visit at every horizon."
        )
    horizons = pd.to_numeric(targets["horizon_minutes"], errors="raise")
    if not (horizons.notna() & horizons.gt(0) & horizons.mod(1).eq(0)).all():
        raise ValueError("Look-back horizons must be positive integer minutes.")
    for column in ("cutoff_time", "reference_arrival_time"):
        targets[column] = pd.to_datetime(targets[column], utc=True)
        if targets[column].isna().any():
            raise ValueError(f"{column} contains missing timestamps.")
    expected_cutoff = targets["reference_arrival_time"] - pd.to_timedelta(
        targets["horizon_minutes"], unit="min"
    )
    if not targets["cutoff_time"].eq(expected_cutoff).all():
        raise ValueError("Cutoffs must equal reference arrival minus horizon.")
    if "estimate_feed_timestamp" in targets:
        publication = pd.to_datetime(targets["estimate_feed_timestamp"], utc=True)
        if publication.gt(targets["cutoff_time"]).any():
            raise ValueError("A BC Transit prediction was published after its cutoff.")
    starts = targets["trip_start_timestamp"].astype("string").str.strip()
    targets["_instance"] = targets["trip_id"] + "|" + starts.replace("", pd.NA)
    if targets["_instance"].isna().any():
        warnings.warn(
            "Missing trip starts: remaining paths and target-bus contribution "
            "cannot be established for those visits.",
            UserWarning,
            stacklevel=2,
        )
    targets["remaining_delay_error_minutes"] = (
        targets["true_delay_minutes"] - targets["estimated_delay_minutes"]
    )
    paths, exclusions = _upstream_paths(targets, result, stop_times, trips)
    reference_horizons = targets.drop(columns="_instance").merge(
        exclusions, on="visit_index", how="left", validate="many_to_one"
    )
    reference_horizons["path_available"] = reference_horizons["visit_index"].isin(
        paths["visit_index"]
    )
    reference_horizons["path_exclusion_reason"] = reference_horizons[
        "path_exclusion_reason"
    ].fillna("")
    targets = targets.loc[targets["visit_index"].isin(paths["visit_index"])].copy()
    positions = _positions(
        targets, vehicle_updates, stop_times, result.route_id, position_age
    )
    unknown_positions = positions.loc[
        ~positions["position_known"], ["visit_index", "horizon_minutes"]
    ]
    if not unknown_positions.empty:
        warnings.warn(
            f"{len(unknown_positions)} visit/horizon pairs have no fresh, "
            "same-instance position; remaining-path values stay missing.",
            UserWarning,
            stacklevel=2,
        )
    observations = _observations(result, time_bin_size)
    if not (
        pd.to_datetime(result.vehicle_probes["feed_timestamp"], utc=True)
        .dt.floor(time_bin_size)
        .eq(pd.to_datetime(result.vehicle_probes["time_bin"], utc=True))
    ).all():
        raise ValueError("time_bin_size does not match the engine's probe bins.")
    expanded = positions.merge(paths, on="visit_index", validate="many_to_many")
    observation_columns = [
        "segment_id",
        "time_bin",
        "bin_end",
        "observed_at",
        "available_at",
        "congestion_index",
        "baseline_speed",
        "probe_count",
        "bus_count",
        "probe_instances",
        "unknown_probe_instances",
        "trailing_mean_congestion",
        "congestion_change",
    ]
    joined = pd.merge_asof(
        expanded.sort_values("cutoff_time"),
        observations[observation_columns].sort_values(
            ["available_at", "time_bin", "segment_id"], kind="stable"
        ),
        left_on="cutoff_time",
        right_on="available_at",
        by="segment_id",
        direction="backward",
    )
    joined["observation_age_minutes"] = (
        joined["cutoff_time"] - joined["observed_at"]
    ).dt.total_seconds() / 60
    joined["in_remaining_path"] = joined["position_known"] & (
        joined["path_stop_sequence"].gt(joined["current_stop_sequence"])
        | (
            joined["path_stop_sequence"].eq(joined["current_stop_sequence"])
            & joined["position_status"].isin([0, 2])
        )
    )
    joined["target_bus_contributed"] = [
        (
            instance in members
            if isinstance(members, tuple) and pd.notna(instance)
            else False
        )
        for instance, members in zip(joined["_instance"], joined["probe_instances"])
    ]
    joined["contribution_known"] = joined["_instance"].notna() & joined[
        "unknown_probe_instances"
    ].eq(0)
    summaries = []
    group_keys = ["visit_index", "horizon_minutes"]
    base = positions.drop(columns="_instance")
    for scope in ("corridor", "remaining"):
        for limit in limits:
            frame = joined.copy()
            frame["_member"] = (
                True if scope == "corridor" else frame["in_remaining_path"]
            )
            frame["_fresh"] = (
                frame["_member"]
                & frame["observation_age_minutes"].between(0, limit)
                & frame["congestion_index"].notna()
            )
            for column in (
                "congestion_index",
                "trailing_mean_congestion",
                "congestion_change",
                "observation_age_minutes",
                "probe_count",
                "bus_count",
            ):
                frame[column] = frame[column].where(frame["_fresh"])
            frame["_target"] = frame["target_bus_contributed"] & frame["_fresh"]
            frame["_unknown"] = ~frame["contribution_known"] & frame["_fresh"]
            summary = frame.groupby(group_keys, as_index=False).agg(
                segment_count=("_member", "sum"),
                observed_segments=("congestion_index", "count"),
                mean_congestion=("congestion_index", "mean"),
                worst_congestion=("congestion_index", "max"),
                trailing_mean_congestion=("trailing_mean_congestion", "mean"),
                congestion_change=("congestion_change", "mean"),
                mean_age_minutes=("observation_age_minutes", "mean"),
                oldest_age_minutes=("observation_age_minutes", "max"),
                probe_count=("probe_count", lambda values: values.sum(min_count=1)),
                min_bus_count=("bus_count", "min"),
                target_bus_contributed=("_target", "any"),
                contribution_unknown=("_unknown", "any"),
            )
            summary = base.merge(summary, on=group_keys, validate="one_to_one")
            summary["scope"] = scope
            summary["max_age_minutes"] = limit
            summary["scope_available"] = (
                True if scope == "corridor" else summary["position_known"]
            )
            summary.loc[~summary["scope_available"], "segment_count"] = np.nan
            summary["coverage_percent"] = (
                100
                * summary["observed_segments"]
                / summary["segment_count"].where(summary["segment_count"].gt(0))
            )
            summaries.append(summary)
    audit = pd.DataFrame(
        [
            ("reference_visits", delay_horizons["visit_index"].nunique()),
            ("path_matched_visits", targets["visit_index"].nunique()),
            ("excluded_static_paths", len(exclusions)),
            ("visit_horizon_pairs", len(targets)),
            ("unknown_or_stale_positions", len(unknown_positions)),
            (
                "receipt_precedes_feed_probes",
                int(
                    (
                        pd.to_datetime(
                            result.vehicle_probes.get(
                                "read_timestamp",
                                result.vehicle_probes["feed_timestamp"],
                            ),
                            utc=True,
                        )
                        < pd.to_datetime(
                            result.vehicle_probes["feed_timestamp"], utc=True
                        )
                    ).sum()
                ),
            ),
        ],
        columns=["measure", "count"],
    )
    return StopCongestionAnalysis(
        paths,
        observations,
        joined.sort_values(["visit_index", "horizon_minutes", "path_order"]),
        pd.concat(summaries, ignore_index=True),
        audit,
        reference_horizons,
    )


def _rank_correlation(
    frame: pd.DataFrame, feature: str, minimum_pairs: int
) -> tuple[int, float]:
    """Return paired count and descriptive Spearman correlation without SciPy.

    Parameters
    ----------
    frame : pandas.DataFrame
        One row per visit at a fixed horizon.
    feature : str
        Congestion descriptor to compare with the remaining signed error.
    minimum_pairs : int
        Minimum count for displaying a correlation.

    Returns
    -------
    tuple of int and float
        Paired count and rank correlation, or NaN for insufficient/constant data.
    """
    paired = frame[[feature, "remaining_delay_error_minutes"]].dropna()
    if len(paired) < minimum_pairs or paired.nunique().min() < 2:
        return len(paired), float("nan")
    return len(paired), float(paired.rank().corr().iloc[0, 1])


def summarize_stop_congestion_signal(
    comparisons: pd.DataFrame,
    *,
    horizons_minutes: Sequence[int] = (60, 45, 30, 15, 5),
    minimum_pairs: int = 10,
) -> pd.DataFrame:
    """Describe congestion/residual associations and coverage sensitivities.

    Parameters
    ----------
    comparisons : pandas.DataFrame
        Scoped comparisons from ``prepare_stop_congestion_analysis``.
    horizons_minutes : sequence of int, optional
        Horizons to compare separately, default 60, 45, 30, 15, and 5 minutes.
    minimum_pairs : int, optional
        Minimum paired visits for a displayed correlation, default 10.
        This is a display safeguard, not a significance threshold.

    Returns
    -------
    pandas.DataFrame
        Pair counts and descriptive Spearman correlations for each horizon,
        scope, freshness limit, feature, and subset. ``common_visits`` uses
        the same visits across all requested horizons and freshness limits,
        separately for each scope. ``external_only`` excludes any known or
        unidentifiable contribution from the evaluated trip. Constant or
        undersupported correlations are NaN, never zero.

    Raises
    ------
    ValueError
        If requested horizons are absent or the minimum pair count is invalid.
    """
    horizons = list(horizons_minutes)
    if (
        not horizons
        or len(set(horizons)) != len(horizons)
        or not set(horizons).issubset(comparisons["horizon_minutes"].unique())
    ):
        raise ValueError("Select unique horizons present in the comparisons.")
    if (
        isinstance(minimum_pairs, bool)
        or not isinstance(minimum_pairs, int)
        or minimum_pairs < 2
    ):
        raise ValueError("minimum_pairs must be an integer of at least two.")
    selected = comparisons.loc[comparisons["horizon_minutes"].isin(horizons)].copy()
    rows = []
    features = (
        "mean_congestion",
        "worst_congestion",
        "trailing_mean_congestion",
        "congestion_change",
    )
    for scope, scoped in selected.groupby("scope", sort=False):
        supported = scoped.dropna(
            subset=["mean_congestion", "remaining_delay_error_minutes"]
        )
        needed = len(horizons) * scoped["max_age_minutes"].nunique()
        common_ids = supported.groupby("visit_index").size()
        common_ids = common_ids.index[common_ids.eq(needed)]
        for (limit, horizon), frame in scoped.groupby(
            ["max_age_minutes", "horizon_minutes"], sort=True
        ):
            subsets = {
                "all_observed": frame,
                "common_visits": frame.loc[frame["visit_index"].isin(common_ids)],
                "complete_coverage": frame.loc[frame["coverage_percent"].eq(100)],
                "external_only": frame.loc[
                    ~frame["target_bus_contributed"] & ~frame["contribution_unknown"]
                ],
            }
            for subset, subset_frame in subsets.items():
                for feature in features:
                    count, correlation = _rank_correlation(
                        subset_frame, feature, minimum_pairs
                    )
                    rows.append(
                        {
                            "scope": scope,
                            "max_age_minutes": limit,
                            "horizon_minutes": horizon,
                            "subset": subset,
                            "feature": feature,
                            "paired_visits": count,
                            "spearman": correlation,
                        }
                    )
    return pd.DataFrame(rows)
