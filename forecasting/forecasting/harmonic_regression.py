"""Calendar-only harmonic regression with bounded residual influence."""

from __future__ import annotations

import json
import pickle
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, TransformerMixin
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import Lasso
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_array, check_is_fitted, check_X_y


class CalendarHarmonics(TransformerMixin, BaseEstimator):
    """Transform timezone-aware departure times into local calendar harmonics.

    Parameters
    ----------
    daily_order, weekly_order : int, default 4, 2
        Numbers of daily and weekly sine/cosine pairs. Weekly order is limited
        to three to avoid redundant frequencies with daily-weekly interactions.
        Either order may be zero, but not both.
    interactions : bool, default True
        Include products of daily and weekly terms, allowing intraday shape to
        vary smoothly across weekdays rather than fitting seven separate curves.
    timezone : str, default "America/Vancouver"
        Calendar timezone. UTC instants are converted before extracting phases;
        repeated DST wall times have identical features.
    timestamp_column : str, default "upstream_departure_time"
        Sole input column consumed. No elapsed-time trend, target, lag, dwell,
        arrival, or delay feature is used.
    """

    def __init__(
        self,
        daily_order: int = 4,
        weekly_order: int = 2,
        interactions: bool = True,
        timezone: str = "America/Vancouver",
        timestamp_column: str = "upstream_departure_time",
    ) -> None:
        self.daily_order = daily_order
        self.weekly_order = weekly_order
        self.interactions = interactions
        self.timezone = timezone
        self.timestamp_column = timestamp_column

    def _clock(self, X: pd.DataFrame) -> pd.Series:
        """Validate input and extract local wall time.

        Parameters
        ----------
        X : pandas.DataFrame
            Departure timestamps.

        Returns
        -------
        pandas.Series
            Naive local wall times.
        """
        if not isinstance(X, pd.DataFrame) or self.timestamp_column not in X:
            raise ValueError(f"X must be a DataFrame with {self.timestamp_column!r}.")
        times = X[self.timestamp_column]
        if not isinstance(times.dtype, pd.DatetimeTZDtype) or times.isna().any():
            raise ValueError(
                "Departure timestamps must be timezone-aware and nonmissing."
            )
        return times.dt.tz_convert(self.timezone).dt.tz_localize(None)

    def fit(self, X: pd.DataFrame, y: object = None) -> CalendarHarmonics:
        """Validate calendar controls without learning anything from outcomes.

        Parameters
        ----------
        X : pandas.DataFrame
            Timezone-aware departure timestamp column.
        y : object, optional
            Ignored; included for scikit-learn pipeline compatibility.

        Returns
        -------
        CalendarHarmonics
            Fitted transformer.
        """
        for name in ("daily_order", "weekly_order"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer.")
        if self.weekly_order > 3 or self.daily_order + self.weekly_order == 0:
            raise ValueError("Require weekly_order <= 3 and at least one harmonic.")
        if not isinstance(self.interactions, bool):
            raise ValueError("interactions must be boolean.")
        self._clock(X)
        self.n_features_in_ = X.shape[1]
        self.feature_names_in_ = np.asarray(X.columns, dtype=object)
        names = [
            f"{period}_{order}_{wave}"
            for period, limit in (
                ("daily", self.daily_order),
                ("weekly", self.weekly_order),
            )
            for order in range(1, limit + 1)
            for wave in ("sin", "cos")
        ]
        daily_count = 2 * self.daily_order
        if self.interactions:
            names += [
                f"{daily}__{weekly}"
                for daily in names[:daily_count]
                for weekly in names[daily_count:]
            ]
        self.output_names_ = np.asarray(names, dtype=object)
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        """Generate smooth repeating features without modifying input.

        Parameters
        ----------
        X : pandas.DataFrame
            Timezone-aware departure timestamps.

        Returns
        -------
        numpy.ndarray
            Daily terms, weekly terms, then their interaction products.
        """
        check_is_fitted(self, "output_names_")
        clock = self._clock(X)
        seconds = (
            clock.dt.hour * 3600
            + clock.dt.minute * 60
            + clock.dt.second
            + clock.dt.microsecond / 1e6
            + clock.dt.nanosecond / 1e9
        ).to_numpy(dtype=float)
        day_phase = seconds / 86400
        week_phase = (clock.dt.dayofweek.to_numpy() + day_phase) / 7
        daily, weekly = [], []
        for phase, limit, columns in (
            (day_phase, self.daily_order, daily),
            (week_phase, self.weekly_order, weekly),
        ):
            for order in range(1, limit + 1):
                angle = 2 * np.pi * order * phase
                columns.extend((np.sin(angle), np.cos(angle)))
        columns = daily + weekly
        if self.interactions:
            columns += [d * w for d in daily for w in weekly]
        return np.column_stack(columns)

    def get_feature_names_out(self, input_features: object = None) -> np.ndarray:
        """Return ordered harmonic feature names.

        Parameters
        ----------
        input_features : object, optional
            Ignored; output names describe the generated harmonics.

        Returns
        -------
        numpy.ndarray
            Names in the same order as ``transform`` columns.
        """
        check_is_fitted(self, "output_names_")
        return self.output_names_.copy()


class HuberLassoRegressor(RegressorMixin, BaseEstimator):
    """Fit Huber loss plus an L1 coefficient penalty using weighted Lasso.

    Minimize ``mean(huber_delta(y - intercept - X @ coef)) + alpha * |coef|_1``.
    Huber residual derivatives are capped at ``delta``. Unlike ordinary Lasso,
    increasing a spike's magnitude does not increase its influence without
    bound. Every observation is retained with a positive final weight.

    Parameters
    ----------
    alpha : float, default 0.001
        Positive L1 penalty, in the supplied target's units. The intercept is
        unpenalized. Standardize features in a training-fitted pipeline.
    delta : float, default 0.15
        Fixed positive residual transition between squared and linear loss.
        For log-duration targets, 0.15 is approximately a 16% multiplicative
        error. It is not an incident cutoff or an estimated noise scale.
    max_iter : int, default 300
        Maximum outer reweighting iterations.
    tol : float, default 1e-6
        Relative coefficient/intercept convergence tolerance.
    lasso_max_iter : int, default 20000
        Maximum coordinate-descent iterations per weighted Lasso fit.
    """

    def __init__(
        self,
        alpha: float = 0.001,
        delta: float = 0.15,
        max_iter: int = 300,
        tol: float = 1e-6,
        lasso_max_iter: int = 20000,
    ) -> None:
        self.alpha = alpha
        self.delta = delta
        self.max_iter = max_iter
        self.tol = tol
        self.lasso_max_iter = lasso_max_iter

    def fit(self, X: np.ndarray, y: np.ndarray) -> HuberLassoRegressor:
        """Fit the bounded-influence objective on supplied training rows.

        Parameters
        ----------
        X : numpy.ndarray
            Finite two-dimensional feature matrix.
        y : numpy.ndarray
            Finite one-dimensional target, normally log traversal duration.

        Returns
        -------
        HuberLassoRegressor
            Estimator with coefficients, intercept, weights and iteration count.

        Warns
        -----
        sklearn.exceptions.ConvergenceWarning
            If outer reweighting or inner coordinate descent fails to converge.
        """
        for name in ("alpha", "delta", "tol"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive.")
        for name in ("max_iter", "lasso_max_iter"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        X, y = check_X_y(X, y, dtype=float)
        self.n_features_in_ = X.shape[1]
        coef = np.zeros(X.shape[1])
        intercept = float(np.median(y))
        lasso = Lasso(
            max_iter=self.lasso_max_iter,
            tol=min(self.tol / 10, 1e-7),
            warm_start=True,
            selection="cyclic",
        )
        self.converged_ = False
        for iteration in range(1, self.max_iter + 1):
            residual = y - (X @ coef + intercept)
            weights = np.minimum(
                1.0, self.delta / np.maximum(np.abs(residual), self.delta)
            )
            # sklearn normalizes weights to n; preserve the fixed Huber L1 penalty.
            lasso.set_params(alpha=self.alpha * len(y) / weights.sum())
            lasso.fit(X, y, sample_weight=weights)
            change = max(
                float(np.max(np.abs(lasso.coef_ - coef))),
                abs(float(lasso.intercept_) - intercept),
            )
            scale = max(1.0, abs(intercept), float(np.max(np.abs(coef))))
            coef, intercept = lasso.coef_.copy(), float(lasso.intercept_)
            if change <= self.tol * scale:
                self.converged_ = True
                break
        if not self.converged_:
            warnings.warn(
                "HuberLassoRegressor did not converge; increase max_iter or inspect the fit.",
                ConvergenceWarning,
                stacklevel=2,
            )
        self.coef_, self.intercept_, self.n_iter_ = coef, intercept, iteration
        residual = y - self.predict(X)
        self.sample_weight_ = np.minimum(
            1.0, self.delta / np.maximum(np.abs(residual), self.delta)
        )
        absolute = np.abs(residual)
        loss = np.where(
            absolute <= self.delta,
            0.5 * residual**2,
            self.delta * (absolute - 0.5 * self.delta),
        )
        self.objective_ = float(loss.mean() + self.alpha * np.abs(coef).sum())
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict in the target units used during fitting.

        Parameters
        ----------
        X : numpy.ndarray
            Finite feature matrix with the fitted number of columns.

        Returns
        -------
        numpy.ndarray
            Linear predictions; exponentiate externally for log-duration fits.
        """
        check_is_fitted(self, "coef_")
        X = check_array(X, dtype=float)
        if X.shape[1] != self.n_features_in_:
            raise ValueError(
                "Prediction features do not match the fitted feature count."
            )
        return X @ self.coef_ + self.intercept_


def make_harmonic_time_splits(
    visits: pd.DataFrame,
    *,
    evaluation_end: pd.Timestamp,
    n_splits: int = 2,
    block_days: int = 7,
    gap_days: int = 1,
    min_train_days: int = 28,
    timezone: str = "America/Vancouver",
    service_day_start_hour: int = 3,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], pd.DataFrame]:
    """Make expanding calendar blocks, followed by one final evaluation block.

    Parameters
    ----------
    visits : pandas.DataFrame
        Contiguous RangeIndex; columns ``service_day`` (naive local dates),
        ``upstream_departure_time``, ``arrival_time``, ``available_at`` (aware
        instants), and ``trip_instance_key``. Availability must cover both final
        endpoint publications. Boundary recording days are excluded beforehand.
    evaluation_end : pandas.Timestamp
        Exclusive naive local service date, derived from recording bounds,
        not from the last observed bus. Calendar gaps are never compressed.
    n_splits, block_days, gap_days, min_train_days : int, optional
        Development block count, block length, embargo and required observed
        training dates. Every evaluation date must have at least one visit.
    timezone : str, optional
        Calendar timezone for converting the embargo cutoff to UTC.
    service_day_start_hour : int, default 3
        Local wall-clock cutover, not an elapsed-time subtraction on aware dates.

    Returns
    -------
    splits : list of tuple of numpy.ndarray
        Training/evaluation positional indices. All but the last are CV;
        the last is the reserved final block.
    summary : pandas.DataFrame
        Cutoffs, date ranges, support and purged-row counts.

    Raises
    ------
    ValueError
        For invalid inputs, missing calendar support or overlapping journeys.
    """
    for name, value in (
        ("n_splits", n_splits),
        ("block_days", block_days),
        ("gap_days", gap_days),
        ("min_train_days", min_train_days),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")
    if (
        isinstance(service_day_start_hour, bool)
        or not isinstance(service_day_start_hour, int)
        or not 0 <= service_day_start_hour <= 23
    ):
        raise ValueError("service_day_start_hour must be an integer in [0, 23].")
    columns = [
        "service_day",
        "upstream_departure_time",
        "arrival_time",
        "available_at",
        "trip_instance_key",
    ]
    if not set(columns).issubset(visits) or not visits.index.equals(
        pd.RangeIndex(len(visits))
    ):
        raise ValueError(
            "Visits need required split columns and a contiguous RangeIndex."
        )
    if visits[columns].isna().any().any():
        raise ValueError("Split columns must not contain missing values.")
    days = visits["service_day"]
    if (
        not pd.api.types.is_datetime64_dtype(days.dtype)
        or not days.eq(days.dt.normalize()).all()
    ):
        raise ValueError("service_day must contain naive local midnight dates.")
    evaluation_end = pd.Timestamp(evaluation_end)
    if (
        pd.isna(evaluation_end)
        or evaluation_end.tzinfo is not None
        or evaluation_end != evaluation_end.normalize()
    ):
        raise ValueError("evaluation_end must be a naive local midnight date.")
    for column in ("upstream_departure_time", "arrival_time", "available_at"):
        if not isinstance(visits[column].dtype, pd.DatetimeTZDtype):
            raise ValueError(f"{column} must contain timezone-aware timestamps.")
    expected_days = (
        visits["upstream_departure_time"].dt.tz_convert(timezone).dt.tz_localize(None)
        - pd.Timedelta(hours=service_day_start_hour)
    ).dt.normalize()
    if not days.eq(expected_days).all():
        raise ValueError("Service dates do not match the configured departure cutover.")
    first_start = evaluation_end - pd.Timedelta(days=block_days * (n_splits + 1))
    splits, rows = [], []
    for block in range(n_splits + 1):
        start = first_start + pd.Timedelta(days=block * block_days)
        end = start + pd.Timedelta(days=block_days)
        gap_start = start - pd.Timedelta(days=gap_days)
        cutoff = (
            (gap_start + pd.Timedelta(hours=service_day_start_hour))
            .tz_localize(timezone)
            .tz_convert("UTC")
        )
        candidate = days.lt(gap_start)
        late = (
            visits["available_at"].ge(cutoff)
            | visits["arrival_time"].ge(cutoff)
            | visits["upstream_departure_time"].ge(cutoff)
        )
        bad_trips = visits.loc[late, "trip_instance_key"]
        train = candidate & ~visits["trip_instance_key"].isin(bad_trips)
        evaluation = days.ge(start) & days.lt(end)
        train_days, eval_days = days[train].nunique(), days[evaluation].nunique()
        if train_days < min_train_days or eval_days != block_days:
            raise ValueError(
                f"Insufficient coverage at {start.date()}: {train_days} training "
                f"days (need {min_train_days}), {eval_days}/{block_days} evaluation days."
            )
        if set(visits.loc[train, "trip_instance_key"]) & set(
            visits.loc[evaluation, "trip_instance_key"]
        ):
            raise ValueError("A trip instance overlaps training and evaluation.")
        splits.append((np.flatnonzero(train), np.flatnonzero(evaluation)))
        rows.append(
            {
                "split": f"cv_{block + 1}" if block < n_splits else "final_evaluation",
                "fit_cutoff_utc": cutoff,
                "evaluation_start": start,
                "evaluation_end_exclusive": end,
                "train_days": train_days,
                "train_rows": int(train.sum()),
                "evaluation_days": eval_days,
                "evaluation_rows": int(evaluation.sum()),
                "purged_train_rows": int((candidate & ~train).sum()),
            }
        )
    return splits, pd.DataFrame(rows)


def save_harmonic_experiment(
    output_root: str | Path,
    *,
    models: dict[str, Pipeline],
    predictions: pd.DataFrame,
    cv_results: pd.DataFrame,
    metadata: dict[str, object],
) -> Path:
    """Save a new offline experiment without overwriting previous runs.

    Parameters
    ----------
    output_root : str or pathlib.Path
        Parent directory for timestamped experiment bundles.
    models : dict of sklearn.pipeline.Pipeline
        Fitted calendar transformers, scalers and log-target regressors.
    predictions, cv_results : pandas.DataFrame
        Evaluation predictions/residuals and development candidate scores.
    metadata : dict
        JSON-serializable configuration, input provenance and split description.

    Returns
    -------
    pathlib.Path
        New bundle directory containing pickle models, CSV evidence and JSON
        metadata. Only load model pickles from trusted sources.
    """
    serialized = json.dumps(metadata, indent=2, allow_nan=False)
    destination = Path(output_root) / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "metadata.json").write_text(serialized + "\n", encoding="utf-8")
    with (destination / "models.pkl").open("wb") as stream:
        pickle.dump(models, stream, protocol=pickle.HIGHEST_PROTOCOL)
    predictions.to_csv(destination / "predictions.csv", index=False)
    cv_results.to_csv(destination / "cv_results.csv", index=False)
    return destination
