import numpy as np
import pandas as pd
import pytest

from forecasting.traversal_models import (
    HarmonicTraversal,
    PooledHarmonicTraversal,
    RecentLevelTraversal,
    ResidualHourCorrection,
    SegmentMedianTraversal,
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


def _shift_recent(links, key, factor, days=14):
    """Scale ``key``'s traversals in the last ``days`` of the frame."""
    links = links.copy()
    start = links["scheduled_departure"].max() - pd.Timedelta(days=days)
    rows = links["segment_key"].eq(key) & links["scheduled_departure"].gt(start)
    links.loc[rows, "traversal_seconds"] *= factor
    return links


def test_recent_level_follows_a_sustained_shift():
    links = _shift_recent(_links({"A": (5.0, 1), "B": (5.0, 1)}), "A", 1.2)
    original = links.copy()
    model = RecentLevelTraversal(SegmentMedianTraversal()).fit(links)
    pd.testing.assert_frame_equal(links, original)
    requests = pd.concat([_requests("A", [12]), _requests("B", [12])])
    base = SegmentMedianTraversal().fit(links).predict(requests)
    adjusted = model.predict(requests)
    # A's last two weeks run 20% slow; B is unchanged.
    assert adjusted[0] == pytest.approx(300 * 1.2, rel=0.01)
    assert adjusted[0] > base[0]
    assert adjusted[1] == pytest.approx(base[1])


def test_recent_level_ignores_a_one_day_incident():
    links = _shift_recent(_links({"A": (5.0, 1)}), "A", 3.0, days=1)
    model = RecentLevelTraversal(SegmentMedianTraversal()).fit(links)
    assert model.predict(_requests("A", [12]))[0] == pytest.approx(300.0)


def test_recent_level_needs_enough_recent_links():
    links = _shift_recent(_links({"A": (5.0, 1)}), "A", 1.5)
    model = RecentLevelTraversal(SegmentMedianTraversal(), min_links=10_000).fit(links)
    assert model.offsets_.empty
    assert model.predict(_requests("A", [12]))[0] == pytest.approx(
        SegmentMedianTraversal().fit(links).predict(_requests("A", [12]))[0]
    )


def test_recent_level_model_is_registered():
    model = make_traversal_model("harmonic_recent_level", TIMEZONE)
    assert isinstance(model, RecentLevelTraversal)
    assert isinstance(model.base, HarmonicTraversal)


def test_residual_hour_correction_fixes_cell_bias_in_both_directions():
    # The segment median misses A's slow 8 am and fast 17 h cells.
    links = _links({"A": (5.0, 1), "B": (5.0, 1)}, peaks={"A": {8: 1.4, 17: 0.8}})
    original = links.copy()
    model = ResidualHourCorrection(SegmentMedianTraversal(), TIMEZONE).fit(links)
    pd.testing.assert_frame_equal(links, original)
    prediction = model.predict(_requests("A", [8, 17, 12]))
    n = 28  # one link per day per cell
    segment_mean = (300 * 0.4 - 300 * 0.2) / 17  # A's mean residual over 17 hours
    shrunk = lambda cell: (n * cell + 30 * segment_mean) / (n + 30)  # noqa: E731
    assert prediction[0] == pytest.approx(300 + shrunk(120))
    assert prediction[1] == pytest.approx(300 + shrunk(-60))  # moves down
    assert prediction[2] == pytest.approx(300 + shrunk(0))
    # B has no bias, so no correction.
    assert model.predict(_requests("B", [8]))[0] == pytest.approx(300)


def test_residual_hour_correction_falls_back_for_unseen_cells_and_segments():
    links = _links({"A": (5.0, 1)}, peaks={"A": {8: 1.4}})
    model = ResidualHourCorrection(SegmentMedianTraversal(), TIMEZONE).fit(links)
    unseen_hour = model.predict(_requests("A", [3]))[0]
    assert unseen_hour == pytest.approx(300 + model.segment_means_["A"])
    base = SegmentMedianTraversal().fit(links)
    unseen_segment = pd.DataFrame(
        {
            "segment_key": ["Z"],
            "scheduled_departure": _requests("A", [8])["scheduled_departure"],
        }
    )
    np.testing.assert_array_equal(
        model.predict(unseen_segment), base.predict(unseen_segment)
    )


def test_residual_hour_model_is_registered():
    model = make_traversal_model("harmonic_residual_hour", TIMEZONE)
    assert isinstance(model, ResidualHourCorrection)
    assert isinstance(model.base, HarmonicTraversal)
    assert model.shrinkage == 30


def test_centred_residual_hour_correction_keeps_only_the_hour_shape():
    # A runs 60 s slow at every hour (a segment level) and 120 s slower at 8 am.
    links = _links({"A": (5.0, 1)}, peaks={"A": {8: 1.6}})
    links["traversal_seconds"] += 60.0
    model = ResidualHourCorrection(SegmentMedianTraversal(), TIMEZONE, centred=True)
    model.fit(links)
    base = SegmentMedianTraversal().fit(links)
    n = 28  # one link per day per cell
    segment_mean = 180 / 17  # A's mean residual over 17 hours (median = 360 s)
    shrink = n / (n + 30)
    prediction = model.predict(_requests("A", [8, 12]))
    expected = base.predict(_requests("A", [8, 12])) + shrink * (
        np.array([180.0, 0.0]) - segment_mean
    )
    np.testing.assert_allclose(prediction, expected)
    # The corrections average to 0 over the segment's links: no level shift.
    fitted = model.predict(links) - base.predict(links)
    assert fitted.mean() == pytest.approx(0.0, abs=1e-9)


def test_centred_correction_is_zero_for_unseen_cells_and_segments():
    links = _links({"A": (5.0, 1)}, peaks={"A": {8: 1.4}})
    model = ResidualHourCorrection(SegmentMedianTraversal(), TIMEZONE, centred=True)
    model.fit(links)
    base = SegmentMedianTraversal().fit(links)
    unseen = pd.concat(
        [
            _requests("A", [3]),
            pd.DataFrame(
                {
                    "segment_key": ["Z"],
                    "scheduled_departure": _requests("A", [8])["scheduled_departure"],
                }
            ),
        ],
        ignore_index=True,
    )
    np.testing.assert_array_equal(model.predict(unseen), base.predict(unseen))


def test_centred_residual_hour_model_is_registered():
    model = make_traversal_model("harmonic_residual_hour_centred", TIMEZONE)
    assert isinstance(model, ResidualHourCorrection)
    assert isinstance(model.base, HarmonicTraversal)
    assert model.centred and model.shrinkage == 30
    assert not make_traversal_model("harmonic_residual_hour", TIMEZONE).centred
