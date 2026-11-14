"""Compare origin-time endpoint forecasts with latest traversal references."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .road_segment_congestion import _normalize_direction_ids
from .stop_dynamics_model import evaluate_stop_dynamics_identity
from .trip_updates import _prepare_stop_delay_updates


@dataclass(frozen=True)
class EarlyTraversalComparison:
    """Early implied traversals with separate latest-reference fields.

    Attributes
    ----------
    observations : pandas.DataFrame
        One row per latest-reference-eligible dated stop pair, including early
        exclusions. ``origin_time`` is the first downstream publication.
        ``early_*`` fields use only publications at or before that origin.
        ``early_implied_traversal_seconds`` assumes zero dwell.
        ``early_scheduled_upstream_at`` is a calendar input, not final departure.
        ``early_scheduled_arrival_lead_minutes`` measures origin-to-scheduled
        downstream arrival using only the early-implied timetable.
        ``reference_traversal_seconds`` removes observed upstream dwell.
        Latest endpoint fields and ``schedule_revision_seconds`` are reference
        diagnostics, never early prediction inputs. Durations are in seconds.
    audit : pandas.DataFrame
        Latest-reference audit followed by mutually exclusive early-comparison
        reasons. Finite negative early predictions are flagged, not excluded.
    """

    observations: pd.DataFrame
    audit: pd.DataFrame


def prepare_early_traversal_comparison(
    trip_updates: pd.DataFrame,
    segment: pd.Series,
    *,
    route_id: str = "97-KEL",
    timezone: str = "America/Vancouver",
) -> EarlyTraversalComparison:
    """Invert earliest endpoint delay forecasts into a zero-dwell traversal.

    Select the first downstream publication and the latest upstream publication
    at or before that origin, for the same dated journey and adjacent numeric
    stop sequences. Equal-time reports use the greatest database ID when
    present, otherwise the first input row. Missing selected values are never
    replaced with a later downstream or older upstream estimate.

    Infer scheduled arrivals from early arrival-minus-delay fields and compute
    ``early_allowance + early_downstream_delay - early_upstream_delay``.
    This equals the early predicted arrival-to-arrival interval; setting dwell
    to zero interprets it as a traversal proxy, not a separately fitted model.

    Latest reports supply reference values only. Reconstructing their traversal
    from the delay identity uses observed dwell and equals latest downstream
    arrival minus latest upstream departure. Neither latest delay nor latest
    schedule is used in an early prediction.

    Parameters
    ----------
    trip_updates : pandas.DataFrame
        Trusted trip-update history accepted by ``evaluate_stop_dynamics_identity``.
    segment : pandas.Series
        Exact directed stop-pair metadata.
    route_id : str, default "97-KEL"
        Selected route.
    timezone : str, default "America/Vancouver"
        Calendar timezone for dated-journey normalization and reference summaries.

    Returns
    -------
    EarlyTraversalComparison
        New observations and audit. Inputs are not modified. Excluded early
        estimates remain in the returned frame with explicit reasons.

    Raises
    ------
    ValueError
        For invalid input, unusable latest references, inconsistent history or
        no eligible predeparture early comparisons.

    Warns
    -----
    UserWarning
        If latest references or early comparison rows are excluded.
    """
    reference = evaluate_stop_dynamics_identity(
        trip_updates,
        segment,
        route_id=route_id,
        display_timezone=timezone,
    )
    route = trip_updates.loc[
        trip_updates["trip_route_id"].astype("string").eq(route_id)
    ]
    directions = _normalize_direction_ids(route["trip_direction_id"], "trip_updates")
    endpoints = route.loc[
        directions.eq(segment["trip_direction_id"])
        & route["stop_id"]
        .astype("string")
        .isin([str(segment["from_stop_id"]), str(segment["to_stop_id"])])
    ].copy()
    starts = endpoints["trip_start_timestamp"].astype("string").str.strip()
    sequence = pd.to_numeric(endpoints["stop_sequence"], errors="raise")
    valid = (
        starts.notna()
        & starts.ne("")
        & sequence.notna()
        & sequence.gt(0)
        & sequence.mod(1).eq(0)
    )
    endpoints = endpoints.loc[valid].copy()
    endpoints["stop_sequence"] = sequence.loc[valid].astype("int64")
    stops = pd.DataFrame(
        {
            "stop_id": [str(segment["from_stop_id"]), str(segment["to_stop_id"])],
            "stop_name": [segment["from_stop_name"], segment["to_stop_name"]],
        }
    )
    history, keys = _prepare_stop_delay_updates(
        endpoints,
        stops,
        route_id=route_id,
        stop_name=None,
        agency_timezone=timezone,
    )
    history["arrival_time"] = pd.to_datetime(
        history["arrival_time"],
        utc=True,
        errors="raise",
    )
    # Normalize tied publications before selecting the earliest timestamp.
    history = history.drop_duplicates([*keys, "feed_timestamp"], keep="first")
    downstream = history.loc[
        history["stop_id"].eq(str(segment["to_stop_id"]))
    ].drop_duplicates(keys, keep="last")
    upstream = history.loc[history["stop_id"].eq(str(segment["from_stop_id"]))]
    fields = ["feed_timestamp", "arrival_time", "arrival_delay"]
    fields += [
        column
        for column in ("id", "schedule_relationship", "trip_schedule_relationship")
        if column in history
    ]
    down_keys = ["trip_instance_key", "stop_id", "stop_sequence"]
    early_downstream = downstream[down_keys + fields].rename(
        columns={
            **{column: f"early_downstream_{column}" for column in fields},
            "feed_timestamp": "origin_time",
        }
    )
    early_upstream = upstream[down_keys + fields].rename(
        columns={
            "stop_id": "from_stop_id",
            "stop_sequence": "from_stop_sequence",
            **{column: f"early_upstream_{column}" for column in fields},
        }
    )
    frame = reference.observations.copy()
    frame["stop_sequence"] = frame["stop_sequence"].astype("int64")
    frame["from_stop_sequence"] = frame["from_stop_sequence"].astype("int64")
    frame["from_stop_id"] = frame["from_stop_id"].astype("string")
    frame = frame.merge(
        early_downstream,
        on=down_keys,
        how="left",
        validate="one_to_one",
    )
    if frame["origin_time"].isna().any():
        raise ValueError(
            "Latest references are missing their downstream report history."
        )
    frame = pd.merge_asof(
        frame.sort_values("origin_time", kind="stable"),
        early_upstream.sort_values("early_upstream_feed_timestamp", kind="stable"),
        left_on="origin_time",
        right_on="early_upstream_feed_timestamp",
        by=["trip_instance_key", "from_stop_id", "from_stop_sequence"],
        direction="backward",
        allow_exact_matches=True,
    ).reset_index(drop=True)

    for endpoint in ("upstream", "downstream"):
        frame[f"early_scheduled_{endpoint}_at"] = frame[
            f"early_{endpoint}_arrival_time"
        ] - pd.to_timedelta(frame[f"early_{endpoint}_arrival_delay"], unit="s")
    frame["early_allowance_seconds"] = (
        frame["early_scheduled_downstream_at"] - frame["early_scheduled_upstream_at"]
    ).dt.total_seconds()
    frame["early_scheduled_arrival_lead_minutes"] = (
        frame["early_scheduled_downstream_at"] - frame["origin_time"]
    ).dt.total_seconds() / 60
    frame["early_implied_traversal_seconds"] = (
        frame["early_allowance_seconds"]
        + frame["early_downstream_arrival_delay"]
        - frame["early_upstream_arrival_delay"]
    )
    frame["early_nonpositive_traversal"] = frame["early_implied_traversal_seconds"].le(
        0
    )
    frame["reference_traversal_seconds"] = frame["traversal_seconds"]
    frame["reference_delay_reconstructed_traversal_seconds"] = (
        frame["inferred_allowance_seconds"]
        + frame["arrival_delay"]
        - frame["upstream_arrival_delay"]
        - frame["dwell_seconds"]
    )
    frame["reference_available_at"] = frame[
        ["feed_timestamp", "upstream_feed_timestamp"]
    ].max(axis=1)
    frame["schedule_revision_seconds"] = (
        frame["inferred_allowance_seconds"] - frame["early_allowance_seconds"]
    )
    frame["origin_lead_minutes"] = (
        frame["upstream_departure_time"] - frame["origin_time"]
    ).dt.total_seconds() / 60
    if not np.allclose(
        frame["reference_traversal_seconds"],
        frame["reference_delay_reconstructed_traversal_seconds"],
        rtol=0,
        atol=1e-6,
    ):
        raise ValueError(
            "Latest delay accounting does not match traversal reference times."
        )

    reasons = pd.Series("included", index=frame.index, dtype="string")
    checks = [
        ("missing_asof_upstream_report", frame["early_upstream_feed_timestamp"].isna()),
    ]
    for endpoint in ("downstream", "upstream"):
        for field in ("arrival_time", "arrival_delay"):
            column = f"early_{endpoint}_{field}"
            checks.append((f"missing_{column}", frame[column].isna()))
        stop_flag = f"early_{endpoint}_schedule_relationship"
        trip_flag = f"early_{endpoint}_trip_schedule_relationship"
        if stop_flag in frame:
            flags = pd.to_numeric(frame[stop_flag], errors="raise")
            checks.append((f"early_{endpoint}_skipped_or_no_data", flags.isin([1, 2])))
        if trip_flag in frame:
            flags = pd.to_numeric(frame[trip_flag], errors="raise")
            checks.append((f"early_{endpoint}_cancelled_trip", flags.isin([3, 7])))
    checks += [
        ("nonpositive_early_allowance", frame["early_allowance_seconds"].le(0)),
        (
            "origin_not_before_departure",
            frame["origin_time"].ge(frame["upstream_departure_time"]),
        ),
        (
            "no_later_downstream_report",
            frame["origin_time"].ge(frame["feed_timestamp"]),
        ),
    ]
    for reason, mask in checks:
        reasons.loc[reasons.eq("included") & mask] = reason
    frame["comparison_exclusion_reason"] = reasons
    early_audit = (
        reasons.value_counts()
        .rename_axis("reason")
        .reset_index(name="count")
        .assign(stage="early_comparison")
    )
    audit = pd.concat([reference.audit, early_audit], ignore_index=True)
    if reasons.ne("included").any():
        warnings.warn(
            "Some earliest traversal comparisons were excluded; inspect comparison audit.",
            UserWarning,
            stacklevel=2,
        )
    if not reasons.eq("included").any():
        raise ValueError(
            "No eligible earliest traversal comparisons: "
            f"{reasons.value_counts().to_dict()}."
        )
    return EarlyTraversalComparison(frame, audit)
