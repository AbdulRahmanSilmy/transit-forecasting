"""Past-only experiments for recurring, signed segment delay changes."""

from __future__ import annotations

import json
import pickle
import platform
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from datetime import timezone as datetime_timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBRegressor

from .road_segment_congestion import _require_columns
from .road_segment_delay_congestion import DelayCongestionResult

TARGET = "delta_delay_seconds"
FEATURES = [
    "segment_id",
    "day_of_week",
    "minute_of_day",
    "time_sin",
    "time_cos",
    "week_sin",
    "week_cos",
]
_LEVELS = (
    ("calendar", ["segment_id", "day_of_week", "time_bin"]),
    ("segment_time", ["segment_id", "time_bin"]),
    ("segment", ["segment_id"]),
)


@dataclass(frozen=True)
class SegmentDelayData:
    """Prepared examples, exclusions, calendar coverage and segment identity.

    Attributes
    ----------
    examples, audit, coverage, segments : pandas.DataFrame
        Signed traversal targets with scheduled-only features, exclusion
        counts, service-day coverage (including missing days), and ID mapping.
    timezone : str
        Agency timezone used for calendar features.
    """

    examples: pd.DataFrame
    audit: pd.DataFrame
    coverage: pd.DataFrame
    segments: pd.DataFrame
    timezone: str
    time_bin_minutes: int = 30


@dataclass(frozen=True)
class DelayModelConfig:
    """Predeclared validation, support and robustness controls.

    Parameters
    ----------
    n_splits, block_days, gap_days, min_train_days : int
        Two weekly development blocks, one-day gaps, and at least 21 observed
        training service days by default.
    min_group_count, min_group_days : int
        Minimum calendar-group support before a broader fallback is used.
    tail_quantiles : tuple of float
        Training-residual bands for typical/tail diagnostics and sensitivity.
        The first band defines the development selection metric.
    random_state, n_jobs : int
        Reproducibility seed and bounded CPU thread count.
    candidates : tuple of dict
        Fixed XGBoost grid; never use held-out labels for early stopping.
    """

    n_splits: int = 2
    block_days: int = 7
    gap_days: int = 1
    min_train_days: int = 21
    min_group_count: int = 5
    min_group_days: int = 3
    tail_quantiles: tuple[float, ...] = (0.95, 0.99)
    random_state: int = 42
    n_jobs: int = 2
    candidates: tuple[dict[str, Any], ...] = field(
        default_factory=lambda: (
            {"n_estimators": 150, "max_depth": 3, "learning_rate": 0.05},
            {"n_estimators": 300, "max_depth": 3, "learning_rate": 0.05},
            {"n_estimators": 300, "max_depth": 5, "learning_rate": 0.05},
            {"n_estimators": 500, "max_depth": 5, "learning_rate": 0.03},
        )
    )

    def __post_init__(self) -> None:
        for name in (
            "n_splits",
            "block_days",
            "gap_days",
            "min_train_days",
            "min_group_count",
            "min_group_days",
            "n_jobs",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if (
            not self.tail_quantiles
            or any(not 0 < q < 1 for q in self.tail_quantiles)
            or len(set(self.tail_quantiles)) != len(self.tail_quantiles)
        ):
            raise ValueError("tail_quantiles must contain distinct values in (0, 1).")
        if not self.candidates:
            raise ValueError("At least one predeclared XGBoost candidate is required.")


@dataclass(frozen=True)
class DelayTimeSplit:
    """A whole-service-day split with label availability enforced.

    Attributes
    ----------
    name, phase : str
        Unique block name and ``cv`` or ``test``.
    fit_cutoff, start, end : pandas.Timestamp
        Exclusive training cutoff (UTC) and half-open held-out service dates.
    train_index, evaluation_index : numpy.ndarray
        Row indices into the prepared example frame.
    purged_train_rows : int
        Candidate training rows removed as part of a boundary/late-label trip.
    """

    name: str
    phase: str
    fit_cutoff: pd.Timestamp
    start: pd.Timestamp
    end: pd.Timestamp
    train_index: np.ndarray
    evaluation_index: np.ndarray
    purged_train_rows: int


def prepare_segment_delay_data(
    result: DelayCongestionResult,
    stop_times: pd.DataFrame,
    *,
    observation_start: str | pd.Timestamp,
    timezone: str = "America/Vancouver",
    time_bin_minutes: int = 30,
) -> SegmentDelayData:
    """Join each traversal to its own scheduled upstream arrival.

    Parameters
    ----------
    result : DelayCongestionResult
        Audited adjacent-stop delay proxies from the existing engine.
    stop_times : pandas.DataFrame
        Snapshot GTFS stop times, including arrival/departure and sequence.
    observation_start : str or pandas.Timestamp
        First raw trip-update publication. Recording-edge service dates are
        excluded, not presumed complete because they contain some traversals.
    timezone : str, optional
        Agency calendar timezone.
    time_bin_minutes : int, optional
        Baseline time-of-day resolution; must divide 1,440.

    Returns
    -------
    SegmentDelayData
        New frames; inputs are never modified. No endpoint delays or actual
        crossing times are used as model features.

    Raises
    ------
    ValueError
        For invalid schedules, duplicate traversals, or no usable full days.
    """
    if (
        isinstance(time_bin_minutes, bool)
        or not isinstance(time_bin_minutes, int)
        or time_bin_minutes <= 0
        or 1440 % time_bin_minutes
    ):
        raise ValueError("time_bin_minutes must be a positive divisor of 1440.")
    _require_columns(
        stop_times,
        ("trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"),
        "stop_times",
    )
    _require_columns(
        result.traversals,
        (
            "trip_id",
            "trip_instance_key",
            "trip_start_timestamp",
            "segment_id",
            "from_stop_id",
            "from_stop_sequence",
            "to_stop_sequence",
            "exclusion_reason",
            TARGET,
            "available_at",
        ),
        "traversals",
    )
    frame = result.traversals.loc[
        result.traversals["exclusion_reason"].eq("included")
    ].copy()
    if frame.empty:
        raise ValueError("No included traversal targets.")
    keys = ["trip_instance_key", "from_stop_sequence", "to_stop_sequence"]
    if frame.duplicated(keys).any():
        raise ValueError("Duplicate trip-instance/adjacent-stop traversals.")
    schedule = stop_times.loc[stop_times["trip_id"].isin(frame["trip_id"])].copy()
    if schedule.duplicated(["trip_id", "stop_sequence"]).any():
        raise ValueError("Scheduled trip/stop sequences must be unique.")
    for column in ("arrival_time", "departure_time"):
        schedule[column] = pd.to_timedelta(schedule[column], errors="raise")
    if schedule[["arrival_time", "departure_time"]].isna().any().any():
        raise ValueError("Selected schedule has missing arrival/departure times.")
    if (
        schedule["arrival_time"].lt(pd.Timedelta(0)).any()
        or schedule["departure_time"].lt(schedule["arrival_time"]).any()
    ):
        raise ValueError("Selected schedule has invalid arrival/departure times.")
    first = (
        schedule.sort_values("stop_sequence")
        .drop_duplicates("trip_id")
        .set_index("trip_id")["departure_time"]
    )
    upstream = schedule.rename(
        columns={
            "stop_id": "from_stop_id",
            "stop_sequence": "from_stop_sequence",
            "arrival_time": "scheduled_offset",
        }
    )
    frame = frame.merge(
        upstream[["trip_id", "from_stop_id", "from_stop_sequence", "scheduled_offset"]],
        how="left",
        on=["trip_id", "from_stop_id", "from_stop_sequence"],
        validate="many_to_one",
    )
    starts = pd.to_datetime(frame["trip_start_timestamp"], errors="raise")
    if starts.dt.tz is not None:
        starts = starts.dt.tz_convert(timezone).dt.tz_localize(None)
    first_offset = frame["trip_id"].map(first)
    day = pd.Timedelta(days=1)
    alignment = (starts - starts.dt.normalize()).eq(first_offset % day)
    frame["service_date"] = starts.dt.normalize() - (first_offset // day) * day
    origin = (frame["service_date"] + pd.Timedelta(hours=12)).dt.tz_localize(
        timezone
    ) - pd.Timedelta(hours=12)
    frame["scheduled_at"] = (origin + frame["scheduled_offset"]).dt.tz_convert("UTC")
    frame["available_at"] = pd.to_datetime(
        frame["available_at"], utc=True, errors="raise"
    )
    targets = pd.to_numeric(frame[TARGET], errors="raise")
    valid_target = targets.notna() & np.isfinite(targets)
    valid_label = frame["available_at"].notna()
    start_date = (
        pd.to_datetime(observation_start, utc=True)
        .tz_convert(timezone)
        .normalize()
        .tz_localize(None)
    )
    end_date = result.observation_end.tz_convert(timezone).normalize().tz_localize(None)
    if pd.isna(start_date) or pd.isna(end_date) or start_date >= end_date:
        raise ValueError("A valid multi-day recording interval is required.")
    complete = frame["service_date"].gt(start_date) & frame["service_date"].lt(end_date)
    reason = pd.Series("included", index=frame.index)
    for label, valid in (
        ("nonfinite_target", valid_target),
        ("missing_label_availability", valid_label),
        ("unmatched_schedule", frame["scheduled_offset"].notna()),
        ("misaligned_trip_start", alignment),
        ("partial_recording_edge", complete),
    ):
        reason.loc[reason.eq("included") & ~valid] = label
    audit = pd.concat(
        [
            result.audit,
            reason.value_counts()
            .rename_axis("reason")
            .reset_index(name="count")
            .assign(stage="modelling"),
        ],
        ignore_index=True,
    )
    excluded = int(reason.ne("included").sum())
    if excluded:
        warnings.warn(
            f"Excluded {excluded} modelling examples; inspect the modelling audit.",
            UserWarning,
            stacklevel=2,
        )
    frame = frame.loc[reason.eq("included")].copy()
    if frame.empty:
        raise ValueError("No usable complete-service-day modelling examples.")
    local = frame["scheduled_at"].dt.tz_convert(timezone)
    frame["minute_of_day"] = local.dt.hour * 60 + local.dt.minute + local.dt.second / 60
    frame["day_of_week"] = local.dt.dayofweek
    frame["time_bin"] = (frame["minute_of_day"] // time_bin_minutes).astype("int64")
    angle = 2 * np.pi * frame["minute_of_day"] / 1440
    week_angle = 2 * np.pi * (frame["day_of_week"] + frame["minute_of_day"] / 1440) / 7
    frame["time_sin"], frame["time_cos"] = np.sin(angle), np.cos(angle)
    frame["week_sin"], frame["week_cos"] = np.sin(week_angle), np.cos(week_angle)
    frame = frame.sort_values(
        ["service_date", "scheduled_at", "segment_id"]
    ).reset_index(drop=True)
    coverage = (
        frame.groupby("service_date")
        .agg(
            traversals=(TARGET, "size"),
            trips=("trip_instance_key", "nunique"),
            segments=("segment_id", "nunique"),
        )
        .reindex(pd.date_range(start_date + day, end_date - day), fill_value=0)
        .rename_axis("service_date")
        .reset_index()
    )
    mapping = result.segments.drop(columns="geometry", errors="ignore").copy()
    return SegmentDelayData(frame, audit, coverage, mapping, timezone, time_bin_minutes)


def make_segment_delay_splits(
    data: SegmentDelayData,
    config: DelayModelConfig,
) -> tuple[list[DelayTimeSplit], pd.DataFrame]:
    """Construct calendar-block splits and purge late/boundary training trips.

    Parameters
    ----------
    data : SegmentDelayData
        Prepared full-service-day examples and full calendar coverage.
    config : DelayModelConfig
        Block lengths, training gap and minimum observed training days.

    Returns
    -------
    splits : list of DelayTimeSplit
        Development blocks followed by the reserved final test.
    summary : pandas.DataFrame
        Date ranges, availability cutoffs, support, gap and purge counts.

    Raises
    ------
    ValueError
        If the requested blocks lack observed days or training support.
    """
    frame = data.examples
    if not frame.index.equals(pd.RangeIndex(len(frame))):
        raise ValueError("Examples must have a unique contiguous RangeIndex.")
    final_end = data.coverage["service_date"].max() + pd.Timedelta(days=1)
    first_start = final_end - pd.Timedelta(
        days=config.block_days * (config.n_splits + 1)
    )
    splits, rows = [], []
    for block in range(config.n_splits + 1):
        start = first_start + pd.Timedelta(days=block * config.block_days)
        end = start + pd.Timedelta(days=config.block_days)
        gap_start = start - pd.Timedelta(days=config.gap_days)
        cutoff = gap_start.tz_localize(data.timezone).tz_convert("UTC")
        candidate = frame["service_date"].lt(gap_start)
        bad_trips = frame.loc[
            frame["available_at"].ge(cutoff) | frame["scheduled_at"].ge(cutoff),
            "trip_instance_key",
        ]
        train = candidate & ~frame["trip_instance_key"].isin(bad_trips)
        evaluation = frame["service_date"].ge(start) & frame["service_date"].lt(end)
        train_days = frame.loc[train, "service_date"].nunique()
        eval_days = frame.loc[evaluation, "service_date"].nunique()
        if train_days < config.min_train_days or eval_days != config.block_days:
            raise ValueError(
                f"Insufficient coverage for block starting {start.date()}: "
                f"{train_days} observed training days (need {config.min_train_days}), "
                f"{eval_days}/{config.block_days} evaluation days. "
                "Inspect coverage; explicitly revise the design or snapshot."
            )
        if set(frame.loc[train, "trip_instance_key"]) & set(
            frame.loc[evaluation, "trip_instance_key"]
        ):
            raise ValueError("A trip instance crosses the train/evaluation boundary.")
        phase = "cv" if block < config.n_splits else "test"
        name = f"cv_{block + 1}" if phase == "cv" else "final_test"
        split = DelayTimeSplit(
            name,
            phase,
            cutoff,
            start,
            end,
            frame.index[train].to_numpy(),
            frame.index[evaluation].to_numpy(),
            int((candidate & ~train).sum()),
        )
        splits.append(split)
        rows.append(
            {
                "split": name,
                "phase": phase,
                "fit_cutoff_utc": cutoff,
                "train_start": frame.loc[train, "service_date"].min(),
                "train_end": frame.loc[train, "service_date"].max(),
                "evaluation_start": start,
                "evaluation_end_exclusive": end,
                "train_days": train_days,
                "train_rows": int(train.sum()),
                "evaluation_days": eval_days,
                "evaluation_rows": int(evaluation.sum()),
                "train_segments": frame.loc[train, "segment_id"].nunique(),
                "evaluation_segments": frame.loc[evaluation, "segment_id"].nunique(),
                "gap_rows": int(
                    (
                        frame["service_date"].ge(gap_start)
                        & frame["service_date"].lt(start)
                    ).sum()
                ),
                "purged_train_rows": split.purged_train_rows,
            }
        )
    return splits, pd.DataFrame(rows)


@dataclass
class CalendarAverage:
    """Training-fitted hierarchical location tables and fallback provenance.

    Attributes
    ----------
    tables : list of pandas.DataFrame
        Supported calendar, segment/time, and segment locations.
    global_value : float
        Training-only mean or median, used for unseen segments.
    """

    tables: list[pd.DataFrame]
    global_value: float

    @classmethod
    def fit(
        cls,
        frame: pd.DataFrame,
        values: np.ndarray,
        config: DelayModelConfig,
        *,
        statistic: str = "mean",
    ) -> CalendarAverage:
        """Fit location tables on supplied training examples.

        Parameters
        ----------
        frame : pandas.DataFrame
            Training examples with calendar columns and service dates.
        values : numpy.ndarray
            Finite signed targets, original or explicitly winsorised.
        config : DelayModelConfig
            Minimum observation and distinct-day support.
        statistic : {'mean', 'median'}, optional
            Arithmetic mean baseline or robust conditional reference.

        Returns
        -------
        CalendarAverage
            Tables ordered from most specific to broadest.
        """
        values = np.asarray(values, dtype=float)
        if (
            statistic not in ("mean", "median")
            or len(frame) != len(values)
            or not len(values)
            or not np.isfinite(values).all()
        ):
            raise ValueError(
                "Location fitting requires finite aligned targets and mean/median."
            )
        source = frame.assign(_value=values)
        tables = []
        for _, keys in _LEVELS:
            table = (
                source.groupby(keys, observed=True)
                .agg(
                    value=("_value", statistic),
                    count=("_value", "size"),
                    days=("service_date", "nunique"),
                )
                .reset_index()
            )
            table = table.loc[
                table["count"].ge(config.min_group_count)
                & table["days"].ge(config.min_group_days)
            ]
            tables.append(table)
        global_value = float(
            np.mean(values) if statistic == "mean" else np.median(values)
        )
        return cls(tables, global_value)

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Predict locations with explicit fallback labels.

        Parameters
        ----------
        frame : pandas.DataFrame
            Examples with segment and calendar columns.

        Returns
        -------
        values, levels : numpy.ndarray
            Locations and the level used, including ``global`` for cold starts.
        """
        values = np.full(len(frame), self.global_value)
        levels = np.full(len(frame), "global", dtype=object)
        for (name, keys), table in reversed(list(zip(_LEVELS, self.tables))):
            match = frame[keys].merge(
                table[keys + ["value"]], on=keys, how="left", validate="many_to_one"
            )
            valid = match["value"].notna().to_numpy()
            values[valid] = match.loc[valid, "value"].to_numpy()
            levels[valid] = name
        return values, levels


@dataclass
class ResidualBands:
    """Conditional training reference with out-of-day residual thresholds.

    Attributes
    ----------
    reference : CalendarAverage
        Conditional median fitted only on the current training partition.
    thresholds : dict
        Segment-specific absolute-residual quantiles for each configured band.
    global_thresholds : dict
        Broader training-only quantiles for unsupported/unseen segments.
    diagnostics : pandas.DataFrame
        Segment support and threshold provenance, including degenerate bands.
    """

    reference: CalendarAverage
    thresholds: dict[float, pd.Series]
    global_thresholds: dict[float, float]
    diagnostics: pd.DataFrame

    @classmethod
    def fit(cls, frame: pd.DataFrame, config: DelayModelConfig) -> ResidualBands:
        """Fit bands using leave-one-training-service-day-out residuals.

        Parameters
        ----------
        frame : pandas.DataFrame
            Current training partition only.
        config : DelayModelConfig
            Support and residual quantiles.

        Returns
        -------
        ResidualBands
            Frozen reference, thresholds and auditable support.

        Raises
        ------
        ValueError
            If fewer than two training service days are available.
        """
        if frame["service_date"].nunique() < 2:
            raise ValueError("Residual bands need at least two training service days.")
        y = frame[TARGET].to_numpy(float)
        reference = CalendarAverage.fit(frame, y, config, statistic="median")
        out_of_day = np.empty(len(frame))
        days = frame["service_date"].to_numpy()
        for day in np.unique(days):
            own = days == day
            earlier = frame.iloc[np.flatnonzero(~own)]
            local = CalendarAverage.fit(earlier, y[~own], config, statistic="median")
            out_of_day[own] = local.predict(frame.iloc[np.flatnonzero(own)])[0]
        residuals = frame[["segment_id", "service_date"]].assign(
            residual=np.abs(y - out_of_day)
        )
        support = residuals.groupby("segment_id").agg(
            count=("residual", "size"), days=("service_date", "nunique")
        )
        eligible = support["count"].ge(config.min_group_count) & support["days"].ge(
            config.min_group_days
        )
        thresholds, global_thresholds, diagnostics = {}, {}, []
        for q in config.tail_quantiles:
            global_thresholds[q] = float(residuals["residual"].quantile(q))
            thresholds[q] = (
                residuals.groupby("segment_id")["residual"].quantile(q).loc[eligible]
            )
            row = support.copy()
            row["quantile"] = q
            row["threshold_seconds"] = (
                row.index.to_series().map(thresholds[q]).fillna(global_thresholds[q])
            )
            row["threshold_level"] = np.where(eligible, "segment", "global")
            row["degenerate_band"] = row["threshold_seconds"].eq(0)
            diagnostics.append(row.reset_index())
        return cls(
            reference,
            thresholds,
            global_thresholds,
            pd.concat(diagnostics, ignore_index=True),
        )

    def limits(
        self, frame: pd.DataFrame, quantile: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return conditional centers, radii and radius provenance.

        Parameters
        ----------
        frame : pandas.DataFrame
            Training or held-out calendar examples.
        quantile : float
            A fitted quantile.

        Returns
        -------
        centers, radii, levels : numpy.ndarray
            Frozen training-reference bounds and threshold fallback level.
        """
        center = self.reference.predict(frame)[0]
        radius = frame["segment_id"].map(self.thresholds[quantile])
        levels = np.where(radius.notna(), "segment", "global")
        return (
            center,
            radius.fillna(self.global_thresholds[quantile]).to_numpy(float),
            levels,
        )


@dataclass
class SegmentDelayModel:
    """Fitted advance-calendar XGBoost pipeline and training-only comparators.

    Attributes
    ----------
    pipeline : sklearn.pipeline.Pipeline
        Frozen preprocessing and absolute-error XGBoost estimator.
    averages : dict
        Ordinary calendar, robust calendar and robust segment-only models.
    bands : ResidualBands
        Shared diagnostic masks, not an anomaly detector at prediction time.
    config : DelayModelConfig
        Exact experiment controls.
    """

    pipeline: Pipeline
    averages: dict[str, CalendarAverage]
    bands: ResidualBands
    config: DelayModelConfig

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Predict delay changes from scheduled calendar features only.

        Parameters
        ----------
        frame : pandas.DataFrame
            Prepared examples, without any requirement for target values.

        Returns
        -------
        pandas.DataFrame
            Signed seconds for all comparators and their fallback levels.
        """
        values: dict[str, Any] = {
            "zero": np.zeros(len(frame)),
            "xgboost": self.pipeline.predict(frame[FEATURES]),
        }
        for name, average in self.averages.items():
            values[name], values[f"{name}_level"] = average.predict(frame)
        return pd.DataFrame(values, index=frame.index)


def _fit_model(
    frame: pd.DataFrame,
    config: DelayModelConfig,
    parameters: dict[str, Any],
    bands: ResidualBands,
) -> SegmentDelayModel:
    """Fit the frozen comparators and L1 pipeline on a training partition.

    Parameters
    ----------
    frame : pandas.DataFrame
        Training examples only.
    config : DelayModelConfig
        Support and fitting controls.
    parameters : dict
        One predeclared candidate's tree settings.
    bands : ResidualBands
        Bands fitted to the same training partition.

    Returns
    -------
    SegmentDelayModel
        Fitted calendar predictors and comparators.
    """
    y = frame[TARGET].to_numpy(float)
    averages = {"calendar_mean": CalendarAverage.fit(frame, y, config)}
    for q in config.tail_quantiles:
        center, radius, _ = bands.limits(frame, q)
        robust_y = np.clip(y, center - radius, center + radius)
        averages[f"robust_mean_q{q:g}"] = CalendarAverage.fit(frame, robust_y, config)
        segment_mean = CalendarAverage.fit(frame, robust_y, config)
        segment_mean.tables = [
            table.iloc[:0] if i < 2 else table
            for i, table in enumerate(segment_mean.tables)
        ]
        averages[f"segment_mean_q{q:g}"] = segment_mean
    preprocessing = ColumnTransformer(
        [
            (
                "categories",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                ["segment_id", "day_of_week"],
            ),
            ("clock", "passthrough", FEATURES[2:]),
        ],
        remainder="drop",
    )
    estimator = XGBRegressor(
        objective="reg:absoluteerror",
        tree_method="hist",
        device="cpu",
        random_state=config.random_state,
        n_jobs=config.n_jobs,
        min_child_weight=10,
        reg_lambda=10,
        reg_alpha=1,
        subsample=1,
        colsample_bytree=1,
        **parameters,
    )
    pipeline = Pipeline([("features", preprocessing), ("model", estimator)])
    pipeline.fit(frame[FEATURES], y)
    return SegmentDelayModel(pipeline, averages, bands, config)


def _prediction_rows(
    frame: pd.DataFrame,
    model: SegmentDelayModel,
    split: DelayTimeSplit,
    candidate: str,
) -> pd.DataFrame:
    """Attach shared retrospective evaluation masks to model predictions.

    Parameters
    ----------
    frame : pandas.DataFrame
        Held-out examples, with unchanged outcomes.
    model : SegmentDelayModel
        A model fitted on the split's training partition.
    split : DelayTimeSplit
        Split provenance.
    candidate : str
        Candidate identifier.

    Returns
    -------
    pandas.DataFrame
        Predictions, original targets and frozen-band masks.
    """
    columns = [
        "trip_instance_key",
        "service_date",
        "segment_id",
        "scheduled_at",
        TARGET,
    ]
    output = pd.concat([frame[columns], model.predict(frame)], axis=1).reset_index(
        drop=True
    )
    output["split"], output["phase"], output["candidate"] = (
        split.name,
        split.phase,
        candidate,
    )
    for q in model.config.tail_quantiles:
        center, radius, level = model.bands.limits(frame, q)
        output[f"typical_q{q:g}"] = (
            np.abs(frame[TARGET].to_numpy(float) - center) <= radius
        )
        output[f"radius_q{q:g}"] = radius
        output[f"threshold_level_q{q:g}"] = level
    output["cold_start"] = (
        ~frame["segment_id"].isin(model.bands.diagnostics["segment_id"]).to_numpy()
    )
    return output


def segment_delay_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    """Score common, unchanged outcomes for every comparator and band.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Output predictions with target, segment, split, and typical flags.

    Returns
    -------
    pandas.DataFrame
        Count, coverage, MAE, median AE, RMSE, R-squared, macro-segment MAE,
        and reasons for undefined scores, in seconds (except R-squared).
    """
    models = [
        c
        for c in predictions
        if c in ("zero", "xgboost", "calendar_mean")
        or c.startswith(("robust_mean_q", "segment_mean_q"))
        and not c.endswith("_level")
    ]
    cohorts = {"all": np.ones(len(predictions), dtype=bool)}
    for column in predictions:
        if column.startswith("typical_q"):
            cohorts[column] = predictions[column].to_numpy(bool)
            cohorts[column.replace("typical_", "tail_")] = ~cohorts[column]
    cohorts["cold_start"] = predictions["cold_start"].to_numpy(bool)
    cohorts["known_segment"] = ~cohorts["cold_start"]
    rows = []
    for cohort, mask in cohorts.items():
        subset = predictions.loc[mask]
        y = subset[TARGET].to_numpy(float)
        for name in models:
            estimate = subset[name].to_numpy(float)
            if not np.isfinite(estimate).all() or not np.isfinite(y).all():
                raise ValueError(
                    "Metrics require finite predictions and original outcomes."
                )
            error = estimate - y
            absolute = np.abs(error)
            denominator = float(np.sum((y - y.mean()) ** 2)) if len(y) else 0
            reason = (
                "empty_cohort"
                if not len(y)
                else (
                    "fewer_than_two_outcomes"
                    if len(y) < 2
                    else "constant_target" if denominator == 0 else ""
                )
            )
            rows.append(
                {
                    "cohort": cohort,
                    "model": name,
                    "n": len(y),
                    "coverage": (
                        len(y) / len(predictions) if len(predictions) else np.nan
                    ),
                    "segments": subset["segment_id"].nunique(),
                    "mae_seconds": float(absolute.mean()) if len(y) else np.nan,
                    "median_ae_seconds": (
                        float(np.median(absolute)) if len(y) else np.nan
                    ),
                    "rmse_seconds": (
                        float(np.sqrt(np.mean(error**2))) if len(y) else np.nan
                    ),
                    "r2": (
                        1 - float(np.sum(error**2)) / denominator
                        if not reason
                        else np.nan
                    ),
                    "r2_undefined_reason": reason,
                    "macro_segment_mae_seconds": (
                        subset.assign(ae=absolute)
                        .groupby("segment_id")["ae"]
                        .mean()
                        .mean()
                        if len(y)
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


@dataclass
class SegmentDelayExperiment:
    """Persistable experiment, including untouched final-test predictions.

    Attributes
    ----------
    model : SegmentDelayModel
        Selected model refit only on eligible pre-test history.
    splits, tuning, metrics, predictions, band_diagnostics : pandas.DataFrame
        Inspectable chronology, tuning scores, all metrics, out-of-time
        predictions, and training threshold/support diagnostics.
    selected_candidate : str
        Candidate selected without final-test outcomes.
    """

    model: SegmentDelayModel
    splits: pd.DataFrame
    tuning: pd.DataFrame
    metrics: pd.DataFrame
    predictions: pd.DataFrame
    band_diagnostics: pd.DataFrame
    selected_candidate: str


def run_segment_delay_experiment(
    data: SegmentDelayData,
    config: DelayModelConfig | None = None,
) -> SegmentDelayExperiment:
    """Tune on blocked development history, then assess one final test.

    Parameters
    ----------
    data : SegmentDelayData
        Prepared traversal observations.
    config : DelayModelConfig or None, optional
        Predeclared settings. Defaults to two weekly development folds.

    Returns
    -------
    SegmentDelayExperiment
        Fitted model, CV tuning results, holdout metrics, and diagnostics.

    Notes
    -----
    Selection minimises mean development-fold typical-range macro-segment
    MAE. CV scores are tuning estimates; only final-test scores confirm
    performance. Final test data are never passed to fitting or selection.
    """
    config = config or DelayModelConfig()
    splits, summary = make_segment_delay_splits(data, config)
    tuning_rows, development, diagnostics = [], [], []
    q = config.tail_quantiles[0]
    for split in splits[:-1]:
        training = data.examples.loc[split.train_index]
        evaluation = data.examples.loc[split.evaluation_index]
        bands = ResidualBands.fit(training, config)
        diagnostics.append(bands.diagnostics.assign(split=split.name))
        for number, parameters in enumerate(config.candidates):
            name = f"candidate_{number + 1}"
            model = _fit_model(training, config, parameters, bands)
            predictions = _prediction_rows(evaluation, model, split, name)
            scores = segment_delay_metrics(predictions)
            score = scores.loc[
                scores["model"].eq("xgboost") & scores["cohort"].eq(f"typical_q{q:g}"),
                "macro_segment_mae_seconds",
            ].iloc[0]
            if not np.isfinite(score):
                raise ValueError(
                    "Model selection has no supported typical-range cohort."
                )
            all_score = scores.loc[
                scores["model"].eq("xgboost") & scores["cohort"].eq("all")
            ].iloc[0]
            typical = scores.loc[
                scores["model"].eq("xgboost") & scores["cohort"].eq(f"typical_q{q:g}")
            ].iloc[0]
            tuning_rows.append(
                {
                    "candidate": name,
                    "split": split.name,
                    "selection_mae_seconds": score,
                    "all_mae_seconds": all_score["mae_seconds"],
                    "all_rmse_seconds": all_score["rmse_seconds"],
                    "typical_coverage": typical["coverage"],
                    **parameters,
                }
            )
            development.append(predictions)
    tuning = pd.DataFrame(tuning_rows)
    selected = (
        tuning.groupby("candidate", sort=True)["selection_mae_seconds"].mean().idxmin()
    )
    number = int(selected.removeprefix("candidate_")) - 1
    final_split = splits[-1]
    training = data.examples.loc[final_split.train_index]
    final_bands = ResidualBands.fit(training, config)
    diagnostics.append(final_bands.diagnostics.assign(split=final_split.name))
    final_model = _fit_model(training, config, config.candidates[number], final_bands)
    final_predictions = _prediction_rows(
        data.examples.loc[final_split.evaluation_index],
        final_model,
        final_split,
        selected,
    )
    predictions = pd.concat(
        [p for p in development if p["candidate"].iloc[0] == selected]
        + [final_predictions],
        ignore_index=True,
    )
    metrics = []
    for split_name, group in predictions.groupby("split", sort=False):
        metrics.append(
            segment_delay_metrics(group).assign(
                split=split_name, phase=group["phase"].iloc[0]
            )
        )
    return SegmentDelayExperiment(
        final_model,
        summary,
        tuning,
        pd.concat(metrics, ignore_index=True),
        predictions,
        pd.concat(diagnostics, ignore_index=True),
        selected,
    )


def summarize_segment_delay_uplift(
    predictions: pd.DataFrame,
    *,
    quantile: float = 0.95,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare paired typical-range errors by segment and service day.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Selected-candidate out-of-time predictions.
    quantile : float, optional
        Fitted typical-range band to inspect.

    Returns
    -------
    segment, daily : pandas.DataFrame
        Support, MAE and paired improvement versus each comparator. Positive
        improvement favours XGBoost; undefined percentages have explicit reasons.
    """
    frame = predictions.loc[predictions[f"typical_q{quantile:g}"]].copy()
    comparators = [
        "calendar_mean",
        f"robust_mean_q{quantile:g}",
        f"segment_mean_q{quantile:g}",
    ]
    for name in ["xgboost", *comparators]:
        frame[f"{name}_ae"] = (frame[name] - frame[TARGET]).abs()
    results = []
    for group_keys in (
        ["phase", "split", "segment_id"],
        ["phase", "split", "service_date"],
    ):
        grouped = frame.groupby(group_keys, observed=True)
        table = grouped.agg(
            n=(TARGET, "size"), service_days=("service_date", "nunique")
        )
        for name in ["xgboost", *comparators]:
            table[f"{name}_mae"] = grouped[f"{name}_ae"].mean()
        for name in comparators:
            denominator = table[f"{name}_mae"]
            table[f"improvement_vs_{name}_seconds"] = denominator - table["xgboost_mae"]
            table[f"improvement_vs_{name}_percent"] = (
                100
                * table[f"improvement_vs_{name}_seconds"]
                / denominator.where(denominator.ne(0))
            )
            table[f"improvement_vs_{name}_undefined_reason"] = np.where(
                denominator.eq(0), "zero_baseline_mae", ""
            )
        results.append(table.reset_index())
    return results[0], results[1]


def save_segment_delay_experiment(
    experiment: SegmentDelayExperiment,
    data: SegmentDelayData,
    output_root: str | Path,
    *,
    dataset_metadata: dict[str, Any],
    run_id: str | None = None,
) -> Path:
    """Save a non-overwriting local model bundle and auditable result tables.

    Parameters
    ----------
    experiment : SegmentDelayExperiment
        Completed experiment.
    data : SegmentDelayData
        Prepared examples and provenance.
    output_root : str or pathlib.Path
        Ignored local results directory.
    dataset_metadata : dict
        Snapshot manifest including identity and checksums.
    run_id : str or None, optional
        New directory name, or a generated UTC timestamp.

    Returns
    -------
    pathlib.Path
        Created run directory.

    Raises
    ------
    ValueError
        For unsafe run names.
    FileExistsError
        If the requested run already exists.

    Notes
    -----
    The pickle bundle must only be loaded from a trusted source. It contains
    the pre-test model, not a model retrained using the final-test outcomes.
    """
    if run_id is None:
        run_id = datetime.now(datetime_timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if (
        not run_id
        or run_id in (".", "..")
        or Path(run_id).name != run_id
        or "\\" in run_id
    ):
        raise ValueError("run_id must be a single safe directory name.")
    destination = Path(output_root) / run_id
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "model_bundle.pkl").open("wb") as stream:
        pickle.dump(experiment.model, stream, protocol=pickle.HIGHEST_PROTOCOL)
    tables = {
        "splits": experiment.splits,
        "tuning": experiment.tuning,
        "metrics": experiment.metrics,
        "predictions": experiment.predictions,
        "band_diagnostics": experiment.band_diagnostics,
        "segments": data.segments,
        "modelling_audit": data.audit,
        "coverage": data.coverage,
    }
    segment, daily = summarize_segment_delay_uplift(
        experiment.predictions,
        quantile=experiment.model.config.tail_quantiles[0],
    )
    tables.update(segment_uplift=segment, daily_uplift=daily)
    for name, table in tables.items():
        table.to_csv(destination / f"{name}.csv", index=False)
    manifest = {
        "dataset": dataset_metadata,
        "timezone": data.timezone,
        "time_bin_minutes": data.time_bin_minutes,
        "features": FEATURES,
        "target": TARGET,
        "target_units": "seconds",
        "config": asdict(experiment.model.config),
        "selected_candidate": experiment.selected_candidate,
        "selection_metric": "mean CV typical-range macro-segment MAE",
        "python": platform.python_version(),
        "libraries": {
            name: version(name)
            for name in ("numpy", "pandas", "scikit-learn", "xgboost")
        },
        "model_fit_split": "final_test (training portion only)",
        "trusted_pickle_only": True,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return destination
