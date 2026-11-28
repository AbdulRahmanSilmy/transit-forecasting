"""Origin-time delay forecasting harness: chained traversal models vs the feed.

This module is the protected verifier for ``experiments/hwy97_delay``. It
prepares one row per stop visit as known at each journey's first realtime
publication, chains traversal forecasts through the delay identity, scores
every method on identical rows and enforces the study's leakage guardrails.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .anchor_models import FEED_ANCHOR, AnchorModel, make_anchor_model
from .dwell_models import (
    DEFAULT_DWELL,
    DWELL_REQUEST_COLUMNS,
    DwellModel,
    StopMedianDwell,
    make_dwell_model,
)
from .feed_clock import journey_timetable_offsets, timetable_offset_summary
from .road_segment_congestion import _normalize_direction_ids, _require_columns
from .traversal_models import (
    BASELINE_MODELS,
    ShuffledTargetTraversal,
    TraversalModel,
    make_traversal_model,
)

REQUIRED_COLUMNS = (
    "trip_route_id",
    "trip_direction_id",
    "trip_id",
    "trip_start_timestamp",
    "stop_id",
    "stop_sequence",
    "feed_timestamp",
    "arrival_time",
    "arrival_delay",
    "departure_time",
)
REQUEST_COLUMNS = ("segment_key", "from_stop_id", "to_stop_id", "scheduled_departure")
ANCHOR_REQUEST_COLUMNS = ("trip_instance_key", "stop_id", "calendar_departure")
SKIPPED_OR_NO_DATA = (1, 2)
CANCELLED_TRIP = (3, 7)
B0 = "B0_transit_bc"
B1 = "B1_constant_anchor"
B2 = "B2_segment_median"
B3 = "B3_calendar_median"
LEAD_BINS = (0, 60, 90, 120, 150, 180, np.inf)
PERIODS = (
    ("early", 0, 6),
    ("am_peak", 6, 9),
    ("midday", 9, 15),
    ("pm_peak", 15, 18),
    ("evening", 18, 24),
)


@dataclass(frozen=True)
class StudyConfig:
    """Frozen study definitions read from ``experiments/<study>/config.json``.

    Attributes
    ----------
    study : str
        Study directory name, used for output paths.
    route_id : str
        GTFS route.
    direction_id : int
        Trip direction.
    timezone : str
        Agency timezone for service days and calendar features.
    service_day_start_hour : int
        Local wall-clock hour at which a service day starts.
    dev_dataset_id, dev_archive_start, dev_archive_end : str
        Development snapshot ID and its inclusive UTC R2 bounds.
    dev_end : datetime.date
        Last development service day.
    fold_starts : tuple of datetime.date
        First service day of each development evaluation block.
    block_days : int
        Development block length in service days.
    embargo_days : int
        Service days between a training cutoff and its evaluation block.
    test_start, test_end : datetime.date
        Inclusive locked-test service days.
    test_dataset_id, test_archive_start, test_archive_end : str
        Locked-test snapshot ID and its inclusive UTC R2 bounds.
    holidays : tuple of datetime.date
        Statutory holidays, scored separately from the primary metric.
    bootstrap_reps : int
        Service-day bootstrap repetitions.
    seed : int
        Random seed for bootstrap and shuffled controls.
    min_relative_improvement : float
        Required relative MAE reduction versus B0, e.g. ``0.05``.
    max_final_report_age_minutes : float
        A final report published more than this long before the event it
        describes is a stale prediction, not an observation, and is excluded.
    calendar_clock : {"timetable", "feed"}
        Clock for service days and calendar features: the trip's public
        timetable (feed schedule plus the journey's whole-hour offset to its
        start descriptor) or the feed's own schedule. See
        :mod:`forecasting.feed_clock`.
    max_clock_mismatch_share : float
        Maximum monthly share of journeys whose descriptor offset is not a
        whole number of hours; a higher share stops the run.
    """

    study: str
    route_id: str
    direction_id: int
    timezone: str
    service_day_start_hour: int
    dev_dataset_id: str
    dev_archive_start: str
    dev_archive_end: str
    dev_end: date
    fold_starts: tuple[date, ...]
    block_days: int
    embargo_days: int
    test_start: date
    test_end: date
    test_dataset_id: str
    test_archive_start: str
    test_archive_end: str
    holidays: tuple[date, ...]
    bootstrap_reps: int
    seed: int
    min_relative_improvement: float
    max_final_report_age_minutes: float = 10.0
    calendar_clock: str = "timetable"
    max_clock_mismatch_share: float = 0.05

    def __post_init__(self) -> None:
        """Validate split ordering so no fold can reach the locked test."""
        if not self.fold_starts or list(self.fold_starts) != sorted(self.fold_starts):
            raise ValueError("fold_starts must be a nonempty ascending sequence.")
        if self.block_days <= 0 or self.embargo_days < 0:
            raise ValueError("block_days must be positive and embargo_days >= 0.")
        last_fold_end = self.fold_starts[-1] + timedelta(days=self.block_days - 1)
        if last_fold_end > self.dev_end:
            raise ValueError("Development folds must end on or before dev_end.")
        if (self.test_start - self.dev_end).days <= self.embargo_days:
            raise ValueError("The locked test must start after dev_end plus embargo.")
        if self.test_end < self.test_start:
            raise ValueError("test_end must not precede test_start.")

    @classmethod
    def from_json(cls, path: str | Path) -> StudyConfig:
        """Read a study configuration file.

        Parameters
        ----------
        path : str or pathlib.Path
            JSON file with one key per attribute; dates use ISO format.

        Returns
        -------
        StudyConfig
            Validated configuration.
        """
        raw = json.loads(Path(path).read_text())
        for key in ("dev_end", "test_start", "test_end"):
            raw[key] = date.fromisoformat(raw[key])
        for key in ("fold_starts", "holidays"):
            raw[key] = tuple(date.fromisoformat(value) for value in raw[key])
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the configuration.

        Returns
        -------
        dict
            Attributes with dates as ISO strings.
        """
        return json.loads(json.dumps(asdict(self), default=str))


@dataclass(frozen=True)
class OriginVisits:
    """Stop visits and segment links as known at each journey's origin.

    Attributes
    ----------
    visits : pandas.DataFrame
        One row per dated stop visit. ``origin_*`` and ``scheduled_*`` fields
        come only from the journey's first publication; ``final_*`` fields and
        ``truth_delay_seconds`` come from its latest publication. Rows with
        ``score_exclusion_reason == 'included'`` are scored.
    links : pandas.DataFrame
        One row per stop visit with a consecutive upstream visit: request
        fields (``REQUEST_COLUMNS``), final traversal/dwell outcomes,
        ``available_at`` and ``train_exclusion_reason``.
    audit : pandas.DataFrame
        Exclusion counts by stage and reason.
    data_end : pandas.Timestamp
        Latest publication in the input.
    """

    visits: pd.DataFrame
    links: pd.DataFrame
    audit: pd.DataFrame
    data_end: pd.Timestamp


def _service_day(times: pd.Series, timezone: str, start_hour: int) -> pd.Series:
    """Map aware instants to naive local service dates with a wall-clock cutover."""
    local = times.dt.tz_convert(timezone).dt.tz_localize(None)
    return (local - pd.Timedelta(hours=start_hour)).dt.normalize()


def _audit(stage: str, reasons: pd.Series) -> pd.DataFrame:
    """Count reasons for one stage."""
    return (
        reasons.value_counts()
        .rename_axis("reason")
        .reset_index(name="count")
        .assign(stage=stage)
    )


def _first_reason(
    checks: Sequence[tuple[str, pd.Series]], index: pd.Index
) -> pd.Series:
    """Assign the first failing check's name, otherwise ``'included'``."""
    reasons = pd.Series("included", index=index, dtype="string")
    for reason, mask in checks:
        reasons.loc[reasons.eq("included") & mask.fillna(False).to_numpy(bool)] = reason
    return reasons


def _untracked_journeys(visits: pd.DataFrame) -> pd.Series:
    """Flag visits of journeys whose final reported delays are all exactly 0 s.

    A tracked bus essentially never reports exactly 0 s at every stop, so such
    a journey carries only schedule-only estimates (e.g. the 2026-11-01 feed
    outage) and has no observed outcome (issue #21).

    Parameters
    ----------
    visits : pandas.DataFrame
        One row per stop visit with ``trip_instance_key``,
        ``final_arrival_time`` and ``final_arrival_delay``, and optionally
        ``final_departure_time`` and ``final_departure_delay``.

    Returns
    -------
    pandas.Series
        Boolean, aligned with ``visits``. A delay counts only where its event
        time is present; a journey with no such delay is not flagged.
    """
    reported = []
    for kind in ("arrival", "departure"):
        delay, time = f"final_{kind}_delay", f"final_{kind}_time"
        if delay in visits and time in visits:
            values = pd.to_numeric(visits[delay], errors="coerce")
            reported.append(values.where(visits[time].notna()))
    delays = pd.concat(reported, axis=1)
    any_reported = delays.notna().any(axis=1)
    any_nonzero = (delays.notna() & delays.ne(0)).any(axis=1)
    journey = visits["trip_instance_key"]
    has_report = any_reported.groupby(journey).transform("any")
    has_nonzero = any_nonzero.groupby(journey).transform("any")
    return (has_report & ~has_nonzero).astype(bool)


def prepare_origin_visits(
    trip_updates: pd.DataFrame,
    *,
    route_id: str,
    direction_id: int,
    timezone: str = "America/Vancouver",
    service_day_start_hour: int = 3,
    max_final_report_age_minutes: float = 10.0,
    clock_tolerance_minutes: float = 1.0,
    calendar_clock: str = "timetable",
) -> OriginVisits:
    """Build origin-time features, feed baselines and final outcomes per visit.

    A journey is a ``trip_id`` plus its start descriptor. Its origin ``t0`` is
    its first publication; only rows published at ``t0`` (greatest ``id`` on
    ties) supply origin fields. Scheduled times are inferred as predicted time
    minus predicted delay at ``t0``. The anchor is the predicted departure at
    the journey's first stop. Truth is the latest published ``arrival_delay``,
    unless that report was published more than
    ``max_final_report_age_minutes`` before the arrival it describes: such a
    report is an un-updated prediction, so the visit is excluded as stale.
    The same rule applies to both ends of a training link, except a
    first-stop departure: the feed leaves an on-time departure at its origin
    estimate, which vehicle positions confirm (E010), so that end is kept.
    A journey whose final reported arrival and departure delays are all
    exactly 0 s is untracked (schedule-only estimates, issue #21); all its
    visits and links are excluded as ``untracked_journey``.
    Event times are never shifted. Each journey's timetable offset (start
    descriptor minus first-stop feed schedule, in whole hours; see
    :mod:`forecasting.feed_clock`) is recorded. With
    ``calendar_clock="timetable"`` the calendar clock (service day and the
    ``scheduled_departure`` request field seen by traversal models) is the feed
    schedule plus that offset; with ``"feed"`` it is the feed schedule. A
    journey whose offset is not a whole number of hours is excluded as
    ``schedule_clock_mismatch``. Missing values are never filled from other
    publications.

    Parameters
    ----------
    trip_updates : pandas.DataFrame
        Realtime trip updates with ``REQUIRED_COLUMNS``; ``id``,
        ``departure_delay``, ``schedule_relationship`` and
        ``trip_schedule_relationship`` are used when present. Naive timestamps
        are UTC.
    route_id : str
        Route to select.
    direction_id : int
        Direction to select.
    timezone : str, default "America/Vancouver"
        Agency timezone for service days.
    service_day_start_hour : int, default 3
        Local service-day cutover hour.
    max_final_report_age_minutes : float, default 10.0
        Maximum time from a final report's publication to the arrival (or,
        for a training link's upstream end, the departure) it describes.
    clock_tolerance_minutes : float, default 1.0
        Allowed residual between the descriptor and the first-stop feed
        schedule after rounding to whole hours.
    calendar_clock : {"timetable", "feed"}, default "timetable"
        Clock used for service days and calendar features.

    Returns
    -------
    OriginVisits
        New frames; the input is not modified.

    Raises
    ------
    ValueError
        If required columns are missing or nothing matches the route/direction.
    """
    _require_columns(trip_updates, REQUIRED_COLUMNS, "trip_updates")
    # Work on integer codes: string operations on millions of rows exhaust memory.
    selected = np.array(
        trip_updates["trip_route_id"].eq(route_id).fillna(False), dtype=bool
    )
    directions = _normalize_direction_ids(
        trip_updates.loc[selected, "trip_direction_id"], "trip_updates"
    )
    selected[selected] = directions.eq(direction_id).fillna(False).to_numpy(bool)
    fields = ["arrival_time", "arrival_delay", "departure_time"]
    fields += [
        column
        for column in (
            "departure_delay",
            "schedule_relationship",
            "trip_schedule_relationship",
        )
        if column in trip_updates
    ]
    columns = ["trip_id", "trip_start_timestamp", "stop_id", "stop_sequence"]
    columns += ["feed_timestamp", *fields] + (["id"] if "id" in trip_updates else [])
    frame = trip_updates.loc[selected, columns]
    if frame.empty:
        raise ValueError(f"No updates for route {route_id!r} direction {direction_id}.")
    for column in ("feed_timestamp", "arrival_time", "departure_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
    trip_codes, trip_values = pd.factorize(frame["trip_id"])
    start_codes, start_values = pd.factorize(frame["trip_start_timestamp"])
    stop_codes, stop_values = pd.factorize(frame["stop_id"])
    start_text = pd.Series(pd.Index(start_values).astype("string")).str.strip()
    blank_start = (start_text.isna() | start_text.eq("")).to_numpy(bool)
    missing_start = (start_codes < 0) | blank_start[np.maximum(start_codes, 0)]
    sequence = pd.to_numeric(frame["stop_sequence"], errors="coerce")
    publication_reason = _first_reason(
        [
            ("missing_trip_start", pd.Series(missing_start, index=frame.index)),
            ("missing_trip_id", pd.Series(trip_codes < 0, index=frame.index)),
            ("missing_stop_id", pd.Series(stop_codes < 0, index=frame.index)),
            (
                "invalid_stop_sequence",
                sequence.isna() | sequence.le(0) | sequence.mod(1).ne(0),
            ),
        ],
        frame.index,
    )
    audit = [_audit("publications", publication_reason)]
    keep = publication_reason.eq("included").to_numpy(bool)
    frame = frame.loc[
        keep, ["feed_timestamp", *fields] + (["id"] if "id" in frame else [])
    ]
    frame["stop_sequence"] = sequence.to_numpy()[keep].astype("int64")
    frame["trip_code"] = trip_codes[keep]
    frame["start_code"] = start_codes[keep]
    frame["stop_code"] = stop_codes[keep]
    frame["journey"] = frame["trip_code"].astype("int64") * (len(start_values) + 1) + (
        frame["start_code"] + 1
    )
    order = ["feed_timestamp", "id"] if "id" in frame else ["feed_timestamp"]
    frame = frame.sort_values(order, kind="stable")
    data_end = frame["feed_timestamp"].max()
    keys = ["journey", "stop_sequence"]

    conflicting = frame.groupby(keys)["stop_code"].nunique().gt(1)
    bad_journeys = conflicting[conflicting].index.get_level_values(0).unique()
    journey_reason = pd.Series(
        "included", index=frame["journey"].unique(), dtype="string"
    )
    journey_reason.loc[bad_journeys] = "conflicting_stop_identity"
    audit.append(_audit("journeys", journey_reason))
    frame = frame.loc[~frame["journey"].isin(bad_journeys)]

    frame["origin_time"] = frame.groupby("journey")["feed_timestamp"].transform("min")
    origin = frame.loc[frame["feed_timestamp"].eq(frame["origin_time"])]
    origin = origin.drop_duplicates(keys, keep="last")[keys + fields]
    final = frame.drop_duplicates(keys, keep="last")
    visits = final[
        keys
        + ["trip_code", "start_code", "stop_code", "origin_time", "feed_timestamp"]
        + fields
    ]
    del frame
    visits = visits.rename(
        columns={"feed_timestamp": "final_feed_timestamp"}
        | {column: f"final_{column}" for column in fields}
    )
    visits = visits.merge(
        origin.rename(columns={column: f"origin_{column}" for column in fields}),
        on=keys,
        how="left",
        validate="one_to_one",
    )
    trip_text = pd.Series(pd.Index(trip_values).astype("string"))
    visits["trip_id"] = trip_text.to_numpy()[visits["trip_code"].to_numpy()]
    visits["stop_id"] = pd.array(
        pd.Index(stop_values).astype("string")[visits["stop_code"].to_numpy()],
        dtype="string",
    )
    visits["trip_instance_key"] = pd.array(
        visits["trip_id"].astype("string")
        + "|"
        + start_text.to_numpy()[visits["start_code"].to_numpy()],
        dtype="string",
    )
    visits = visits.drop(columns=["journey", "trip_code", "start_code", "stop_code"])
    visits = visits.sort_values(
        ["trip_instance_key", "stop_sequence"], kind="stable"
    ).reset_index(drop=True)

    visits["scheduled_arrival"] = visits["origin_arrival_time"] - pd.to_timedelta(
        visits["origin_arrival_delay"], unit="s"
    )
    if "origin_departure_delay" in visits:
        scheduled_departure = visits["origin_departure_time"] - pd.to_timedelta(
            visits["origin_departure_delay"], unit="s"
        )
    else:
        scheduled_departure = visits["origin_departure_time"]
    visits["scheduled_departure"] = scheduled_departure.fillna(
        visits["scheduled_arrival"]
    )

    journey = visits.groupby("trip_instance_key", sort=False)
    visits["first_stop_sequence"] = journey["stop_sequence"].transform("min")
    visits["is_first_stop"] = visits["stop_sequence"].eq(visits["first_stop_sequence"])
    first = visits.loc[visits["is_first_stop"]].set_index("trip_instance_key")
    anchor_time = first["origin_departure_time"].fillna(first["origin_arrival_time"])
    anchor_delay = first["origin_arrival_delay"].copy()
    if "origin_departure_delay" in first:
        has_departure = first["origin_departure_time"].notna()
        anchor_delay.loc[has_departure] = first.loc[
            has_departure, "origin_departure_delay"
        ]
    visits["anchor_time"] = visits["trip_instance_key"].map(anchor_time)
    visits["anchor_delay_seconds"] = (
        visits["trip_instance_key"].map(anchor_delay).astype(float)
    )
    if calendar_clock not in ("timetable", "feed"):
        raise ValueError("calendar_clock must be 'timetable' or 'feed'.")
    anchor_clock = visits["trip_instance_key"].map(first["scheduled_departure"])
    descriptor = pd.to_datetime(
        visits["trip_instance_key"].str.split("|").str[1], errors="coerce"
    )
    offset_minutes, clock_mismatch = journey_timetable_offsets(
        anchor_clock,
        descriptor,
        timezone=timezone,
        tolerance_minutes=clock_tolerance_minutes,
    )
    visits["timetable_offset_minutes"] = offset_minutes
    visits["timetable_clock_mismatch"] = clock_mismatch
    calendar_shift = pd.to_timedelta(
        offset_minutes.fillna(0) if calendar_clock == "timetable" else 0.0, unit="m"
    )
    visits["calendar_departure"] = visits["scheduled_departure"] + calendar_shift
    visits["calendar_arrival"] = visits["scheduled_arrival"] + calendar_shift
    visits["service_day"] = _service_day(
        (anchor_clock + calendar_shift).fillna(visits["origin_time"]),
        timezone,
        service_day_start_hour,
    )

    previous = visits.groupby("trip_instance_key", sort=False)[
        [
            "stop_sequence",
            "stop_id",
            "final_arrival_time",
            "final_departure_time",
            "final_feed_timestamp",
            "scheduled_departure",
            "scheduled_arrival",
            "calendar_departure",
            "is_first_stop",
        ]
        + (
            ["final_schedule_relationship"]
            if "final_schedule_relationship" in visits
            else []
        )
    ].shift(1)
    visits["consecutive"] = previous["stop_sequence"].eq(visits["stop_sequence"] - 1)
    link_ok = (
        visits["consecutive"]
        & previous["scheduled_departure"].notna()
        & visits["scheduled_arrival"].notna()
    )
    visits["link_ok"] = link_ok
    broken = (~link_ok & ~visits["is_first_stop"]).astype(int)
    visits["chain_ok"] = broken.groupby(visits["trip_instance_key"]).cumsum().eq(0)

    skipped = pd.Series(False, index=visits.index)
    cancelled = pd.Series(False, index=visits.index)
    if "final_schedule_relationship" in visits:
        skipped = visits["final_schedule_relationship"].isin(SKIPPED_OR_NO_DATA)
    if "final_trip_schedule_relationship" in visits:
        cancelled = visits["final_trip_schedule_relationship"].isin(CANCELLED_TRIP)
    visits["truth_delay_seconds"] = visits["final_arrival_delay"].astype(float)
    visits["lead_minutes"] = (
        visits["final_arrival_time"] - visits["origin_time"]
    ).dt.total_seconds() / 60
    max_age = pd.Timedelta(minutes=max_final_report_age_minutes)
    stale_arrival = (visits["final_arrival_time"] - visits["final_feed_timestamp"]).gt(
        max_age
    )
    untracked = _untracked_journeys(visits)
    visits["untracked_journey"] = untracked
    visits["score_exclusion_reason"] = _first_reason(
        [
            ("anchor_stop", visits["is_first_stop"]),
            ("missing_anchor", visits["anchor_time"].isna()),
            ("schedule_clock_mismatch", clock_mismatch),
            ("untracked_journey", untracked),
            (
                "missing_origin_report",
                visits["origin_arrival_time"].isna()
                | visits["origin_arrival_delay"].isna(),
            ),
            ("broken_chain", ~visits["chain_ok"]),
            ("cancelled_trip", cancelled),
            ("skipped_or_no_data", skipped),
            (
                "missing_final_arrival",
                visits["final_arrival_time"].isna()
                | visits["final_arrival_delay"].isna(),
            ),
            ("unfinished_arrival", visits["final_arrival_time"].gt(data_end)),
            ("stale_final_report", stale_arrival),
            ("nonpositive_lead", visits["lead_minutes"].le(0)),
        ],
        visits.index,
    )
    audit.append(_audit("scored_visits", visits["score_exclusion_reason"]))

    links = visits.loc[visits["consecutive"]].copy()
    upstream = previous.loc[links.index]
    links["from_stop_id"] = upstream["stop_id"].astype("string")
    links["to_stop_id"] = links["stop_id"]
    links["segment_key"] = links["from_stop_id"] + "->" + links["to_stop_id"]
    # Models see the calendar clock; chaining uses feed (physical) times.
    links["scheduled_departure"] = upstream["calendar_departure"]
    links["feed_scheduled_departure"] = upstream["scheduled_departure"]
    links["feed_scheduled_arrival"] = links["scheduled_arrival"]
    links["upstream_feed_scheduled_arrival"] = upstream["scheduled_arrival"]
    links["upstream_is_first_stop"] = upstream["is_first_stop"].astype(bool)
    links["traversal_seconds"] = (
        links["final_arrival_time"] - upstream["final_departure_time"]
    ).dt.total_seconds()
    links["upstream_dwell_seconds"] = (
        upstream["final_departure_time"] - upstream["final_arrival_time"]
    ).dt.total_seconds()
    links.loc[links["upstream_is_first_stop"], "upstream_dwell_seconds"] = np.nan
    # Origin-time scheduled dwell (feed fields), zero at the first stop.
    links["scheduled_dwell_seconds"] = (
        (links["feed_scheduled_departure"] - links["upstream_feed_scheduled_arrival"])
        .dt.total_seconds()
        .where(~links["upstream_is_first_stop"], 0.0)
    )
    # Observed arrival delay at the upstream stop, for training dwell models
    # that respond to delay; NaN when that arrival report is stale.
    links["upstream_arrival_delay_seconds"] = (
        (upstream["final_arrival_time"] - upstream["scheduled_arrival"])
        .dt.total_seconds()
        .where(
            ~(upstream["final_arrival_time"] - upstream["final_feed_timestamp"]).gt(
                max_age
            )
        )
    )
    links["available_at"] = pd.concat(
        [links["final_feed_timestamp"], upstream["final_feed_timestamp"]], axis=1
    ).max(axis=1)
    upstream_skipped = pd.Series(False, index=links.index)
    if "final_schedule_relationship" in upstream:
        upstream_skipped = upstream["final_schedule_relationship"].isin(
            SKIPPED_OR_NO_DATA
        )
    links["train_exclusion_reason"] = _first_reason(
        [
            ("schedule_clock_mismatch", clock_mismatch.loc[links.index]),
            ("untracked_journey", untracked.loc[links.index]),
            ("cancelled_trip", cancelled.loc[links.index]),
            ("skipped_or_no_data", skipped.loc[links.index] | upstream_skipped),
            ("missing_scheduled_departure", links["scheduled_departure"].isna()),
            ("missing_final_arrival", links["final_arrival_time"].isna()),
            ("missing_upstream_departure", upstream["final_departure_time"].isna()),
            ("unfinished_arrival", links["final_arrival_time"].gt(data_end)),
            (
                "stale_final_report",
                stale_arrival.loc[links.index]
                | (
                    (
                        upstream["final_departure_time"]
                        - upstream["final_feed_timestamp"]
                    ).gt(max_age)
                    # A first-stop departure left at its origin estimate is an
                    # on-time departure, not an old prediction (vehicle check, E010).
                    & ~links["upstream_is_first_stop"]
                ),
            ),
            ("nonpositive_traversal", links["traversal_seconds"].le(0)),
        ],
        links.index,
    )
    audit.append(_audit("training_links", links["train_exclusion_reason"]))
    link_columns = [
        "trip_instance_key",
        "stop_sequence",
        "service_day",
        *REQUEST_COLUMNS,
        "upstream_is_first_stop",
        "feed_scheduled_departure",
        "feed_scheduled_arrival",
        "upstream_feed_scheduled_arrival",
        "link_ok",
        "traversal_seconds",
        "upstream_dwell_seconds",
        "scheduled_dwell_seconds",
        "upstream_arrival_delay_seconds",
        "available_at",
        "train_exclusion_reason",
    ]
    return OriginVisits(
        visits=visits,
        links=links[link_columns].reset_index(drop=True),
        audit=pd.concat(audit, ignore_index=True),
        data_end=data_end,
    )


# The study's default dwell rule now lives in :mod:`forecasting.dwell_models`.
StopDwellMedian = StopMedianDwell


def chain_delay_forecast(
    visits: pd.DataFrame,
    links: pd.DataFrame,
    traversal_seconds: np.ndarray,
    dwell_seconds: np.ndarray,
) -> pd.Series:
    """Chain link forecasts from the origin anchor into arrival delays.

    For stop ``k`` after the first stop,
    ``A_k = anchor + sum_{j<=k} (T_j + W_{j-1})`` and
    ``d_k = A_k - scheduled_arrival_k``. This equals repeated application of
    ``d_k = d_{k-1} + T_k + W_{k-1} - S_k`` with origin-time allowances.

    Parameters
    ----------
    visits : pandas.DataFrame
        Prepared visits with ``trip_instance_key``, ``stop_sequence``,
        ``anchor_time`` and ``scheduled_arrival``.
    links : pandas.DataFrame
        Links of the same journeys with ``trip_instance_key`` and
        ``stop_sequence`` (the downstream visit).
    traversal_seconds, dwell_seconds : numpy.ndarray
        Forecasts aligned with ``links``. NaN propagates downstream.

    Returns
    -------
    pandas.Series
        Forecast arrival delay in seconds, indexed like ``visits``; NaN where
        no complete chain exists.
    """
    increments = links[["trip_instance_key", "stop_sequence"]].copy()
    increments["step"] = np.asarray(traversal_seconds, float) + np.asarray(
        dwell_seconds, float
    )
    increments = increments.sort_values(["trip_instance_key", "stop_sequence"])
    # cumsum skips NaN, so carry an explicit "seen a NaN" flag downstream.
    missing = increments["step"].isna().astype(int)
    increments["elapsed"] = increments.groupby("trip_instance_key")["step"].cumsum()
    increments.loc[
        missing.groupby(increments["trip_instance_key"]).cumsum().gt(0), "elapsed"
    ] = np.nan
    merged = visits[["trip_instance_key", "stop_sequence"]].merge(
        increments[["trip_instance_key", "stop_sequence", "elapsed"]],
        on=["trip_instance_key", "stop_sequence"],
        how="left",
        validate="one_to_one",
    )
    arrival = visits["anchor_time"] + pd.to_timedelta(
        merged["elapsed"].to_numpy(), unit="s"
    )
    return pd.Series(
        (arrival - visits["scheduled_arrival"]).dt.total_seconds().to_numpy(),
        index=visits.index,
    )


def chain_with_dwell_model(
    visits: pd.DataFrame,
    links: pd.DataFrame,
    traversal_seconds: np.ndarray,
    dwell_model: DwellModel,
) -> tuple[pd.Series, np.ndarray]:
    """Chain stop by stop with a dwell model that responds to predicted delay.

    Walks each journey's links in order. Before each link it gives the dwell
    model the chain's predicted arrival delay at the upstream stop (anchor
    plus the elapsed time so far, minus the upstream scheduled arrival), then
    adds the predicted dwell and traversal. With the dwell array fixed, the
    result is exactly :func:`chain_delay_forecast`.

    Parameters
    ----------
    visits : pandas.DataFrame
        Prepared visits with ``trip_instance_key``, ``stop_sequence``,
        ``is_first_stop``, ``anchor_time`` and ``scheduled_arrival``.
    links : pandas.DataFrame
        Links of the same journeys with ``trip_instance_key``,
        ``stop_sequence``, ``upstream_feed_scheduled_arrival`` and the
        ``DWELL_REQUEST_COLUMNS``.
    traversal_seconds : numpy.ndarray
        Traversal forecasts aligned with ``links``.
    dwell_model : DwellModel
        Fitted model; called with ``arrival_delay_seconds`` if it sets
        ``uses_arrival_delay``.

    Returns
    -------
    delay : pandas.Series
        Forecast arrival delay in seconds, indexed like ``visits``.
    dwell : numpy.ndarray
        Dwell seconds used at each link's upstream stop, aligned with
        ``links``.
    """
    requests = links[list(DWELL_REQUEST_COLUMNS)]
    if not getattr(dwell_model, "uses_arrival_delay", False):
        dwell = np.asarray(dwell_model.predict(requests), dtype=float)
        return chain_delay_forecast(visits, links, traversal_seconds, dwell), dwell
    traversal = np.asarray(traversal_seconds, dtype=float)
    first = visits.loc[visits["is_first_stop"]].set_index("trip_instance_key")
    anchor = links["trip_instance_key"].map(first["anchor_time"])
    order = np.lexsort(
        (links["stop_sequence"].to_numpy(), links["trip_instance_key"].to_numpy())
    )
    keys = links["trip_instance_key"].to_numpy()[order]
    position = pd.Series(keys).groupby(keys).cumcount().to_numpy()
    upstream_offset = (
        (links["upstream_feed_scheduled_arrival"] - anchor).dt.total_seconds()
    ).to_numpy()[order]
    elapsed_before = np.zeros(len(links))
    dwell_sorted = np.full(len(links), np.nan)
    for step in range(int(position.max()) + 1 if len(links) else 0):
        at = np.flatnonzero(position == step)
        if step > 0:
            elapsed_before[at] = elapsed_before[at - 1] + (
                dwell_sorted[at - 1] + traversal[order[at - 1]]
            )
        arrival_delay = elapsed_before[at] - upstream_offset[at]
        dwell_sorted[at] = dwell_model.predict(
            requests.iloc[order[at]], arrival_delay_seconds=arrival_delay
        )
    dwell = np.empty(len(links))
    dwell[order] = dwell_sorted
    return chain_delay_forecast(visits, links, traversal, dwell), dwell


def first_stop_departures(
    visits: pd.DataFrame, max_final_report_age_minutes: float
) -> pd.DataFrame:
    """Observed first-stop departure delays usable to train an anchor model.

    Keeps first-stop visits whose final departure report is fresh (published
    at most ``max_final_report_age_minutes`` before the departure it reports),
    on tracked (see ``untracked_journey``), non-cancelled, non-skipped
    journeys with a consistent timetable clock.

    Parameters
    ----------
    visits : pandas.DataFrame
        Prepared visits from :func:`prepare_origin_visits`.
    max_final_report_age_minutes : float
        The study's stale-report threshold.

    Returns
    -------
    pandas.DataFrame
        One row per journey: ``trip_instance_key``, ``stop_id``,
        ``calendar_departure``, ``departure_delay_seconds`` (final departure
        minus feed scheduled departure) and ``available_at`` (the final
        report's publication time).
    """
    first = visits.loc[visits["is_first_stop"]]
    age = first["final_departure_time"] - first["final_feed_timestamp"]
    keep = (
        first["final_departure_time"].notna()
        & first["scheduled_departure"].notna()
        & age.le(pd.Timedelta(minutes=max_final_report_age_minutes))
        & ~first["timetable_clock_mismatch"].astype(bool)
    )
    if "untracked_journey" in first:
        keep &= ~first["untracked_journey"].astype(bool)
    if "final_schedule_relationship" in first:
        keep &= ~first["final_schedule_relationship"].isin(SKIPPED_OR_NO_DATA)
    if "final_trip_schedule_relationship" in first:
        keep &= ~first["final_trip_schedule_relationship"].isin(CANCELLED_TRIP)
    first = first.loc[keep]
    return pd.DataFrame(
        {
            "trip_instance_key": first["trip_instance_key"],
            "stop_id": first["stop_id"],
            "calendar_departure": first["calendar_departure"],
            "departure_delay_seconds": (
                first["final_departure_time"] - first["scheduled_departure"]
            ).dt.total_seconds(),
            "available_at": first["final_feed_timestamp"],
        }
    ).reset_index(drop=True)


def learned_anchor(
    visits: pd.DataFrame,
    eval_visits: pd.DataFrame,
    *,
    cutoff: pd.Timestamp,
    anchor_name: str,
    config: StudyConfig,
    anchor_factory: Callable[[str, str], AnchorModel] = make_anchor_model,
) -> pd.Series:
    """Fit an anchor model before the cutoff and predict evaluation journeys.

    Parameters
    ----------
    visits : pandas.DataFrame
        All prepared visits (training source).
    eval_visits : pandas.DataFrame
        Visits of the block's evaluation journeys.
    cutoff : pandas.Timestamp
        Training cutoff; only departures reported before it train.
    anchor_name : str
        Registered anchor model name.
    config : StudyConfig
        Study configuration.
    anchor_factory : callable, optional
        ``(name, timezone) -> AnchorModel``; injectable for tests.

    Returns
    -------
    pandas.Series
        Predicted first-stop departure delay (seconds) per evaluation
        ``trip_instance_key``.

    Raises
    ------
    AssertionError
        If a leakage guardrail fails.
    """
    departures = first_stop_departures(visits, config.max_final_report_age_minutes)
    train = departures.loc[departures["available_at"].lt(cutoff)]
    journeys = eval_visits["trip_instance_key"].unique()
    # Guardrails 1 and 3 for the anchor: no outcome after the cutoff, disjoint journeys.
    assert train["available_at"].max() < cutoff
    assert not set(train["trip_instance_key"]).intersection(journeys)
    requests = eval_visits.loc[
        eval_visits["is_first_stop"], list(ANCHOR_REQUEST_COLUMNS)
    ].reset_index(drop=True)
    model = anchor_factory(anchor_name, config.timezone).fit(train)
    predicted = np.asarray(model.predict(requests.drop(columns="trip_instance_key")))
    if predicted.shape != (len(requests),):
        raise ValueError(f"Anchor {anchor_name} returned {predicted.shape} values.")
    return pd.Series(predicted, index=requests["trip_instance_key"].to_numpy())


def fold_cutoff(start: date, config: StudyConfig) -> pd.Timestamp:
    """Return the UTC training cutoff for an evaluation block.

    Parameters
    ----------
    start : datetime.date
        First evaluation service day.
    config : StudyConfig
        Study configuration (embargo, cutover hour, timezone).

    Returns
    -------
    pandas.Timestamp
        Service-day start ``embargo_days`` before ``start``, in UTC.
    """
    local = pd.Timestamp(start) - pd.Timedelta(days=config.embargo_days)
    local += pd.Timedelta(hours=config.service_day_start_hour)
    return local.tz_localize(config.timezone).tz_convert("UTC")


def evaluate_block(
    prepared: OriginVisits,
    *,
    model_name: str,
    block_start: date,
    block_days: int,
    config: StudyConfig,
    fold: str,
    model_factory: Callable[[str, str], TraversalModel] = make_traversal_model,
    anchor_name: str = FEED_ANCHOR,
    anchor_factory: Callable[[str, str], AnchorModel] = make_anchor_model,
    dwell_name: str = DEFAULT_DWELL,
    dwell_factory: Callable[[str], DwellModel] = make_dwell_model,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit on links available before the cutoff and forecast one block.

    Parameters
    ----------
    prepared : OriginVisits
        Output of :func:`prepare_origin_visits`.
    model_name : str
        Candidate traversal model name (may itself be a baseline).
    block_start : datetime.date
        First evaluation service day.
    block_days : int
        Number of evaluation service days.
    config : StudyConfig
        Study configuration.
    fold : str
        Label stored on every row.
    model_factory : callable, optional
        ``(name, timezone) -> TraversalModel``; injectable for tests.
    anchor_name : str, default 'feed'
        Chain anchor for B2, B3 and the candidate. ``'feed'`` uses Transit
        BC's earliest first-stop estimate; any other name is an anchor model
        (see :mod:`forecasting.anchor_models`) trained before the cutoff. B0
        and B1 always use the feed.
    anchor_factory : callable, optional
        ``(name, timezone) -> AnchorModel``; injectable for tests.
    dwell_name : str, default 'stop_median'
        Dwell model for B2, B3, the candidate and the control (see
        :mod:`forecasting.dwell_models`), fitted once on the fold's training
        links. B0 and B1 do not use it.
    dwell_factory : callable, optional
        ``name -> DwellModel``; injectable for tests.

    Returns
    -------
    visit_rows : pandas.DataFrame
        One row per scored visit with truth, ``pred_<method>`` columns and
        breakdown fields. With a learned anchor, ``anchor_model_s`` holds the
        journey's predicted first-stop departure delay.
        ``actual_departure_delay_s`` (the journey's final reported first-stop
        departure delay) and ``chain_anchor_delay_s`` (the anchor the
        chained methods started from) are evaluation-only; they feed the
        anchor-free score. With a dwell model that responds to predicted
        delay, ``anchor_free_pred_<method>`` holds each chained method
        re-chained from the actual first-stop departure (a shift is not
        exact once dwell depends on delay).
    link_rows : pandas.DataFrame
        One row per segment traversal of the block's scored journeys with a
        valid observed outcome: ``actual_traversal_s``, ``actual_dwell_s``
        (upstream dwell; NaN at the first stop), ``trav_<method>`` predictions
        (B0 is the feed's scheduled run time) and ``dwell_<method>``
        predictions (``dwell_B0_transit_bc`` is the scheduled dwell;
        ``dwell_model`` is the dwell model's prediction, along the
        candidate's chain, or B3's when the candidate is a baseline).

    Raises
    ------
    AssertionError
        If a leakage guardrail fails.
    """
    cutoff = fold_cutoff(block_start, config)
    start = pd.Timestamp(block_start)
    end = start + pd.Timedelta(days=block_days)
    visits = prepared.visits
    links = prepared.links
    in_block = visits["service_day"].ge(start) & visits["service_day"].lt(end)
    scored = visits.loc[in_block & visits["score_exclusion_reason"].eq("included")]
    journeys = scored["trip_instance_key"].unique()
    eval_visits = visits.loc[visits["trip_instance_key"].isin(journeys)]
    eval_links = links.loc[links["trip_instance_key"].isin(journeys) & links["link_ok"]]
    train = links.loc[
        links["train_exclusion_reason"].eq("included")
        & links["available_at"].lt(cutoff)
    ]
    if train.empty or scored.empty:
        raise ValueError(f"Fold {fold} has no training links or no scored visits.")
    # Guardrails 1 and 3: outcomes after the cutoff never train; journeys are disjoint.
    assert train["available_at"].max() < cutoff
    assert not set(train["trip_instance_key"]).intersection(journeys)
    assert (scored["origin_time"] < scored["final_arrival_time"]).all()
    requests = eval_links[list(REQUEST_COLUMNS)].copy()
    assert list(requests.columns) == list(REQUEST_COLUMNS)

    dwell_model = dwell_factory(dwell_name).fit(train)
    delay_aware = bool(getattr(dwell_model, "uses_arrival_delay", False))
    rows = scored[
        [
            "trip_instance_key",
            "stop_sequence",
            "stop_id",
            "service_day",
            "scheduled_arrival",
            "calendar_arrival",
            "origin_time",
            "lead_minutes",
            "truth_delay_seconds",
        ]
    ].copy()
    rows["fold"] = fold
    rows[f"pred_{B0}"] = scored["origin_arrival_delay"].astype(float)
    rows[f"pred_{B1}"] = scored["anchor_delay_seconds"]
    chain_visits = eval_visits
    if anchor_name != FEED_ANCHOR:
        anchor = learned_anchor(
            visits,
            eval_visits,
            cutoff=cutoff,
            anchor_name=anchor_name,
            config=config,
            anchor_factory=anchor_factory,
        )
        journey_anchor = eval_visits["trip_instance_key"].map(anchor).astype(float)
        first_departure = eval_visits["trip_instance_key"].map(
            eval_visits.loc[eval_visits["is_first_stop"]].set_index(
                "trip_instance_key"
            )["scheduled_departure"]
        )
        chain_visits = eval_visits.assign(
            anchor_time=first_departure
            + pd.to_timedelta(journey_anchor.to_numpy(), unit="s")
        )
        rows["anchor_model_s"] = journey_anchor.loc[scored.index]
    # Evaluation only, never a feature: the actual first-stop departure delay
    # and the chain's anchor delay, for scoring with the anchor error removed.
    first_stop = eval_visits.loc[eval_visits["is_first_stop"]].set_index(
        "trip_instance_key"
    )
    chain_first = chain_visits.loc[chain_visits["is_first_stop"]].set_index(
        "trip_instance_key"
    )
    # Like truth (the reported arrival_delay), use the reported departure_delay;
    # time arithmetic is only a fallback. The two disagree by 1 h for a few
    # Nov 2 journeys whose feed carried two clocks during the outage restart.
    departure_delay = (
        first_stop["final_departure_time"] - first_stop["scheduled_departure"]
    ).dt.total_seconds()
    if "final_departure_delay" in first_stop:
        reported = first_stop["final_departure_delay"].astype(float)
        departure_delay = reported.where(
            reported.notna() & first_stop["final_departure_time"].notna(),
            departure_delay,
        )
    chain_anchor = (
        chain_first["anchor_time"] - chain_first["scheduled_departure"]
    ).dt.total_seconds()
    rows["actual_departure_delay_s"] = (
        scored["trip_instance_key"].map(departure_delay).astype(float)
    )
    rows["chain_anchor_delay_s"] = (
        scored["trip_instance_key"].map(chain_anchor).astype(float)
    )
    if delay_aware:
        # Evaluation only: the same chain started from the actual departure.
        actual_start = eval_visits["trip_instance_key"].map(departure_delay)
        first_departure = eval_visits["trip_instance_key"].map(
            first_stop["scheduled_departure"]
        )
        actual_visits = eval_visits.assign(
            anchor_time=first_departure
            + pd.to_timedelta(actual_start.astype(float).to_numpy(), unit="s")
        )
    methods: dict[str, TraversalModel] = {
        B2: model_factory("segment_median", config.timezone),
        B3: model_factory("calendar_median", config.timezone),
    }
    if model_name not in BASELINE_MODELS:
        methods[model_name] = model_factory(model_name, config.timezone)
        methods[f"control_shuffled_{model_name}"] = ShuffledTargetTraversal(
            model_factory(model_name, config.timezone), seed=config.seed
        )
    links_out = eval_links[
        [
            "trip_instance_key",
            "stop_sequence",
            "service_day",
            "segment_key",
            "from_stop_id",
            "to_stop_id",
            "upstream_is_first_stop",
            "feed_scheduled_departure",
            "scheduled_departure",
            "traversal_seconds",
            "upstream_dwell_seconds",
            "train_exclusion_reason",
        ]
    ].rename(
        columns={
            "scheduled_departure": "calendar_departure",
            "traversal_seconds": "actual_traversal_s",
            "upstream_dwell_seconds": "actual_dwell_s",
        }
    )
    links_out["fold"] = fold
    links_out[f"trav_{B0}"] = (
        eval_links["feed_scheduled_arrival"] - eval_links["feed_scheduled_departure"]
    ).dt.total_seconds()
    scheduled_dwell = (
        eval_links["feed_scheduled_departure"]
        - eval_links["upstream_feed_scheduled_arrival"]
    ).dt.total_seconds()
    links_out[f"dwell_{B0}"] = scheduled_dwell.where(
        ~eval_links["upstream_is_first_stop"], 0.0
    )
    dwell_owner = model_name if model_name in methods else B3
    for name, model in methods.items():
        model.fit(train)
        traversal = np.asarray(model.predict(requests), dtype=float)
        if traversal.shape != (len(requests),):
            raise ValueError(f"{name} returned {traversal.shape} predictions.")
        chained, dwell = chain_with_dwell_model(
            chain_visits, eval_links, traversal, dwell_model
        )
        rows[f"pred_{name}"] = chained.loc[scored.index]
        if delay_aware:
            anchor_free, _ = chain_with_dwell_model(
                actual_visits, eval_links, traversal, dwell_model
            )
            rows[f"anchor_free_pred_{name}"] = anchor_free.loc[scored.index]
        links_out[f"trav_{name}"] = traversal
        if name == dwell_owner:
            links_out["dwell_model"] = dwell
    # Observed traversals must be valid outcomes (not stale, skipped, etc.).
    links_out = links_out.loc[links_out["train_exclusion_reason"].eq("included")]
    links_out = links_out.drop(columns="train_exclusion_reason")
    return rows.reset_index(drop=True), links_out.reset_index(drop=True)


def _errors(rows: pd.DataFrame, method: str) -> pd.Series:
    """Signed forecast error in seconds."""
    return rows[f"pred_{method}"] - rows["truth_delay_seconds"]


def _summary(rows: pd.DataFrame, methods: Sequence[str]) -> pd.DataFrame:
    """MAE, median AE, RMSE and bias per method."""
    records = []
    for method in methods:
        error = _errors(rows, method)
        records.append(
            {
                "method": method,
                "rows": len(rows),
                "MAE_s": error.abs().mean(),
                "MedAE_s": error.abs().median(),
                "RMSE_s": float(np.sqrt((error**2).mean())),
                "bias_s": error.mean(),
            }
        )
    return pd.DataFrame(records)


def day_bootstrap_delta(
    rows: pd.DataFrame,
    method: str,
    reference: str,
    *,
    reps: int,
    seed: int,
) -> tuple[float, float, float]:
    """Paired MAE difference with a service-day cluster bootstrap CI.

    Parameters
    ----------
    rows : pandas.DataFrame
        Scored rows with ``service_day`` and both prediction columns.
    method, reference : str
        Methods to compare; the difference is ``MAE(method) - MAE(reference)``.
    reps : int
        Bootstrap repetitions.
    seed : int
        Random seed.

    Returns
    -------
    tuple of float
        Point difference, lower and upper 95% percentile bounds, in seconds.
    """
    grouped = pd.DataFrame(
        {
            "day": rows["service_day"].to_numpy(),
            "method": _errors(rows, method).abs().to_numpy(),
            "reference": _errors(rows, reference).abs().to_numpy(),
        }
    ).groupby("day")
    sums = grouped[["method", "reference"]].sum().to_numpy()
    counts = grouped.size().to_numpy()
    point = sums[:, 0].sum() / counts.sum() - sums[:, 1].sum() / counts.sum()
    rng = np.random.default_rng(seed)
    sample = rng.integers(0, len(counts), size=(reps, len(counts)))
    n = counts[sample].sum(axis=1)
    deltas = (sums[sample, 0].sum(axis=1) - sums[sample, 1].sum(axis=1)) / n
    low, high = np.percentile(deltas, [2.5, 97.5])
    return float(point), float(low), float(high)


def _breakdown(
    rows: pd.DataFrame, methods: Sequence[str], by: pd.Series
) -> pd.DataFrame:
    """MAE per method within groups."""
    table = pd.DataFrame(
        {method: _errors(rows, method).abs() for method in methods}
    ).groupby(by.to_numpy(), observed=True)
    out = table.mean()
    out.insert(0, "rows", table.size())
    return out.rename_axis(by.name).reset_index()


def anchor_free_scores(
    rows: pd.DataFrame,
    methods: Sequence[str],
    *,
    candidate: str | None,
    config: StudyConfig,
) -> dict[str, Any] | None:
    """Score chained methods as if every chain started at the actual departure.

    The first-stop departure delay is out of the study's scope, but every
    chained forecast inherits its anchor error. This secondary score removes
    it: each chained prediction is shifted by ``chain_anchor_delay_s -
    actual_departure_delay_s``, so only the delay accumulated after the first
    stop is judged. Where ``anchor_free_pred_<method>`` exists (a dwell model
    that responds to delay), that re-chained prediction is used instead. B1 becomes "delay stays as at the actual departure". B0
    (Transit BC's own per-stop estimate) is not chained and is excluded.

    Parameters
    ----------
    rows : pandas.DataFrame
        Primary scored rows with ``actual_departure_delay_s`` and
        ``chain_anchor_delay_s``.
    methods : sequence of str
        Methods with ``pred_<method>`` columns.
    candidate : str or None
        Candidate method; paired against B3 when given.
    config : StudyConfig
        Bootstrap settings.

    Returns
    -------
    dict or None
        ``rows``, ``excluded_rows`` (no actual departure), ``summary``,
        ``by_fold``, ``by_stop`` and ``paired_vs_B3`` (each chained method's
        MAE minus B3's, with a service-day bootstrap CI); None if the rows
        lack the departure columns.
    """
    if "actual_departure_delay_s" not in rows or "chain_anchor_delay_s" not in rows:
        return None
    known = (
        rows["actual_departure_delay_s"].notna() & rows["chain_anchor_delay_s"].notna()
    )
    shifted = rows.loc[known].copy()
    actual = shifted["actual_departure_delay_s"]
    scored_methods = [method for method in methods if method != B0]
    for method in scored_methods:
        if method == B1:
            shifted[f"pred_{B1}"] = actual
        elif f"anchor_free_pred_{method}" in shifted:
            shifted[f"pred_{method}"] = shifted[f"anchor_free_pred_{method}"]
        else:
            shifted[f"pred_{method}"] = shifted[f"pred_{method}"] - (
                shifted["chain_anchor_delay_s"] - actual
            )
    paired = []
    for method in scored_methods:
        if method in (B1, B3):
            continue
        delta, low, high = day_bootstrap_delta(
            shifted, method, B3, reps=config.bootstrap_reps, seed=config.seed
        )
        paired.append(
            {"method": method, "delta_MAE_s": delta, "ci_low_s": low, "ci_high_s": high}
        )
    return {
        "rows": len(shifted),
        "excluded_rows": int((~known).sum()),
        "summary": _summary(shifted, scored_methods),
        "by_fold": _breakdown(shifted, scored_methods, shifted["fold"].rename("fold")),
        "by_stop": _breakdown(
            shifted, scored_methods, shifted["stop_sequence"].rename("stop_sequence")
        ),
        "paired_vs_B3": pd.DataFrame(paired),
    }


def score_predictions(
    predictions: pd.DataFrame,
    *,
    config: StudyConfig,
    candidate: str | None,
) -> dict[str, Any]:
    """Score all methods on identical rows and check the success criterion.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Concatenated :func:`evaluate_block` outputs.
    config : StudyConfig
        Holidays, bootstrap settings and minimum improvement.
    candidate : str or None
        Candidate method name, or None when only baselines ran.

    Returns
    -------
    dict
        ``coverage``, ``summary``, ``paired_vs_B0``, ``by_fold``, ``by_stop``,
        ``by_lead``, ``by_period``, ``holidays``, ``anchor_free`` (see
        :func:`anchor_free_scores`), ``control`` and ``criterion`` entries
        (DataFrames or plain values).
    """
    methods = [column[5:] for column in predictions if column.startswith("pred_")]
    finite = pd.DataFrame(
        {method: np.isfinite(predictions[f"pred_{method}"]) for method in methods}
    )
    common = finite.all(axis=1)
    coverage = pd.DataFrame(
        {
            "method": methods,
            "unscorable_rows": [int((~finite[m]).sum()) for m in methods],
        }
    )
    holidays = {pd.Timestamp(day) for day in config.holidays}
    is_holiday = predictions["service_day"].isin(holidays)
    rows = predictions.loc[common & ~is_holiday].copy()
    holiday_rows = predictions.loc[common & is_holiday]
    result: dict[str, Any] = {
        "coverage": coverage,
        "common_rows": int(common.sum()),
        "primary_rows": len(rows),
        "primary_days": int(rows["service_day"].nunique()),
        "holiday_rows": len(holiday_rows),
        "summary": _summary(rows, methods),
    }
    paired = []
    reference_mae = _errors(rows, B0).abs().mean()
    for method in methods:
        if method == B0:
            continue
        delta, low, high = day_bootstrap_delta(
            rows, method, B0, reps=config.bootstrap_reps, seed=config.seed
        )
        method_error = _errors(rows, method).abs()
        reference_error = _errors(rows, B0).abs()
        decided = ~np.isclose(method_error, reference_error, rtol=0, atol=1e-9)
        paired.append(
            {
                "method": method,
                "delta_MAE_s": delta,
                "ci_low_s": low,
                "ci_high_s": high,
                "relative_change": delta / reference_mae,
                "win_rate_vs_B0": (
                    float((method_error < reference_error)[decided].mean())
                    if decided.any()
                    else np.nan
                ),
            }
        )
    result["paired_vs_B0"] = pd.DataFrame(paired)
    result["by_fold"] = _breakdown(rows, methods, rows["fold"].rename("fold"))
    result["by_stop"] = _breakdown(
        rows, methods, rows["stop_sequence"].rename("stop_sequence")
    )
    lead = pd.cut(rows["lead_minutes"], LEAD_BINS, right=False).astype("string")
    result["by_lead"] = _breakdown(rows, methods, lead.rename("lead_minutes"))
    calendar = "calendar_arrival" if "calendar_arrival" in rows else "scheduled_arrival"
    hour = rows[calendar].dt.tz_convert(config.timezone).dt.hour
    period = pd.Series("evening", index=rows.index, name="period")
    for name, low_hour, high_hour in PERIODS:
        period.loc[hour.ge(low_hour) & hour.lt(high_hour)] = name
    result["by_period"] = _breakdown(rows, methods, period)
    result["holidays"] = (
        _summary(holiday_rows, methods) if len(holiday_rows) else pd.DataFrame()
    )
    # Secondary metric (owner, 2026-11-22); the success criterion stays above.
    result["anchor_free"] = anchor_free_scores(
        rows, methods, candidate=candidate, config=config
    )
    result["control"] = None
    result["criterion"] = None
    if candidate is not None and candidate not in BASELINE_MODELS:
        summary = result["summary"].set_index("method")["MAE_s"]
        control = f"control_shuffled_{candidate}"
        # Guardrail 5: a permuted-target model must not beat the segment median.
        result["control"] = {
            "control_MAE_s": float(summary[control]),
            "B2_MAE_s": float(summary[B2]),
            "passed": bool(summary[control] >= 0.99 * summary[B2]),
        }
        row = result["paired_vs_B0"].set_index("method").loc[candidate]
        result["criterion"] = {
            "ci_entirely_below_zero": bool(row["ci_high_s"] < 0),
            "relative_change": float(row["relative_change"]),
            "meets_min_improvement": bool(
                row["relative_change"] <= -config.min_relative_improvement
            ),
            "beats_B3": bool(summary[candidate] < summary[B3]),
        }
        result["criterion"]["meets_all"] = all(
            value
            for key, value in result["criterion"].items()
            if key != "relative_change"
        )
    return result


def segment_labels(traversals: pd.DataFrame) -> pd.Series:
    """Readable label per segment key, ordered along the route.

    Parameters
    ----------
    traversals : pandas.DataFrame
        Traversal rows with ``segment_key``, ``stop_sequence``,
        ``from_stop_id`` and ``to_stop_id``.

    Returns
    -------
    pandas.Series
        ``"seq <n>: <from> -> <to>"`` indexed by segment key, sorted by the
        segment's median downstream stop sequence.
    """
    grouped = traversals.groupby("segment_key")
    order = grouped["stop_sequence"].median().sort_values(kind="stable")
    first = grouped[["from_stop_id", "to_stop_id"]].first().loc[order.index]
    return pd.Series(
        [
            f"seq {int(sequence)}: {row.from_stop_id} -> {row.to_stop_id}"
            for sequence, row in zip(order, first.itertuples())
        ],
        index=order.index,
        name="segment",
    )


def score_traversals(
    traversals: pd.DataFrame,
    *,
    config: StudyConfig,
) -> dict[str, pd.DataFrame]:
    """Score traversal and dwell predictions per method and per segment.

    Rows with any non-finite traversal prediction and holiday service days are
    excluded, so every method is scored on identical traversals.

    Parameters
    ----------
    traversals : pandas.DataFrame
        Concatenated ``link_rows`` from :func:`evaluate_block`.
    config : StudyConfig
        Holidays.

    Returns
    -------
    dict
        ``summary`` (traversal MAE/MedAE/RMSE/bias per method),
        ``dwell_summary`` (dwell MAE/bias per dwell rule, upstream stops
        after the first only) and ``by_segment`` (rows, label and traversal
        MAE per method for each segment, in route order).
    """
    methods = [column[5:] for column in traversals if column.startswith("trav_")]
    finite = np.all(
        [np.isfinite(traversals[f"trav_{m}"]) for m in methods], axis=0
    ) & np.isfinite(traversals["actual_traversal_s"])
    holidays = {pd.Timestamp(day) for day in config.holidays}
    rows = traversals.loc[finite & ~traversals["service_day"].isin(holidays)]
    records = []
    for method in methods:
        error = rows[f"trav_{method}"] - rows["actual_traversal_s"]
        records.append(
            {
                "method": method,
                "rows": len(rows),
                "MAE_s": error.abs().mean(),
                "MedAE_s": error.abs().median(),
                "RMSE_s": float(np.sqrt((error**2).mean())),
                "bias_s": error.mean(),
            }
        )
    dwell_rows = rows.loc[
        ~rows["upstream_is_first_stop"] & rows["actual_dwell_s"].ge(0)
    ]
    dwell_records = []
    for rule in [c for c in rows if c.startswith("dwell_")]:
        error = dwell_rows[rule] - dwell_rows["actual_dwell_s"]
        dwell_records.append(
            {
                "rule": rule[6:],
                "rows": len(dwell_rows),
                "MAE_s": error.abs().mean(),
                "bias_s": error.mean(),
            }
        )
    labels = segment_labels(rows)
    errors = pd.DataFrame(
        {m: (rows[f"trav_{m}"] - rows["actual_traversal_s"]).abs() for m in methods}
    )
    grouped = errors.groupby(rows["segment_key"].to_numpy())
    by_segment = grouped.mean().reindex(labels.index)
    by_segment.insert(0, "rows", grouped.size().reindex(labels.index))
    by_segment.insert(0, "segment", labels)
    return {
        "summary": pd.DataFrame(records),
        "dwell_summary": pd.DataFrame(dwell_records),
        "by_segment": by_segment.rename_axis("segment_key").reset_index(),
    }


@dataclass(frozen=True)
class StudyRun:
    """Predictions, scores and audit from one harness run.

    Attributes
    ----------
    predictions : pandas.DataFrame
        All scored rows across folds.
    metrics : dict
        Output of :func:`score_predictions`.
    audit : pandas.DataFrame
        Preparation exclusion counts.
    info : dict
        Split, model, folds, cutoffs and data provenance.
    traversals : pandas.DataFrame
        Per-segment traversal and dwell predictions with observed outcomes.
    """

    predictions: pd.DataFrame
    metrics: dict[str, Any]
    audit: pd.DataFrame
    info: dict[str, Any]
    traversals: pd.DataFrame = field(default_factory=pd.DataFrame)


def check_test_access(
    config: StudyConfig, *, approved: bool, today: date | None = None
) -> None:
    """Refuse locked-test access without approval or before it has finished.

    Parameters
    ----------
    config : StudyConfig
        Study configuration.
    approved : bool
        Whether the owner explicitly approved this run.
    today : datetime.date or None, optional
        Local date to check against; defaults to now in the study timezone.

    Raises
    ------
    PermissionError
        If not approved, or the test period has not fully elapsed.
    """
    if not approved:
        raise PermissionError(
            "The locked test needs explicit owner approval (--i-have-owner-approval)."
        )
    if today is None:
        today = pd.Timestamp.now(tz=config.timezone).date()
    if today <= config.test_end + timedelta(days=1):
        raise PermissionError(
            f"The locked test runs through {config.test_end}; wait until it has fully "
            "elapsed and been archived."
        )


def run_study(
    config: StudyConfig,
    trip_updates: pd.DataFrame,
    *,
    model_name: str,
    split: str,
    approved: bool = False,
    today: date | None = None,
    model_factory: Callable[[str, str], TraversalModel] = make_traversal_model,
    anchor: str = FEED_ANCHOR,
    dwell: str = DEFAULT_DWELL,
) -> StudyRun:
    """Run the development folds or the locked test for one candidate.

    Parameters
    ----------
    config : StudyConfig
        Study configuration.
    trip_updates : pandas.DataFrame
        Realtime trip updates covering the needed window.
    model_name : str
        Candidate traversal model name.
    split : {'dev', 'test'}
        Development folds or the owner-approved locked test.
    approved : bool, default False
        Owner approval for ``split='test'``.
    today : datetime.date or None, optional
        Override for the test-period check.
    model_factory : callable, optional
        Traversal model factory; injectable for tests.
    anchor : str, default 'feed'
        Chain anchor for B2, B3 and the candidate (see :func:`evaluate_block`).
    dwell : str, default 'stop_median'
        Dwell model for the chained methods (see :func:`evaluate_block`).

    Returns
    -------
    StudyRun
        Predictions, metrics, audit and run information.

    Raises
    ------
    PermissionError
        For unapproved or premature locked-test access.
    AssertionError
        If development data reaches the locked test or a guardrail fails.
    """
    if split not in ("dev", "test"):
        raise ValueError("split must be 'dev' or 'test'.")
    if split == "test":
        check_test_access(config, approved=approved, today=today)
    prepared = prepare_origin_visits(
        trip_updates,
        route_id=config.route_id,
        direction_id=config.direction_id,
        timezone=config.timezone,
        service_day_start_hour=config.service_day_start_hour,
        max_final_report_age_minutes=config.max_final_report_age_minutes,
        calendar_clock=config.calendar_clock,
    )
    clock = timetable_offset_summary(prepared.visits)
    high = clock.loc[clock["share_mismatch"].gt(config.max_clock_mismatch_share)]
    if not high.empty:
        raise ValueError(
            "Trip start descriptors disagree with feed schedules by a non-whole "
            f"number of hours too often (see forecasting.feed_clock):\n"
            f"{high.to_string(index=False)}"
        )
    if split == "dev":
        horizon = pd.Timestamp(config.dev_end)
        prepared = OriginVisits(
            visits=prepared.visits.loc[prepared.visits["service_day"].le(horizon)],
            links=prepared.links.loc[prepared.links["service_day"].le(horizon)],
            audit=prepared.audit,
            data_end=prepared.data_end,
        )
        assert prepared.visits["service_day"].max() < pd.Timestamp(config.test_start)
        assert prepared.links["service_day"].max() < pd.Timestamp(config.test_start)
        blocks = [
            (f"dev{i + 1}", start, config.block_days)
            for i, start in enumerate(config.fold_starts)
        ]
    else:
        blocks = [
            ("test", config.test_start, (config.test_end - config.test_start).days + 1)
        ]
    blocks_out = [
        evaluate_block(
            prepared,
            model_name=model_name,
            block_start=start,
            block_days=days,
            config=config,
            fold=label,
            model_factory=model_factory,
            anchor_name=anchor,
            dwell_name=dwell,
        )
        for label, start, days in blocks
    ]
    predictions = pd.concat([visit for visit, _ in blocks_out], ignore_index=True)
    traversals = pd.concat([link for _, link in blocks_out], ignore_index=True)
    candidate = None if model_name in BASELINE_MODELS else model_name
    metrics = score_predictions(predictions, config=config, candidate=candidate)
    metrics["schedule_clock"] = clock
    metrics["traversal"] = score_traversals(traversals, config=config)
    info = {
        "split": split,
        "model": model_name,
        "anchor": anchor,
        "dwell": dwell,
        "blocks": [
            {
                "fold": label,
                "start": str(start),
                "days": days,
                "train_cutoff_utc": str(fold_cutoff(start, config)),
            }
            for label, start, days in blocks
        ],
        "data_end_utc": str(prepared.data_end),
    }
    return StudyRun(predictions, metrics, prepared.audit, info, traversals)


def _jsonable(value: Any) -> Any:
    """Convert metrics to JSON-compatible structures."""
    if isinstance(value, pd.DataFrame):
        return json.loads(value.to_json(orient="records", date_format="iso"))
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def log_row(
    run: StudyRun,
    *,
    run_id: str,
    git_hash: str,
    log_id: str = "",
    hypothesis: str = "",
) -> str:
    """Format a LOG.md summary-table row for a run.

    Parameters
    ----------
    run : StudyRun
        Completed run.
    run_id : str
        MLflow run ID or output directory name.
    git_hash : str
        Short commit hash.
    log_id, hypothesis : str, optional
        LOG entry and backlog IDs; placeholders when empty.

    Returns
    -------
    str
        Markdown table row with a placeholder verdict.
    """
    metrics = run.metrics
    summary = metrics["summary"].set_index("method")["MAE_s"]
    method = run.info["model"]
    if method not in summary:
        method = B3
    paired = metrics["paired_vs_B0"].set_index("method").loc[method]
    baselines = summary[[B1, B2, B3]]
    best = baselines.idxmin()
    delta_best = summary[method] - baselines.min()
    anchor = run.info.get("anchor", FEED_ANCHOR)
    anchor_note = "" if anchor == FEED_ANCHOR else f" + {anchor} anchor"
    dwell = run.info.get("dwell", DEFAULT_DWELL)
    if dwell != DEFAULT_DWELL:
        anchor_note += f" + {dwell} dwell"
    anchor_free = run.metrics.get("anchor_free")
    anchor_free_note = ""
    if anchor_free is not None and len(anchor_free["summary"]):
        af = anchor_free["summary"].set_index("method")["MAE_s"]
        if method in af:
            anchor_free_note = f"; anchor-free {af[method]:.1f}"
    today = datetime.now(datetime_timezone.utc).date()
    return (
        f"| {log_id or 'E0NN'} | {today} | {git_hash} | {hypothesis or 'H?'} "
        f"| {run.info['split']}: {method}{anchor_note} ({run_id}) "
        f"| {summary[method]:.1f} (B0 {summary[B0]:.1f}{anchor_free_note}) "
        f"| {paired['delta_MAE_s']:+.1f} [{paired['ci_low_s']:+.1f}, "
        f"{paired['ci_high_s']:+.1f}] ({paired['relative_change']:+.1%}) "
        f"| {delta_best:+.1f} vs {best} | ? |"
    )


def write_run(
    run: StudyRun,
    run_dir: Path,
    *,
    provenance: dict[str, Any],
    figures: dict[str, Any] | None = None,
    plotlyjs: str | bool = "directory",
) -> None:
    """Persist a run to a new directory, never overwriting.

    Parameters
    ----------
    run : StudyRun
        Completed run.
    run_dir : pathlib.Path
        New output directory.
    provenance : dict
        CLI arguments, configuration, git state and dataset checksums.
    figures : dict of str to plotly.graph_objects.Figure or None, optional
        Written as ``figures/<name>.html``.
    plotlyjs : str or bool, default "directory"
        Plotly ``include_plotlyjs`` mode: ``"directory"`` shares one local
        ``plotly.min.js`` (offline), ``"cdn"`` keeps files small and renders in
        the MLflow UI, ``True`` embeds Plotly in every file.

    Raises
    ------
    FileExistsError
        If ``run_dir`` already exists.
    """
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(
        json.dumps(provenance | {"info": run.info}, indent=2, default=str)
    )
    run.predictions.to_csv(run_dir / "predictions.csv.gz", index=False)
    if not run.traversals.empty:
        run.traversals.to_csv(run_dir / "traversals.csv.gz", index=False)
    (run_dir / "metrics.json").write_text(
        json.dumps(_jsonable(run.metrics), indent=2, default=str)
    )
    run.audit.to_csv(run_dir / "audit.csv", index=False)
    if figures:
        figure_dir = run_dir / "figures"
        figure_dir.mkdir()
        for name, figure in figures.items():
            path = figure_dir / f"{name}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            # "directory" mode references plotly.min.js next to each file.
            mode = plotlyjs
            if plotlyjs == "directory" and path.parent != figure_dir:
                mode = "cdn"
            figure.write_html(path, full_html=True, include_plotlyjs=mode)
