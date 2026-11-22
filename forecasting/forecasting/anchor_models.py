"""First-stop departure-delay models that replace the feed's chain anchor.

The delay study chains traversal forecasts from an anchor: the departure delay
at a journey's first stop. By default that anchor is Transit BC's earliest
estimate, which is almost always 0 s even though buses usually leave late.
These models learn the anchor from observed first-stop departures in training
journeys and predict it from fields known at the journey's origin: the first
``stop_id`` and its origin-feed ``calendar_departure``.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import pandas as pd

from .traversal_models import CalendarMedianTraversal

FEED_ANCHOR = "feed"


class AnchorModel(Protocol):
    """Interface consumed by the delay forecast harness."""

    def fit(self, departures: pd.DataFrame) -> AnchorModel:
        """Learn from first-stop rows with ``departure_delay_seconds``."""

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Return departure-delay seconds aligned with ``requests``."""


class CalendarMedianAnchor:
    """First stop x weekday x hour median departure delay, with fallbacks.

    Falls back to first stop x hour, then first stop, then the pooled
    training median, so every journey gets an anchor.

    Parameters
    ----------
    timezone : str, default "America/Vancouver"
        Calendar timezone for weekday and hour.
    min_count : int, default 5
        Minimum training departures for a weekday x hour or hour cell.
    """

    def __init__(self, timezone: str = "America/Vancouver", min_count: int = 5) -> None:
        self.timezone = timezone
        self.min_count = min_count

    @staticmethod
    def _as_links(frame: pd.DataFrame) -> pd.DataFrame:
        """Rename first-stop columns to the calendar-median traversal schema."""
        links = pd.DataFrame(
            {
                "segment_key": frame["stop_id"].astype("string").to_numpy(),
                # Keep a Series: to_numpy() would drop the timezone.
                "scheduled_departure": frame["calendar_departure"].reset_index(
                    drop=True
                ),
            }
        )
        if "departure_delay_seconds" in frame:
            links["traversal_seconds"] = frame["departure_delay_seconds"].to_numpy(
                dtype=float
            )
        return links

    def fit(self, departures: pd.DataFrame) -> CalendarMedianAnchor:
        """Learn cell medians of observed first-stop departure delay.

        Parameters
        ----------
        departures : pandas.DataFrame
            Training first-stop rows with ``stop_id``, ``calendar_departure``
            (timezone-aware) and ``departure_delay_seconds``.

        Returns
        -------
        CalendarMedianAnchor
            Fitted model.

        Raises
        ------
        ValueError
            If there are no training departures.
        """
        if departures.empty:
            raise ValueError("No training departures for the anchor model.")
        self.calendar_ = CalendarMedianTraversal(
            timezone=self.timezone, min_count=self.min_count
        ).fit(self._as_links(departures))
        self.pooled_ = float(departures["departure_delay_seconds"].median())
        return self

    def predict(self, requests: pd.DataFrame) -> np.ndarray:
        """Predict each journey's first-stop departure delay.

        Parameters
        ----------
        requests : pandas.DataFrame
            One row per journey with ``stop_id`` and ``calendar_departure``.

        Returns
        -------
        numpy.ndarray
            Departure delay in seconds; never NaN.
        """
        prediction = self.calendar_.predict(self._as_links(requests))
        prediction[np.isnan(prediction)] = self.pooled_
        return prediction


def make_anchor_model(name: str, timezone: str) -> AnchorModel:
    """Construct a registered anchor model.

    Parameters
    ----------
    name : {'calendar_median'}
        Registered anchor model name. The harness handles ``'feed'`` itself
        (Transit BC's earliest estimate) without a model.
    timezone : str
        Calendar timezone.

    Returns
    -------
    AnchorModel
        Unfitted model.

    Raises
    ------
    ValueError
        For an unknown name.
    """
    if name == "calendar_median":
        return CalendarMedianAnchor(timezone=timezone)
    raise ValueError(f"Unknown anchor model {name!r}.")
