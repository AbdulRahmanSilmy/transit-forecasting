"""Dwell-time models for origin-time delay forecasting.

The delay study chains ``d_k = d_{k-1} + T_k + W_k - S_k``. Traversal models
(:mod:`forecasting.traversal_models`) supply ``T_k``; dwell models supply
``W_k``, the time a bus spends at the upstream stop of each link.

A dwell model predicts from fields known at a journey's origin
(``DWELL_REQUEST_COLUMNS``). A model that also sets ``uses_arrival_delay``
receives the chain's own predicted arrival delay at the stop, so it can
express schedule recovery: a bus that is early at a timepoint waits for its
scheduled departure. The harness then chains stop by stop instead of with
one cumulative sum.

Dwell at a journey's first stop is the layover before the anchor and is
always zero.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

DEFAULT_DWELL = "stop_median"
DWELL_REQUEST_COLUMNS = (
    "from_stop_id",
    "scheduled_departure",
    "scheduled_dwell_seconds",
    "upstream_is_first_stop",
)


class DwellModel(Protocol):
    """Interface consumed by the delay forecast harness.

    Attributes
    ----------
    uses_arrival_delay : bool
        Whether ``predict`` needs the chain's predicted arrival delay at the
        upstream stop. If False, the harness calls ``predict`` once for all
        links and ``arrival_delay_seconds`` is None.
    """

    uses_arrival_delay: bool

    def fit(self, links: pd.DataFrame) -> DwellModel:
        """Learn from training links with ``upstream_dwell_seconds``."""

    def predict(
        self,
        requests: pd.DataFrame,
        arrival_delay_seconds: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return dwell seconds at each request's upstream stop."""


def _training_dwells(links: pd.DataFrame) -> pd.DataFrame:
    """Training links with a valid dwell at a stop after the first."""
    valid = links["upstream_dwell_seconds"].ge(0)
    if "upstream_is_first_stop" in links:
        valid &= ~links["upstream_is_first_stop"].astype(bool)
    return links.loc[valid]


class StopMedianDwell:
    """Per-upstream-stop training median dwell, the study's default rule.

    Negative or missing dwell, and dwell at a journey's first stop (layover),
    are ignored. Unseen stops use the pooled median; no data gives zero.
    """

    uses_arrival_delay = False

    def fit(self, links: pd.DataFrame) -> StopMedianDwell:
        """Learn medians from training links.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``from_stop_id`` and ``upstream_dwell_seconds``.

        Returns
        -------
        StopMedianDwell
            Fitted rule.
        """
        valid = links.loc[links["upstream_dwell_seconds"].ge(0)]
        self.medians_ = valid.groupby("from_stop_id")["upstream_dwell_seconds"].median()
        pooled = valid["upstream_dwell_seconds"].median()
        self.pooled_ = 0.0 if pd.isna(pooled) else float(pooled)
        return self

    def predict(
        self,
        requests: pd.DataFrame,
        arrival_delay_seconds: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return dwell seconds at each link's upstream stop.

        Parameters
        ----------
        requests : pandas.DataFrame
            Links with ``from_stop_id`` and ``upstream_is_first_stop``.
        arrival_delay_seconds : numpy.ndarray or None, optional
            Ignored.

        Returns
        -------
        numpy.ndarray
            Nonnegative dwell; zero where the upstream stop is the first stop.
        """
        dwell = requests["from_stop_id"].map(self.medians_).fillna(self.pooled_)
        dwell = np.array(dwell, dtype=float)
        dwell[requests["upstream_is_first_stop"].to_numpy(bool)] = 0.0
        return dwell


def fit_hold_hinge(
    arrival_delay: np.ndarray,
    departure_delay: np.ndarray,
    *,
    offsets: np.ndarray,
    floors: np.ndarray,
    min_gain: float,
) -> tuple[float, float]:
    """Fit ``departure = max(arrival + c, h)`` to one stop by least absolute error.

    The no-hold fit (``h = -inf``) uses the exact median of
    ``departure - arrival``. A floor is kept only if the best hinge on the
    grid lowers the mean absolute error by at least ``min_gain`` (relative).

    Parameters
    ----------
    arrival_delay, departure_delay : numpy.ndarray
        Observed delays at the stop, in seconds.
    offsets : numpy.ndarray
        Grid of candidate recovery offsets ``c`` (seconds).
    floors : numpy.ndarray
        Grid of candidate hold floors ``h`` (departure delay, seconds).
    min_gain : float
        Minimum relative MAE reduction needed to keep a floor.

    Returns
    -------
    tuple of float
        ``(c, h)``; ``h`` is ``-inf`` when the stop does not hold.
    """
    a = np.asarray(arrival_delay, float)
    y = np.asarray(departure_delay, float)
    c0 = float(np.median(y - a))
    loss0 = float(np.abs(y - (a + c0)).mean())
    best = (np.inf, c0, -np.inf)
    shifted = a[None, :] + np.asarray(offsets, float)[:, None]
    for h in floors:
        loss = np.abs(y[None, :] - np.maximum(shifted, h)).mean(axis=1)
        i = int(np.argmin(loss))
        if loss[i] < best[0]:
            best = (float(loss[i]), float(offsets[i]), float(h))
    if best[0] <= (1 - min_gain) * loss0:
        return best[1], best[2]
    return c0, -np.inf


class StopHoldDwell:
    """Recovery-aware dwell: per-stop hinge on the predicted arrival delay.

    At each stop, departure delay is ``max(arrival_delay + c, h)``. ``c`` is
    the usual change in delay over the dwell (dwell minus scheduled dwell);
    ``h`` is a hold floor near the schedule. A bus predicted to arrive early
    at a stop that holds waits until its departure delay reaches ``h``.
    Both are learned per stop from training links (L1 grid search, see
    :func:`fit_hold_hinge`). Stops without a clear floor get ``h = -inf``,
    which is the median rule expressed as a delay change. Nothing is
    route-specific.

    Predicted dwell is ``max(c, h - arrival_delay) + scheduled_dwell``,
    clipped at zero.

    Parameters
    ----------
    min_links : int, default 50
        Stops with fewer training links use the pooled median dwell, no hold.
    min_gain : float, default 0.02
        Relative in-sample MAE reduction needed to keep a floor.
    offset_grid : tuple of float, default (-120, 180, 1)
        ``(start, stop, step)`` for ``c`` in seconds (stop inclusive).
    floor_grid : tuple of float, default (-60, 60, 5)
        ``(start, stop, step)`` for ``h`` in seconds (stop inclusive).
    """

    uses_arrival_delay = True

    def __init__(
        self,
        *,
        min_links: int = 50,
        min_gain: float = 0.02,
        offset_grid: tuple[float, float, float] = (-120.0, 180.0, 1.0),
        floor_grid: tuple[float, float, float] = (-60.0, 60.0, 5.0),
    ) -> None:
        self.min_links = min_links
        self.min_gain = min_gain
        self.offset_grid = offset_grid
        self.floor_grid = floor_grid

    def fit(self, links: pd.DataFrame) -> StopHoldDwell:
        """Learn each stop's recovery offset and hold floor.

        Parameters
        ----------
        links : pandas.DataFrame
            Training links with ``from_stop_id``, ``upstream_is_first_stop``,
            ``upstream_dwell_seconds``, ``upstream_arrival_delay_seconds`` and
            ``scheduled_dwell_seconds``.

        Returns
        -------
        StopHoldDwell
            Fitted model with ``params_`` (per stop: ``links``, ``c``, ``h``)
            and ``pooled_`` (median dwell for unseen stops).
        """
        valid = _training_dwells(links)
        pooled = valid["upstream_dwell_seconds"].median()
        self.pooled_ = 0.0 if pd.isna(pooled) else float(pooled)
        valid = valid.loc[
            np.isfinite(valid["upstream_arrival_delay_seconds"].astype(float))
            & np.isfinite(valid["scheduled_dwell_seconds"].astype(float))
        ]
        start, stop, step = self.offset_grid
        offsets = np.arange(start, stop + step / 2, step)
        start, stop, step = self.floor_grid
        floors = np.arange(start, stop + step / 2, step)
        records = []
        for stop_id, group in valid.groupby("from_stop_id"):
            if len(group) < self.min_links:
                continue
            arrival = group["upstream_arrival_delay_seconds"].to_numpy(float)
            departure = (
                arrival
                + group["upstream_dwell_seconds"].to_numpy(float)
                - group["scheduled_dwell_seconds"].to_numpy(float)
            )
            c, h = fit_hold_hinge(
                arrival,
                departure,
                offsets=offsets,
                floors=floors,
                min_gain=self.min_gain,
            )
            records.append(
                {"from_stop_id": stop_id, "links": len(group), "c": c, "h": h}
            )
        self.params_ = pd.DataFrame(
            records, columns=["from_stop_id", "links", "c", "h"]
        ).set_index("from_stop_id")
        return self

    def predict(
        self,
        requests: pd.DataFrame,
        arrival_delay_seconds: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return dwell seconds given the predicted arrival delay at each stop.

        Parameters
        ----------
        requests : pandas.DataFrame
            Links with ``from_stop_id``, ``scheduled_dwell_seconds`` and
            ``upstream_is_first_stop``.
        arrival_delay_seconds : numpy.ndarray or None, optional
            Predicted arrival delay at each upstream stop. Where missing, no
            hold is applied.

        Returns
        -------
        numpy.ndarray
            Nonnegative dwell; zero at a journey's first stop.
        """
        n = len(requests)
        arrival = (
            np.full(n, np.nan)
            if arrival_delay_seconds is None
            else np.asarray(arrival_delay_seconds, float)
        )
        stops = requests["from_stop_id"]
        c = stops.map(self.params_["c"]).to_numpy(float)
        h = stops.map(self.params_["h"]).to_numpy(float)
        scheduled = requests["scheduled_dwell_seconds"].to_numpy(float)
        scheduled = np.where(np.isfinite(scheduled), scheduled, 0.0)
        hold = np.where(np.isfinite(arrival) & np.isfinite(h), h - arrival, -np.inf)
        dwell = np.maximum(c, hold) + scheduled
        dwell = np.where(np.isfinite(c), dwell, self.pooled_)
        dwell = np.maximum(dwell, 0.0)
        dwell[requests["upstream_is_first_stop"].to_numpy(bool)] = 0.0
        return dwell


def make_dwell_model(name: str) -> DwellModel:
    """Construct a registered dwell model.

    Parameters
    ----------
    name : str
        ``'stop_median'`` (per-stop training median, the default rule) or
        ``'stop_hold'`` (per-stop hinge on the predicted arrival delay:
        timepoint holding and recovery, see :class:`StopHoldDwell`).

    Returns
    -------
    DwellModel
        Unfitted model.

    Raises
    ------
    ValueError
        For an unknown name.
    """
    if name == "stop_median":
        return StopMedianDwell()
    if name == "stop_hold":
        return StopHoldDwell()
    raise ValueError(f"Unknown dwell model {name!r}.")
