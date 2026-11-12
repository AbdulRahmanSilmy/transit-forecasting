from dataclasses import replace

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    SegmentTimingEDA,
    StopDynamicsEDA,
    fit_stop_dynamics_model,
    make_stop_dynamics_model_figure,
)


@pytest.mark.parametrize("split", ["train", "holdout"])
def test_model_figure_uses_only_requested_visits_with_correct_units(split):
    analysis, timing = _inputs()
    result = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    original = result.observations.copy(deep=True)
    visits = original.loc[original["split"].eq(split)].sort_values("arrival_time")
    figure = make_stop_dynamics_model_figure(result, split=split)
    assert len(figure.data) == 4
    assert (
        list(figure.data[0].x)
        == visits.arrival_time.dt.tz_convert("America/Vancouver").tolist()
    )
    np.testing.assert_allclose(figure.data[0].y, visits.arrival_delay / 60)
    np.testing.assert_allclose(figure.data[1].y, visits.predicted_delay_seconds / 60)
    np.testing.assert_allclose(figure.data[3].y, visits.residual_seconds / 60)
    assert figure.data[3].yaxis == "y2"
    pdt.assert_frame_equal(result.observations, original)


def test_model_figure_rejects_unknown_and_empty_splits():
    analysis, timing = _inputs()
    result = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    with pytest.raises(ValueError, match="split must"):
        make_stop_dynamics_model_figure(result, split="all")
    empty = replace(result, observations=result.observations.iloc[:0])
    with pytest.raises(ValueError, match="No visits"):
        make_stop_dynamics_model_figure(empty)


def _inputs():
    rng = np.random.default_rng(97)
    n = 60
    days = pd.date_range("2026-10-01", periods=10, tz="America/Vancouver")
    hours = np.tile([8, 8, 9, 9, 10, 10], 10)
    upstream = pd.DatetimeIndex(
        [
            days[i // 6] + pd.Timedelta(hours=int(hours[i]), minutes=i % 6)
            for i in range(n)
        ]
    ).tz_convert("UTC")
    traversal = rng.uniform(500, 900, n)
    dwell = rng.uniform(0, 90, n)
    dwell[0] = 0
    delays = rng.uniform(-60, 600, n)
    durations = pd.DataFrame({"hour": hours, "traversal": traversal, "dwell": dwell})
    baselines = durations.iloc[:48].groupby("hour")[["traversal", "dwell"]].median()
    target = (
        0.8 * delays
        + 1.1 * (traversal - baselines.traversal.reindex(hours).to_numpy())
        + 0.3 * (dwell - baselines.dwell.reindex(hours).to_numpy())
        - 25
    )
    arrival = upstream + pd.to_timedelta(traversal + dwell, unit="s")
    keys = pd.DataFrame(
        {
            "trip_instance_key": [f"journey-{i}" for i in range(n)],
            "stop_id": "b",
            "stop_sequence": 20,
            "from_stop_id": "a",
            "from_stop_sequence": 10,
        }
    )
    observations = keys.assign(
        arrival_time=arrival,
        upstream_arrival_time=upstream,
        feed_timestamp=arrival + pd.Timedelta(seconds=10),
        arrival_delay=target,
        traversal_seconds=traversal,
        dwell_seconds=dwell,
        traversal_exclusion_reason="included",
        dwell_exclusion_reason="included",
    )
    pairs = keys.assign(
        upstream_arrival_delay=delays,
        upstream_feed_timestamp=upstream + pd.Timedelta(seconds=10),
        exclusion_reason="included",
    )
    segment = pd.Series(
        {
            "segment_id": 4,
            "trip_direction_id": 0,
            "shape_id": "51737",
            "from_stop_id": "a",
            "to_stop_id": "b",
        }
    )
    empty = pd.DataFrame()
    analysis = StopDynamicsEDA(
        segment=segment,
        speed_bins=empty,
        stop_visits=empty,
        upstream_visits=empty,
        delay_changes=pairs,
        delay_bins=empty,
        audit=empty,
        coverage=empty,
        recording_start=upstream.min(),
        recording_end=arrival.max(),
        time_bin_size="5min",
    )
    timing = SegmentTimingEDA(segment, observations, empty, empty)
    return analysis, timing


def test_recovers_equation_and_scores_same_holdout_visits_without_mutation():
    analysis, timing = _inputs()
    original_pairs = analysis.delay_changes.copy(deep=True)
    original_timings = timing.observations.copy(deep=True)
    result = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    assert result.coefficients.loc[
        ["alpha_d", "alpha_t", "alpha_w", "constant_seconds"]
    ].to_numpy() == pytest.approx([0.8, 1.1, 0.3, 25])
    assert result.holdout_start == pd.Timestamp("2026-10-09", tz="America/Vancouver")
    assert result.observations.groupby("split").size().to_dict() == {
        "holdout": 12,
        "train": 48,
    }
    assert result.metrics.query("model == 'equation'").mae_seconds.max() < 1e-9
    assert result.metrics.query("split == 'holdout'").n.eq(12).all()
    assert result.metrics.query("model == 'persistence'").mae_seconds.min() > 10
    assert result.observations.dwell_seconds.eq(0).sum() == 1
    pdt.assert_frame_equal(analysis.delay_changes, original_pairs)
    pdt.assert_frame_equal(timing.observations, original_timings)


def test_holdout_values_cannot_change_coefficients_or_hourly_baselines():
    analysis, timing = _inputs()
    original = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    timing.observations.loc[
        48:, ["arrival_delay", "traversal_seconds", "dwell_seconds"]
    ] += 500
    analysis.delay_changes.loc[48:, "upstream_arrival_delay"] += 1000
    changed = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    pdt.assert_series_equal(original.coefficients, changed.coefficients)
    pdt.assert_frame_equal(original.hourly_baselines, changed.hourly_baselines)
    assert (
        changed.metrics.query(
            "split == 'holdout' and model == 'equation'"
        ).mae_seconds.iloc[0]
        > 100
    )


def test_excludes_bad_pairs_durations_and_late_training_publications():
    analysis, timing = _inputs()
    analysis.delay_changes.loc[0, "exclusion_reason"] = "upstream_missing_latest_delay"
    timing.observations.loc[1, "dwell_exclusion_reason"] = "missing_upstream_arrival"
    timing.observations.loc[2, "traversal_exclusion_reason"] = "nonpositive_traversal"
    timing.observations.loc[3, "arrival_delay"] = np.nan
    timing.observations.loc[4, "feed_timestamp"] = pd.Timestamp("2026-10-09 07:00Z")
    analysis.delay_changes.loc[5, "upstream_feed_timestamp"] = pd.Timestamp(
        "2026-10-09 07:00Z"
    )
    analysis.delay_changes.loc[6, "trip_instance_key"] = "journey-48"
    timing.observations.loc[6, "trip_instance_key"] = "journey-48"
    for frame in (analysis.delay_changes, timing.observations):
        frame.loc[6, ["stop_sequence", "from_stop_sequence"]] = [40, 30]
    with pytest.warns(UserWarning, match="excluded"):
        result = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    reasons = result.audit.set_index("reason")["count"]
    assert reasons["delay_upstream_missing_latest_delay"] == 1
    assert reasons["dwell_missing_upstream_arrival"] == 1
    assert reasons["traversal_nonpositive_traversal"] == 1
    assert reasons["missing_or_nonfinite_measurement"] == 1
    assert reasons["training_publication_reaches_holdout"] == 2
    assert reasons["training_journey_in_holdout"] == 1
    assert (
        result.observations.query("split == 'train'")
        .feed_timestamp.lt(result.holdout_start)
        .all()
    )


def test_unobserved_holdout_hour_uses_explicit_training_only_fallback():
    analysis, timing = _inputs()
    timing.observations.loc[48:, "upstream_arrival_time"] += pd.Timedelta(hours=5)
    with pytest.warns(UserWarning, match="unobserved training hours"):
        result = fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    heldout = result.observations.query("split == 'holdout'")
    assert heldout.baseline_source.eq("global_training_median").all()
    assert heldout.training_count.eq(0).all()
    assert heldout.baseline_traversal_seconds.eq(
        timing.observations.iloc[:48].traversal_seconds.median()
    ).all()


@pytest.mark.parametrize("holdout_days", [0, -1, True, 1.5, 10])
def test_rejects_invalid_or_impossible_holdout(holdout_days):
    analysis, timing = _inputs()
    with pytest.raises(ValueError, match="holdout|training arrival days"):
        fit_stop_dynamics_model(analysis, timing, holdout_days=holdout_days)


def test_rejects_unidentifiable_predictors_and_mismatched_segments():
    analysis, timing = _inputs()
    timing.observations["dwell_seconds"] = 0
    with pytest.raises(ValueError, match="rank deficient"):
        fit_stop_dynamics_model(analysis, timing, holdout_days=2)
    wrong_segment = timing.segment.copy()
    wrong_segment["segment_id"] = 5
    with pytest.raises(ValueError, match="same segment"):
        fit_stop_dynamics_model(
            analysis, replace(timing, segment=wrong_segment), holdout_days=2
        )
