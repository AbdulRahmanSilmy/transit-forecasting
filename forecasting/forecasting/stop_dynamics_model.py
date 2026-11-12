"""Fit retrospective, same-journey delay dynamics for one incoming segment."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from .road_segment_congestion import _require_columns
from .stop_dynamics_eda import SegmentTimingEDA, StopDynamicsEDA

_PAIR_KEYS = [
    "trip_instance_key",
    "stop_id",
    "stop_sequence",
    "from_stop_id",
    "from_stop_sequence",
]


@dataclass(frozen=True)
class StopDynamicsModel:
    """Fitted equation and audited chronological evaluation.

    Attributes
    ----------
    segment : pandas.Series
        Exact directed, shape-specific segment metadata.
    coefficients : pandas.Series
        Dimensionless ``alpha_d``, ``alpha_t``, ``alpha_w`` and signed
        ``constant_seconds``. The equation subtracts the constant.
    hourly_baselines : pandas.DataFrame
        Training medians in seconds indexed by local upstream-arrival hour.
        Unobserved hours explicitly use the global training medians; support
        counts and ``baseline_source`` identify these fallback hours.
    observations : pandas.DataFrame
        Eligible dated visits with training/holdout split, baselines, duration
        deviations, predictions and signed residuals, all in seconds.
    metrics : pandas.DataFrame
        Training and holdout count, MAE, RMSE and R-squared for the equation,
        upstream-delay persistence and a training-mean delay-change baseline.
        Undefined R-squared values have an explicit reason.
    audit : pandas.DataFrame
        Mutually exclusive modelling eligibility reasons and their counts.
    holdout_start : pandas.Timestamp
        Local midnight beginning the first held-out observed arrival day.
    baseline_timezone : str
        Timezone used for arrival-day splitting and upstream-arrival hours.
    """

    segment: pd.Series
    coefficients: pd.Series
    hourly_baselines: pd.DataFrame
    observations: pd.DataFrame
    metrics: pd.DataFrame
    audit: pd.DataFrame
    holdout_start: pd.Timestamp
    baseline_timezone: str


def fit_stop_dynamics_model(
    analysis: StopDynamicsEDA,
    timing: SegmentTimingEDA,
    *,
    holdout_days: int = 7,
    baseline_timezone: str = "America/Vancouver",
) -> StopDynamicsModel:
    """Fit delay propagation with training-only hourly duration baselines.

    Fit ordinary least squares with an unconstrained signed intercept:
    ``D_current = alpha_d * D_upstream
    + alpha_t * (traversal - B_traversal(hour))
    + alpha_w * (upstream_dwell - B_dwell(hour)) - constant``.
    All delays and durations are in seconds. The upstream delay is from the
    same dated journey's exact scheduled predecessor, not the previous bus.

    Use only visits with valid paired delays, traversal and upstream dwell.
    Hold out the last ``holdout_days`` observed local destination-arrival days
    in their entirety. Training rows whose final endpoint publications reach
    the holdout boundary, or whose journey also occurs in holdout, are excluded.
    Fit baselines and coefficients on the remaining training rows only.
    EDA full-snapshot medians and time-bin aggregates are never fitting inputs.
    Inputs are not modified.

    This is a retrospective equation, not a point-in-time forecast: completed
    traversal includes the target arrival timestamp. Final feed estimates may
    encode schedule arithmetic, so a good fit is not evidence of traffic
    causality or independent measurement accuracy.

    Parameters
    ----------
    analysis : StopDynamicsEDA
        Prepared latest paired delays for exactly one incoming segment.
    timing : SegmentTimingEDA
        Durations prepared from the same analysis.
    holdout_days : int, default 7
        Number of latest observed local arrival days reserved for evaluation.
    baseline_timezone : str, default "America/Vancouver"
        Timezone for day boundaries and hourly baselines.

    Returns
    -------
    StopDynamicsModel
        Coefficients, frozen baselines, predictions, metrics and exclusions.

    Raises
    ------
    ValueError
        If inputs disagree, required fields are missing, holdout_days is
        invalid, there are too few observed days, or training predictors do
        not identify all three slopes and the intercept.

    Warns
    -----
    UserWarning
        If visits are excluded or a holdout hour has no training observations.
        Missing-hour baselines use explicitly labelled global training medians.
    """
    if (
        isinstance(holdout_days, bool)
        or not isinstance(holdout_days, int)
        or holdout_days < 1
    ):
        raise ValueError("holdout_days must be a positive integer.")
    identity = [
        "segment_id",
        "trip_direction_id",
        "shape_id",
        "from_stop_id",
        "to_stop_id",
    ]
    if not analysis.segment[identity].equals(timing.segment[identity]):
        raise ValueError("Delay and timing inputs must describe the same segment.")
    _require_columns(
        analysis.delay_changes,
        _PAIR_KEYS
        + ["upstream_arrival_delay", "upstream_feed_timestamp", "exclusion_reason"],
        "delay_changes",
    )
    _require_columns(
        timing.observations,
        _PAIR_KEYS
        + [
            "arrival_time",
            "arrival_delay",
            "feed_timestamp",
            "upstream_arrival_time",
            "traversal_seconds",
            "dwell_seconds",
            "traversal_exclusion_reason",
            "dwell_exclusion_reason",
        ],
        "timing.observations",
    )
    pairs = analysis.delay_changes[
        _PAIR_KEYS
        + ["upstream_arrival_delay", "upstream_feed_timestamp", "exclusion_reason"]
    ].rename(columns={"exclusion_reason": "delay_exclusion_reason"})
    frame = timing.observations.merge(
        pairs, on=_PAIR_KEYS, how="left", validate="one_to_one"
    )
    frame["model_exclusion_reason"] = "included"
    for prefix in ("delay", "traversal", "dwell"):
        reason = frame[f"{prefix}_exclusion_reason"].fillna("missing_pair")
        mask = frame["model_exclusion_reason"].eq("included") & reason.ne("included")
        frame.loc[mask, "model_exclusion_reason"] = prefix + "_" + reason.loc[mask]
    numeric = [
        "arrival_delay",
        "upstream_arrival_delay",
        "traversal_seconds",
        "dwell_seconds",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    finite = np.isfinite(frame[numeric].to_numpy(dtype=float, na_value=np.nan)).all(
        axis=1
    )
    frame.loc[
        frame["model_exclusion_reason"].eq("included") & ~finite,
        "model_exclusion_reason",
    ] = "missing_or_nonfinite_measurement"
    for column in (
        "arrival_time",
        "upstream_arrival_time",
        "feed_timestamp",
        "upstream_feed_timestamp",
    ):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
        frame.loc[
            frame["model_exclusion_reason"].eq("included") & frame[column].isna(),
            "model_exclusion_reason",
        ] = (
            "missing_" + column
        )
    frame["arrival_day"] = (
        frame["arrival_time"].dt.tz_convert(baseline_timezone).dt.normalize()
    )
    eligible = frame["model_exclusion_reason"].eq("included")
    days = frame.loc[eligible, "arrival_day"].drop_duplicates().sort_values()
    if len(days) <= holdout_days:
        raise ValueError(
            "Need training arrival days in addition to the requested holdout days."
        )
    holdout_start = days.iloc[-holdout_days]
    in_holdout = frame["arrival_day"].ge(holdout_start)
    late_publication = frame["feed_timestamp"].ge(holdout_start) | frame[
        "upstream_feed_timestamp"
    ].ge(holdout_start)
    frame.loc[eligible & ~in_holdout & late_publication, "model_exclusion_reason"] = (
        "training_publication_reaches_holdout"
    )
    holdout_journeys = frame.loc[eligible & in_holdout, "trip_instance_key"]
    frame.loc[
        frame["model_exclusion_reason"].eq("included")
        & ~in_holdout
        & frame["trip_instance_key"].isin(holdout_journeys),
        "model_exclusion_reason",
    ] = "training_journey_in_holdout"
    audit = (
        frame["model_exclusion_reason"]
        .value_counts()
        .rename_axis("reason")
        .reset_index(name="count")
    )
    if audit["reason"].ne("included").any():
        warnings.warn(
            "Some model visits were excluded; inspect model audit.",
            UserWarning,
            stacklevel=2,
        )
    observations = frame.loc[frame["model_exclusion_reason"].eq("included")].copy()
    observations["split"] = np.where(
        observations["arrival_day"].ge(holdout_start), "holdout", "train"
    )
    observations["baseline_hour"] = (
        observations["upstream_arrival_time"].dt.tz_convert(baseline_timezone).dt.hour
    )
    train = observations.loc[observations["split"].eq("train")]
    if len(train) < 4:
        raise ValueError(
            "Need at least four eligible training visits to identify the equation."
        )
    baselines = (
        train.groupby("baseline_hour")
        .agg(
            baseline_traversal_seconds=("traversal_seconds", "median"),
            baseline_dwell_seconds=("dwell_seconds", "median"),
            training_count=("arrival_delay", "size"),
        )
        .reindex(pd.Index(range(24), name="baseline_hour"))
    )
    baselines["training_count"] = baselines["training_count"].fillna(0).astype("int64")
    baselines["baseline_source"] = np.where(
        baselines["training_count"].gt(0),
        "hourly_training_median",
        "global_training_median",
    )
    for metric in ("traversal", "dwell"):
        baselines[f"baseline_{metric}_seconds"] = baselines[
            f"baseline_{metric}_seconds"
        ].fillna(train[f"{metric}_seconds"].median())
    observations = observations.join(
        baselines, on="baseline_hour", validate="many_to_one"
    )
    if (
        observations.loc[observations["split"].eq("holdout"), "training_count"]
        .eq(0)
        .any()
    ):
        warnings.warn(
            "Holdout includes unobserved training hours; baseline_source identifies "
            "global-training-median fallbacks.",
            UserWarning,
            stacklevel=2,
        )
    for metric in ("traversal", "dwell"):
        observations[f"{metric}_deviation_seconds"] = (
            observations[f"{metric}_seconds"]
            - observations[f"baseline_{metric}_seconds"]
        )
    features = [
        "upstream_arrival_delay",
        "traversal_deviation_seconds",
        "dwell_deviation_seconds",
    ]
    training = observations["split"].eq("train")
    x_train = observations.loc[training, features].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(x_train)), x_train])
    if np.linalg.matrix_rank(design) < 4:
        raise ValueError(
            "Training predictors are rank deficient; the equation is not identifiable."
        )
    model = LinearRegression().fit(
        x_train, observations.loc[training, "arrival_delay"].to_numpy(dtype=float)
    )
    coefficients = pd.Series(
        [*model.coef_, -float(model.intercept_)],
        index=["alpha_d", "alpha_t", "alpha_w", "constant_seconds"],
        name="value",
    )
    observations["predicted_delay_seconds"] = model.predict(
        observations[features].to_numpy(dtype=float)
    )
    observations["persistence_delay_seconds"] = observations["upstream_arrival_delay"]
    mean_change = (
        observations.loc[training, "arrival_delay"]
        - observations.loc[training, "upstream_arrival_delay"]
    ).mean()
    observations["mean_change_delay_seconds"] = (
        observations["upstream_arrival_delay"] + mean_change
    )
    observations["residual_seconds"] = (
        observations["arrival_delay"] - observations["predicted_delay_seconds"]
    )
    rows = []
    for split, subset in observations.groupby("split", sort=False):
        y = subset["arrival_delay"].to_numpy(dtype=float)
        denominator = float(np.sum((y - y.mean()) ** 2))
        reason = (
            "fewer_than_two_outcomes"
            if len(y) < 2
            else "constant_target" if denominator == 0 else ""
        )
        for name, column in (
            ("equation", "predicted_delay_seconds"),
            ("persistence", "persistence_delay_seconds"),
            ("mean_change", "mean_change_delay_seconds"),
        ):
            error = subset[column].to_numpy(dtype=float) - y
            rows.append(
                {
                    "split": split,
                    "model": name,
                    "n": len(y),
                    "mae_seconds": float(np.abs(error).mean()),
                    "rmse_seconds": float(np.sqrt(np.mean(error**2))),
                    "r2": (
                        np.nan if reason else 1 - float(np.sum(error**2)) / denominator
                    ),
                    "r2_undefined_reason": reason,
                }
            )
    return StopDynamicsModel(
        segment=analysis.segment.copy(),
        coefficients=coefficients,
        hourly_baselines=baselines,
        observations=observations.reset_index(drop=True),
        metrics=pd.DataFrame(rows),
        audit=audit,
        holdout_start=holdout_start,
        baseline_timezone=baseline_timezone,
    )
