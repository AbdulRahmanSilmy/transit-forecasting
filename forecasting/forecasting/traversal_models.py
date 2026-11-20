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
        ``'segment_median'``, ``'calendar_median'`` or ``'harmonic_huber'``.
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
    raise ValueError(f"Unknown traversal model {name!r}.")
