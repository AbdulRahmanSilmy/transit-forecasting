"""Traversal-time models for origin-time delay forecasting.

Every model learns recurring stop-to-stop traversal seconds from training
links and predicts from fields known at a journey's origin: ``segment_key``,
``from_stop_id``, ``to_stop_id`` and the origin-feed ``scheduled_departure``
of the upstream stop. Unseen segments yield NaN; the harness counts them.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .harmonic_regression import CalendarHarmonics, HuberLassoRegressor

BASELINE_MODELS = frozenset({"segment_median", "calendar_median"})


class TraversalModel(Protocol):
    """Interface consumed by the delay forecast harness."""

    def fit(self, links: pd.DataFrame) -> TraversalModel:
        """Learn from training links with ``traversal_seconds``."""

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Return traversal seconds aligned with ``requests``."""


def _local_clock(times: pd.Series, timezone: str) -> pd.Series:
    """Convert aware instants to naive local wall times."""
    return times.dt.tz_convert(timezone).dt.tz_localize(None)


class SegmentMedianTraversal:
    """B2: per-segment training median, with no calendar pattern."""

    def fit(self, links: pd.DataFrame) -> SegmentMedianTraversal:
        """Learn per-segment medians.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key`` and ``traversal_seconds``.

        Returns
        -------
        SegmentMedianTraversal
            Fitted model.
        """
        self.medians_ = links.groupby("segment_key")["traversal_seconds"].median()
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict each request's segment median.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests with ``segment_key``.

        Returns
        -------
        numpy.ndarray
            Seconds; NaN for unseen segments.
        """
        return requests["segment_key"].map(self.medians_).to_numpy(dtype=float)


class CalendarMedianTraversal:
    """B3: segment x weekday x hour median with coarser fallbacks.

    Parameters
    ----------
    timezone : str, default "America/Vancouver"
        Calendar timezone for weekday and hour.
    min_count : int, default 5
        Minimum training links for a cell; otherwise fall back to segment x
        hour, then segment.
    """

    def __init__(self, timezone: str = "America/Vancouver", min_count: int = 5) -> None:
        self.timezone = timezone
        self.min_count = min_count

    def _keys(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Calendar keys from the scheduled upstream departure."""
        clock = _local_clock(frame["scheduled_departure"], self.timezone)
        return pd.DataFrame(
            {
                "segment_key": frame["segment_key"].to_numpy(),
                "weekday": clock.dt.dayofweek.to_numpy(),
                "hour": clock.dt.hour.to_numpy(),
            }
        )

    def fit(self, links: pd.DataFrame) -> CalendarMedianTraversal:
        """Learn cell medians with support counts.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key``, ``scheduled_departure`` and
            ``traversal_seconds``.

        Returns
        -------
        CalendarMedianTraversal
            Fitted model.
        """
        keys = self._keys(links).assign(
            traversal_seconds=links["traversal_seconds"].to_numpy()
        )
        self.tables_ = []
        for columns in (
            ["segment_key", "weekday", "hour"],
            ["segment_key", "hour"],
            ["segment_key"],
        ):
            table = keys.groupby(columns)["traversal_seconds"].agg(["median", "size"])
            minimum = self.min_count if len(columns) > 1 else 1
            self.tables_.append(
                (columns, table.loc[table["size"].ge(minimum), "median"])
            )
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict from the most specific supported cell.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests with ``segment_key`` and ``scheduled_departure``.

        Returns
        -------
        numpy.ndarray
            Seconds; NaN for unseen segments.
        """
        keys = self._keys(requests)
        prediction = np.full(len(keys), np.nan)
        for columns, medians in self.tables_:
            index = (
                pd.MultiIndex.from_frame(keys[columns])
                if len(columns) > 1
                else keys[columns[0]]
            )
            values = medians.reindex(index).to_numpy(dtype=float)
            fill = np.isnan(prediction)
            prediction[fill] = values[fill]
        return prediction


class HarmonicTraversal:
    """Per-segment robust harmonic regression on log traversal minutes.

    Mirrors the segment 4 notebook: daily/weekly Fourier terms of the local
    scheduled departure, standardized, fitted with Huber loss plus L1 penalty.
    Segments with fewer than ``min_rows`` links use their median.

    Parameters
    ----------
    daily_order, weekly_order : int, default 6, 3
        Harmonic orders.
    alpha : float, default 0.003
        L1 penalty.
    delta : float, default 0.15
        Huber transition in log units.
    timezone : str, default "America/Vancouver"
        Calendar timezone.
    min_rows : int, default 100
        Minimum links per segment for a harmonic fit.
    """

    def __init__(
        self,
        daily_order: int = 6,
        weekly_order: int = 3,
        alpha: float = 0.003,
        delta: float = 0.15,
        timezone: str = "America/Vancouver",
        min_rows: int = 100,
    ) -> None:
        self.daily_order = daily_order
        self.weekly_order = weekly_order
        self.alpha = alpha
        self.delta = delta
        self.timezone = timezone
        self.min_rows = min_rows

    def _pipeline(self) -> Pipeline:
        """Fresh unfitted pipeline."""
        return Pipeline(
            [
                (
                    "calendar",
                    CalendarHarmonics(
                        daily_order=self.daily_order,
                        weekly_order=self.weekly_order,
                        timezone=self.timezone,
                        timestamp_column="scheduled_departure",
                    ),
                ),
                ("scale", StandardScaler()),
                ("regressor", HuberLassoRegressor(alpha=self.alpha, delta=self.delta)),
            ]
        )

    def fit(self, links: pd.DataFrame) -> HarmonicTraversal:
        """Fit one pipeline per supported segment.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key``, ``scheduled_departure`` and
            positive ``traversal_seconds``.

        Returns
        -------
        HarmonicTraversal
            Fitted model.
        """
        self.pipelines_: dict[str, Pipeline] = {}
        self.medians_ = links.groupby("segment_key")["traversal_seconds"].median()
        for key, group in links.groupby("segment_key"):
            if len(group) < self.min_rows:
                continue
            target = np.log(group["traversal_seconds"].to_numpy(dtype=float) / 60)
            self.pipelines_[key] = self._pipeline().fit(
                group[["scheduled_departure"]], target
            )
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict traversal seconds per request.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests with ``segment_key`` and ``scheduled_departure``.

        Returns
        -------
        numpy.ndarray
            Seconds; segment median for unsupported segments, NaN if unseen.
        """
        prediction = np.array(requests["segment_key"].map(self.medians_), dtype=float)
        positions = np.arange(len(requests))
        for key, group in requests.groupby("segment_key"):
            if key in self.pipelines_:
                rows = positions[requests["segment_key"].eq(key).to_numpy()]
                log_minutes = self.pipelines_[key].predict(
                    group[["scheduled_departure"]]
                )
                prediction[rows] = 60 * np.exp(log_minutes)
        return prediction


class PooledHarmonicTraversal:
    """Segment levels plus one calendar shape shared across segments (H4).

    On log traversal minutes, each segment gets a robust level (its training
    median) and all segments share one Huber-Lasso harmonic shape fitted on
    the pooled level-removed residuals. With ``deviation_alpha`` set, each
    segment with at least ``min_rows`` links also fits its own harmonic
    deviation from the shared shape, with that (stronger) L1 penalty, so
    segment shapes are shrunk towards the pooled one (partial pooling).

    Parameters
    ----------
    daily_order, weekly_order : int, default 6, 3
        Harmonic orders.
    alpha : float, default 0.003
        L1 penalty of the shared shape.
    delta : float, default 0.15
        Huber transition in log units.
    deviation_alpha : float or None, default None
        L1 penalty of per-segment deviations; ``None`` shares the shape fully.
    timezone : str, default "America/Vancouver"
        Calendar timezone.
    min_rows : int, default 100
        Minimum links for a segment's own deviation fit.
    """

    def __init__(
        self,
        daily_order: int = 6,
        weekly_order: int = 3,
        alpha: float = 0.003,
        delta: float = 0.15,
        deviation_alpha: float | None = None,
        timezone: str = "America/Vancouver",
        min_rows: int = 100,
    ) -> None:
        self.daily_order = daily_order
        self.weekly_order = weekly_order
        self.alpha = alpha
        self.delta = delta
        self.deviation_alpha = deviation_alpha
        self.timezone = timezone
        self.min_rows = min_rows

    def _pipeline(self, alpha: float) -> Pipeline:
        """Fresh unfitted harmonic pipeline with the given L1 penalty."""
        return HarmonicTraversal(
            daily_order=self.daily_order,
            weekly_order=self.weekly_order,
            alpha=alpha,
            delta=self.delta,
            timezone=self.timezone,
        )._pipeline()

    def fit(self, links: pd.DataFrame) -> PooledHarmonicTraversal:
        """Fit segment levels, the shared shape and optional deviations.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key``, ``scheduled_departure`` and
            positive ``traversal_seconds``.

        Returns
        -------
        PooledHarmonicTraversal
            Fitted model.
        """
        log_minutes = np.log(links["traversal_seconds"].to_numpy(dtype=float) / 60)
        segments = links["segment_key"].to_numpy()
        self.levels_ = pd.Series(log_minutes).groupby(segments).median()
        residual = log_minutes - self.levels_.reindex(segments).to_numpy()
        times = links[["scheduled_departure"]]
        self.shared_ = self._pipeline(self.alpha).fit(times, residual)
        self.deviations_: dict[str, Pipeline] = {}
        if self.deviation_alpha is not None:
            remaining = residual - self.shared_.predict(times)
            for key, positions in links.groupby("segment_key").indices.items():
                if len(positions) < self.min_rows:
                    continue
                self.deviations_[key] = self._pipeline(self.deviation_alpha).fit(
                    times.iloc[positions], remaining[positions]
                )
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict traversal seconds per request.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests with ``segment_key`` and ``scheduled_departure``.

        Returns
        -------
        numpy.ndarray
            Seconds; NaN for segments unseen in training.
        """
        times = requests[["scheduled_departure"]]
        log_minutes = requests["segment_key"].map(self.levels_).to_numpy(dtype=float)
        log_minutes = log_minutes + self.shared_.predict(times)
        for key, positions in requests.groupby("segment_key").indices.items():
            if key in self.deviations_:
                log_minutes[positions] += self.deviations_[key].predict(
                    times.iloc[positions]
                )
        return 60 * np.exp(log_minutes)


class RecentLevelTraversal:
    """Calendar model plus each segment's recent level offset (H3).

    Fits ``base`` on all training links, then measures each segment's median
    log residual ``log(actual / base prediction)`` over links whose scheduled
    departure falls in the last ``window_days`` of the training data. The
    median keeps short-lived incidents from moving the level. Predictions are
    the base prediction times ``exp(offset)``. Segments with fewer than
    ``min_links`` recent links get no offset.

    Parameters
    ----------
    base : TraversalModel
        Unfitted calendar model.
    window_days : float, default 14
        Trailing window, in days before the latest training departure.
    min_links : int, default 30
        Minimum recent links for a segment's offset.
    """

    def __init__(
        self, base: TraversalModel, window_days: float = 14, min_links: int = 30
    ) -> None:
        self.base = base
        self.window_days = window_days
        self.min_links = min_links

    def fit(self, links: pd.DataFrame) -> RecentLevelTraversal:
        """Fit the base model and the recent per-segment offsets.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key``, ``scheduled_departure`` and
            positive ``traversal_seconds``. Not modified.

        Returns
        -------
        RecentLevelTraversal
            Fitted model.
        """
        self.base.fit(links)
        start = links["scheduled_departure"].max() - pd.Timedelta(days=self.window_days)
        recent = links.loc[links["scheduled_departure"].gt(start)]
        residual = np.log(
            recent["traversal_seconds"].to_numpy(dtype=float)
            / np.asarray(self.base.predict(recent), dtype=float)
        )
        summary = (
            pd.Series(residual, index=recent.index)
            .groupby(recent["segment_key"])
            .agg(["median", "size"])
        )
        self.offsets_ = summary.loc[summary["size"].ge(self.min_links), "median"]
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict base traversal seconds scaled by the segment's offset.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests accepted by the base model, with ``segment_key``.

        Returns
        -------
        numpy.ndarray
            Seconds; NaN wherever the base model returns NaN.
        """
        offset = requests["segment_key"].map(self.offsets_).fillna(0.0)
        return np.asarray(self.base.predict(requests), dtype=float) * np.exp(
            offset.to_numpy(dtype=float)
        )


class ResidualHourCorrection:
    """Calendar model plus an additive per-segment x hour residual mean (H8).

    Fits ``base`` on all training links, then takes the in-sample residual
    ``actual - base prediction`` of every link and averages it per segment and
    local hour of the scheduled upstream departure. Each cell's mean is
    shrunk towards its segment's mean residual,
    ``(n * cell_mean + k * segment_mean) / (n + k)``, so thin cells borrow from
    the segment. Predictions are the base prediction plus that correction,
    which makes them mean-unbiased per cell on the training data. Unlike a
    ratio (smearing) factor, the correction can be negative, so cells the base
    model over-predicts move down.

    With ``centred=True`` the segment's level is removed and only the hour
    shape within the segment is kept: the correction is
    ``n / (n + k) * (cell_mean - segment_mean)``, the cell's deviation from
    its segment shrunk towards 0. Over a segment's training links the
    corrections then average to about 0, so the model can't shift a whole
    segment up or down (E017, option (c) on H8).

    Parameters
    ----------
    base : TraversalModel
        Unfitted calendar model.
    timezone : str, default "America/Vancouver"
        Calendar timezone for the hour.
    shrinkage : float, default 30
        Pseudo-count ``k`` pulling a cell's mean towards its segment's mean
        (or, when centred, its deviation towards 0).
    centred : bool, default False
        Keep only the within-segment hour shape (see above).
    """

    def __init__(
        self,
        base: TraversalModel,
        timezone: str = "America/Vancouver",
        shrinkage: float = 30.0,
        centred: bool = False,
    ) -> None:
        self.base = base
        self.timezone = timezone
        self.shrinkage = shrinkage
        self.centred = centred

    def _hours(self, frame: pd.DataFrame) -> np.ndarray:
        """Local hour of the scheduled upstream departure."""
        return _local_clock(
            frame["scheduled_departure"], self.timezone
        ).dt.hour.to_numpy()

    def fit(self, links: pd.DataFrame) -> ResidualHourCorrection:
        """Fit the base model and the shrunk cell corrections.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``segment_key``, ``scheduled_departure`` and
            positive ``traversal_seconds``. Not modified.

        Returns
        -------
        ResidualHourCorrection
            Fitted model.
        """
        self.base.fit(links)
        residual = links["traversal_seconds"].to_numpy(dtype=float) - np.asarray(
            self.base.predict(links), dtype=float
        )
        frame = pd.DataFrame(
            {
                "segment_key": links["segment_key"].to_numpy(),
                "hour": self._hours(links),
                "residual": residual,
            }
        ).dropna(subset=["residual"])
        self.segment_means_ = frame.groupby("segment_key")["residual"].mean()
        cells = frame.groupby(["segment_key", "hour"])["residual"].agg(["mean", "size"])
        prior = self.segment_means_.reindex(cells.index.get_level_values(0)).to_numpy()
        self.corrections_ = (cells["size"] * cells["mean"] + self.shrinkage * prior) / (
            cells["size"] + self.shrinkage
        )
        if self.centred:
            self.corrections_ = self.corrections_ - prior
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict base traversal seconds plus the cell correction.

        Parameters
        ----------
        requests : pandas.DataFrame
            Requests accepted by the base model, with ``segment_key`` and
            ``scheduled_departure``.

        Returns
        -------
        numpy.ndarray
            Seconds; NaN wherever the base model returns NaN. Unseen cells use
            the segment's mean residual (0 when centred); unseen segments get
            no correction.
        """
        index = pd.MultiIndex.from_arrays(
            [requests["segment_key"].to_numpy(), self._hours(requests)]
        )
        correction = self.corrections_.reindex(index).to_numpy(dtype=float)
        if not self.centred:
            segment = requests["segment_key"].map(self.segment_means_)
            correction = np.where(
                np.isnan(correction), segment.to_numpy(dtype=float), correction
            )
        correction = np.nan_to_num(correction, nan=0.0)
        return np.asarray(self.base.predict(requests), dtype=float) + correction


class ShuffledTargetTraversal:
    """Control: fit a model on traversals permuted within each segment.

    Permutation keeps each segment's distribution but destroys any calendar
    relationship, so the control should not beat the segment median.

    Parameters
    ----------
    model : TraversalModel
        Unfitted model to wrap.
    seed : int, default 0
        Permutation seed.
    """

    def __init__(self, model: TraversalModel, seed: int = 0) -> None:
        self.model = model
        self.seed = seed

    def fit(self, links: pd.DataFrame) -> ShuffledTargetTraversal:
        """Fit the wrapped model on permuted targets.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links; not modified.

        Returns
        -------
        ShuffledTargetTraversal
            Fitted control.
        """
        rng = np.random.default_rng(self.seed)
        shuffled = links.copy()
        values = shuffled["traversal_seconds"].to_numpy(dtype=float).copy()
        for positions in shuffled.groupby("segment_key").indices.values():
            values[positions] = rng.permutation(values[positions])
        shuffled["traversal_seconds"] = values
        self.model.fit(shuffled)
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict with the wrapped model.

        Parameters
        ----------
        requests : pandas.DataFrame
            Prediction requests.

        Returns
        -------
        numpy.ndarray
            Seconds.
        """
        return self.model.predict(requests)


def make_traversal_model(name: str, timezone: str) -> TraversalModel:
    """Construct a registered traversal model.

    Parameters
    ----------
    name : str
        ``'segment_median'``, ``'calendar_median'``, ``'harmonic_huber'``,
        ``'pooled_harmonic'`` (shared calendar shape) or
        ``'pooled_harmonic_partial'`` (shared shape plus per-segment
        deviations penalised at ``alpha=0.01``) or ``'harmonic_recent_level'``
        (``harmonic_huber`` plus a trailing 14-day per-segment level offset)
        or ``'harmonic_residual_hour'`` (``harmonic_huber`` plus an additive
        per-segment x hour training-residual mean, shrinkage 30) or
        ``'harmonic_residual_hour_centred'`` (the same with the segment mean
        removed, so only the within-segment hour shape is added).
    timezone : str
        Calendar timezone.

    Returns
    -------
    TraversalModel
        Unfitted model.

    Raises
    ------
    ValueError
        For an unknown name.
    """
    if name == "segment_median":
        return SegmentMedianTraversal()
    if name == "calendar_median":
        return CalendarMedianTraversal(timezone=timezone)
    if name == "harmonic_huber":
        return HarmonicTraversal(timezone=timezone)
    if name == "pooled_harmonic":
        return PooledHarmonicTraversal(timezone=timezone)
    if name == "pooled_harmonic_partial":
        return PooledHarmonicTraversal(timezone=timezone, deviation_alpha=0.01)
    if name == "harmonic_recent_level":
        return RecentLevelTraversal(HarmonicTraversal(timezone=timezone))
    if name == "harmonic_residual_hour":
        return ResidualHourCorrection(HarmonicTraversal(timezone=timezone), timezone)
    if name == "harmonic_residual_hour_centred":
        return ResidualHourCorrection(
            HarmonicTraversal(timezone=timezone), timezone, centred=True
        )
    raise ValueError(f"Unknown traversal model {name!r}.")
