import numpy as np
import pandas as pd
import pytest

from forecasting.traversal_models import (
    HarmonicTraversal,
    PooledHarmonicTraversal,
    ShuffledTargetTraversal,
    make_traversal_model,
)

TIMEZONE = "America/Vancouver"


def _links(segments, days=28, peaks=None):
    """Hourly links 06:00-22:00 local; ``peaks`` maps segment -> {hour: factor}."""
    peaks = peaks or {}
    rows = []
    for day in pd.date_range("2026-09-07", periods=days, freq="D"):
        for hour in range(6, 23):
            local = day + pd.Timedelta(hours=hour)
            for key, (minutes, every) in segments.items():
                if (day.dayofyear * 24 + hour) % every:
                    continue
                factor = peaks.get(key, {}).get(hour, 1.0)
                rows.append(
                    {
                        "segment_key": key,
                        "scheduled_departure": local.tz_localize(TIMEZONE).tz_convert(
                            "UTC"
                        ),
                        "traversal_seconds": 60 * minutes * factor,
                    }
                )
    return pd.DataFrame(rows)


def _requests(key, hours, day="2026-10-07"):
    local = pd.Timestamp(day) + pd.to_timedelta(hours, unit="h")
    return pd.DataFrame(
        {
            "segment_key": key,
            "scheduled_departure": local.tz_localize(TIMEZONE).tz_convert("UTC"),
        }
    )


def test_shared_shape_lends_a_peak_to_a_low_support_segment():
    # A and B are well supported; C has only ~50 links (below min_rows=100).
    segments = {"A": (5.0, 1), "B": (10.0, 1), "C": (2.0, 10)}
    peak = {7: 1.3, 8: 1.3, 9: 1.3}  # a broad morning peak, not a one-hour spike
    links = _links(segments, peaks={key: peak for key in segments})
    assert links["segment_key"].value_counts()["C"] < 100
    pooled = PooledHarmonicTraversal(timezone=TIMEZONE).fit(links)
    per_segment = HarmonicTraversal(timezone=TIMEZONE).fit(links)
    requests = _requests("C", [8, 14])
    pooled_c = pooled.predict(requests)
    # The pooled model gives C the shared 8 am peak; per-segment falls back to C's median.
    assert pooled_c[0] / pooled_c[1] > 1.15
    assert per_segment.predict(requests)[0] == pytest.approx(
        per_segment.predict(requests)[1]
    )
    # Segment levels are kept: B is about twice A off-peak.
    off_peak = pooled.predict(pd.concat([_requests("A", [14]), _requests("B", [14])]))
    assert off_peak[1] / off_peak[0] == pytest.approx(2.0, rel=0.05)


def test_partial_pooling_recovers_a_segment_specific_peak():
    segments = {"A": (5.0, 1), "B": (5.0, 1), "D": (5.0, 1)}
    links = _links(segments, peaks={"B": {17: 1.6}})
    shared = PooledHarmonicTraversal(timezone=TIMEZONE).fit(links)
    partial = PooledHarmonicTraversal(timezone=TIMEZONE, deviation_alpha=0.001).fit(
        links
    )
    requests = _requests("B", [17])
    truth = 60 * 5.0 * 1.6
    assert abs(partial.predict(requests)[0] - truth) < abs(
        shared.predict(requests)[0] - truth
    )
    # A has no 5 pm peak; the partial model shouldn't invent one for it.
    assert partial.predict(_requests("A", [17]))[0] < 60 * 5.0 * 1.2


def test_pooled_predicts_nan_for_unseen_segments_and_works_under_control():
    links = _links({"A": (5.0, 1), "B": (7.0, 1)})
    model = PooledHarmonicTraversal(timezone=TIMEZONE).fit(links)
    assert np.isnan(model.predict(_requests("Z", [8]))).all()
    control = ShuffledTargetTraversal(PooledHarmonicTraversal(timezone=TIMEZONE)).fit(
        links
    )
    assert np.isfinite(control.predict(_requests("A", [8, 9]))).all()


@pytest.mark.parametrize(
    "name, partial",
    [("pooled_harmonic", False), ("pooled_harmonic_partial", True)],
)
def test_pooled_models_are_registered(name, partial):
    model = make_traversal_model(name, TIMEZONE)
    assert isinstance(model, PooledHarmonicTraversal)
    assert (model.deviation_alpha is not None) is partial
