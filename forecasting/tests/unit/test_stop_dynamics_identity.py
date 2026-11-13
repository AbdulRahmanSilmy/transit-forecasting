import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    evaluate_stop_dynamics_identity,
    make_stop_dynamics_identity_figure,
)


def _updates():
    rows = []
    for day in range(6):
        arrival = pd.Timestamp("2026-10-01 10:00Z") + pd.Timedelta(days=day)
        for seq, stop, seconds, delay in ((4, "a", 0, 120), (5, "b", 360, 180)):
            rows.append(
                {
                    "id": len(rows),
                    "trip_route_id": "97-KEL",
                    "trip_direction_id": 0,
                    "trip_id": "historical-not-in-GTFS",
                    "trip_start_timestamp": f"2026-10-{day + 1:02d} 02:00:00",
                    "stop_sequence": seq,
                    "stop_id": stop,
                    "arrival_time": arrival + pd.Timedelta(seconds=seconds),
                    "departure_time": arrival
                    + pd.Timedelta(seconds=seconds + (0 if day == 0 else 30)),
                    "arrival_delay": delay,
                    "feed_timestamp": arrival
                    + pd.Timedelta(minutes=-1 if seq == 4 else 10),
                    "schedule_relationship": 0,
                    "trip_schedule_relationship": 0,
                }
            )
    return pd.DataFrame(rows)


def _segment():
    return pd.Series(
        {
            "segment_id": 4,
            "trip_direction_id": 0,
            "shape_id": "current-shape",
            "from_stop_id": "a",
            "to_stop_id": "b",
            "from_stop_name": "Upstream",
            "to_stop_name": "Current",
        }
    )


def test_identity_recovers_allowance_from_historical_ids_without_fitting_or_mutation():
    updates, segment = _updates(), _segment()
    original_updates, original_segment = updates.copy(deep=True), segment.copy(
        deep=True
    )
    result = evaluate_stop_dynamics_identity(updates, segment)
    assert len(result.observations) == 6
    assert result.observations.inferred_allowance_seconds.eq(300).all()
    assert result.observations.identity_delay_seconds.eq(180).all()
    assert result.observations.residual_seconds.eq(0).all()
    assert result.observations.dwell_seconds.eq(0).sum() == 1
    assert result.observations.delta_delay_seconds.eq(60).all()
    assert {
        "split",
        "baseline_source",
        "predicted_delay_seconds",
        "persistence_delay_seconds",
        "mean_change_delay_seconds",
    }.isdisjoint(result.observations.columns)
    assert pd.isna(result.segment.shape_id)
    assert result.segment.reference_shape_id == "current-shape"
    pdt.assert_frame_equal(updates, original_updates)
    pdt.assert_series_equal(segment, original_segment)
    figure = make_stop_dynamics_identity_figure(result)
    assert all(len(trace.x) == 6 for trace in figure.data)
    np.testing.assert_allclose(figure.data[1].y, 3)
    np.testing.assert_allclose(figure.data[2].y, 1)
    np.testing.assert_allclose(figure.data[3].y, 1)


def test_identity_is_target_dependent_and_latest_missing_values_are_not_backfilled():
    updates = _updates()
    original = evaluate_stop_dynamics_identity(updates, _segment())
    updates.loc[11, "arrival_delay"] = 210
    changed = evaluate_stop_dynamics_identity(updates, _segment())
    assert changed.observations.iloc[-1].inferred_allowance_seconds == 270
    assert changed.observations.iloc[-1].identity_delay_seconds == 210
    assert original.observations.iloc[-1].identity_delay_seconds == 180
    last = updates.iloc[[11]].copy()
    last["id"] = 100
    last["arrival_delay"] = np.nan
    missing = pd.concat([updates, last], ignore_index=True)
    with pytest.warns(UserWarning, match="excluded"):
        result = evaluate_stop_dynamics_identity(missing, _segment())
    assert len(result.observations) == 5
    assert result.audit.query("reason == 'current_missing_delay'")["count"].iloc[0] == 1


@pytest.mark.parametrize(
    "change, expected",
    [
        ("missing_start", "missing_trip_start"),
        ("gap", "missing_consecutive_upstream_visit"),
        ("skip", "upstream_skipped_or_no_data"),
        ("cancel", "upstream_cancelled_trip"),
        ("negative_dwell", "negative_dwell"),
        ("unfinished", "current_unfinished_arrival"),
        ("negative_allowance", "nonpositive_inferred_allowance"),
    ],
)
def test_excludes_unreliable_or_invalid_pairs(change, expected):
    updates = _updates()
    if change == "missing_start":
        updates.loc[0, "trip_start_timestamp"] = None
    elif change == "gap":
        updates.loc[0, "stop_sequence"] = 3
    elif change == "skip":
        updates.loc[0, "schedule_relationship"] = 1
    elif change == "cancel":
        updates.loc[0, "trip_schedule_relationship"] = 3
    elif change == "negative_dwell":
        updates.loc[0, "departure_time"] -= pd.Timedelta(seconds=1)
    elif change == "unfinished":
        updates.loc[1, "arrival_time"] = pd.Timestamp("2026-10-07 10:00Z")
    else:
        updates.loc[1, "arrival_delay"] = 600
    with pytest.warns(UserWarning, match="excluded"):
        result = evaluate_stop_dynamics_identity(updates, _segment())
    assert expected in result.audit.reason.to_list()
    assert len(result.observations) == 5


def test_rejects_conflicting_sequence_identity():
    updates = _updates()
    updates.loc[0, "stop_sequence"] = 5
    with pytest.raises(ValueError, match="conflict"):
        evaluate_stop_dynamics_identity(updates, _segment())


def test_single_day_identity_requires_no_split_or_training_period():
    result = evaluate_stop_dynamics_identity(_updates().iloc[:2], _segment())
    assert len(result.observations) == 1
    assert result.observations.identity_delay_seconds.iloc[0] == 180
