import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    make_traversal_comparison_timeseries,
    make_traversal_prediction_scatter,
    prepare_early_traversal_comparison,
)


def _segment():
    return pd.Series(
        {
            "segment_id": 4,
            "trip_direction_id": 0,
            "shape_id": "reference",
            "from_stop_id": "u",
            "to_stop_id": "d",
            "from_stop_name": "Upstream",
            "to_stop_name": "Downstream",
        }
    )


def _updates():
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 5, 6],
            "trip_route_id": ["97-KEL"] * 6,
            "trip_direction_id": [0] * 6,
            "trip_id": ["trip"] * 6,
            "trip_start_timestamp": ["2026-11-03 01:00:00"] * 6,
            "stop_id": ["u", "d"] * 3,
            "stop_sequence": [1, 2] * 3,
            "feed_timestamp": pd.to_datetime(
                [
                    "2026-11-03 08:00Z",
                    "2026-11-03 08:00Z",
                    "2026-11-03 08:30Z",
                    "2026-11-03 08:30Z",
                    "2026-11-03 10:12Z",
                    "2026-11-03 10:12Z",
                ]
            ),
            "arrival_time": pd.to_datetime(
                [
                    "2026-11-03 10:00:30Z",
                    "2026-11-03 10:09:00Z",
                    "2026-11-03 10:01:00Z",
                    "2026-11-03 10:10:00Z",
                    "2026-11-03 10:02:00Z",
                    "2026-11-03 10:11:00Z",
                ]
            ),
            "departure_time": pd.to_datetime(
                [
                    "2026-11-03 10:01:30Z",
                    "2026-11-03 10:10:00Z",
                    "2026-11-03 10:02:00Z",
                    "2026-11-03 10:11:00Z",
                    "2026-11-03 10:03:00Z",
                    "2026-11-03 10:12:00Z",
                ]
            ),
            "arrival_delay": [30, 60, 60, 120, 120, 180],
        }
    )


def _with_good_visit(updates):
    return pd.concat([updates, _updates().assign(trip_id="good")], ignore_index=True)


def test_early_dynamics_and_latest_dwell_adjusted_reference_preserve_inputs():
    updates, segment = _updates(), _segment()
    original, original_segment = updates.copy(deep=True), segment.copy(deep=True)
    result = prepare_early_traversal_comparison(updates, segment)
    row = result.observations.iloc[0]
    assert row.origin_time == pd.Timestamp("2026-11-03 08:00Z")
    assert row.early_upstream_feed_timestamp == row.origin_time
    assert row.early_allowance_seconds == 480
    assert row.early_implied_traversal_seconds == 510
    assert row.early_scheduled_upstream_at == pd.Timestamp("2026-11-03 10:00Z")
    assert row.dwell_seconds == 60
    assert row.reference_traversal_seconds == 480
    assert row.reference_delay_reconstructed_traversal_seconds == 480
    assert row.origin_lead_minutes == 123
    assert row.early_scheduled_arrival_lead_minutes == 128
    assert row.schedule_revision_seconds == 0
    assert row.comparison_exclusion_reason == "included"
    pdt.assert_frame_equal(updates, original)
    pdt.assert_series_equal(segment, original_segment)


def test_earliest_timestamp_uses_greatest_id_without_advancing_to_later_reports():
    updates = _updates()
    tie = updates.iloc[[1]].assign(
        id=99,
        arrival_delay=120,
        arrival_time=pd.Timestamp("2026-11-03 10:10Z"),
    )
    result = prepare_early_traversal_comparison(
        pd.concat([updates, tie], ignore_index=True).iloc[::-1],
        _segment(),
    )
    row = result.observations.iloc[0]
    assert row.early_downstream_id == 99
    assert row.early_implied_traversal_seconds == 570
    assert row.early_upstream_id == 1


def test_equal_timestamp_without_ids_keeps_first_input_row():
    updates = _updates().drop(columns="id")
    tie = updates.iloc[[1]].assign(
        arrival_delay=120,
        arrival_time=pd.Timestamp("2026-11-03 10:10Z"),
    )
    row = prepare_early_traversal_comparison(
        pd.concat([tie, updates], ignore_index=True),
        _segment(),
    ).observations.iloc[0]
    assert row.early_implied_traversal_seconds == 570


def test_future_upstream_reports_do_not_enter_the_origin_estimate():
    updates = _updates()
    future = updates.iloc[[0]].assign(
        id=100,
        feed_timestamp=pd.Timestamp("2026-11-03 08:00:01Z"),
        arrival_time=pd.Timestamp("2026-11-03 10:20Z"),
        arrival_delay=1200,
    )
    row = prepare_early_traversal_comparison(
        pd.concat([updates, future], ignore_index=True),
        _segment(),
    ).observations.iloc[0]
    assert row.early_implied_traversal_seconds == 510
    assert row.early_upstream_id == 1
    assert row.early_upstream_feed_timestamp <= row.origin_time


def test_changing_latest_outcomes_changes_only_reference_information():
    updates = _updates()
    before = prepare_early_traversal_comparison(updates, _segment()).observations
    updates.loc[5, "arrival_time"] += pd.Timedelta(minutes=2)
    updates.loc[5, "departure_time"] += pd.Timedelta(minutes=2)
    updates.loc[5, "feed_timestamp"] += pd.Timedelta(minutes=2)
    updates.loc[5, "arrival_delay"] += 120
    after = prepare_early_traversal_comparison(updates, _segment()).observations
    columns = [column for column in before if column.startswith("early_")]
    pdt.assert_frame_equal(before[columns], after[columns])
    assert after.reference_traversal_seconds.iloc[0] == 600


@pytest.mark.parametrize("column", ["arrival_delay", "arrival_time"])
def test_missing_first_downstream_values_are_not_replaced_by_later_rows(column):
    updates = _updates()
    updates.loc[1, column] = np.nan if column == "arrival_delay" else pd.NaT
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    bad = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert bad.comparison_exclusion_reason == f"missing_early_downstream_{column}"
    assert bad.origin_time == pd.Timestamp("2026-11-03 08:00Z")


def test_missing_selected_upstream_delay_is_not_replaced_by_an_older_row():
    updates = _updates()
    older = updates.iloc[[0]].assign(
        id=0,
        feed_timestamp=pd.Timestamp("2026-11-03 07:59Z"),
    )
    updates.loc[0, "arrival_delay"] = np.nan
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(pd.concat([older, updates], ignore_index=True)),
            _segment(),
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == "missing_early_upstream_arrival_delay"


def test_missing_asof_upstream_history_is_explicit_not_a_future_substitution():
    updates = _updates()
    updates.loc[0, "feed_timestamp"] += pd.Timedelta(seconds=1)
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == "missing_asof_upstream_report"
    assert pd.isna(row.early_upstream_feed_timestamp)


def test_missing_latest_delay_excludes_reference_without_an_older_fallback():
    updates = _updates()
    updates.loc[5, "arrival_delay"] = np.nan
    with pytest.warns(UserWarning, match="identity audit"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    assert result.observations.trip_id.tolist() == ["good"]
    assert (
        result.audit.loc[result.audit.reason.eq("current_missing_delay"), "count"].sum()
        == 1
    )


@pytest.mark.parametrize(
    "column,value,reason",
    [
        ("schedule_relationship", 1, "early_downstream_skipped_or_no_data"),
        ("trip_schedule_relationship", 3, "early_downstream_cancelled_trip"),
    ],
)
def test_early_skipped_or_cancelled_reports_are_audited(column, value, reason):
    updates = _updates()
    updates[column] = 0
    updates.loc[1, column] = value
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == reason


def test_negative_finite_early_predictions_are_retained_and_flagged():
    updates = _updates()
    updates.loc[0, ["arrival_time", "arrival_delay"]] = [
        pd.Timestamp("2026-11-03 10:15Z"),
        900,
    ]
    updates.loc[1, ["arrival_time", "arrival_delay"]] = [
        pd.Timestamp("2026-11-03 10:08Z"),
        0,
    ]
    row = prepare_early_traversal_comparison(updates, _segment()).observations.iloc[0]
    assert row.early_implied_traversal_seconds == -420
    assert row.early_nonpositive_traversal
    assert row.comparison_exclusion_reason == "included"


def test_dated_instances_and_repeated_stop_sequences_do_not_mix():
    updates = _updates()
    repeated = updates.copy()
    repeated["stop_sequence"] += 2
    for column in ("feed_timestamp", "arrival_time", "departure_time"):
        repeated[column] += pd.Timedelta(minutes=30)
    next_day = updates.assign(trip_start_timestamp="2026-11-04 01:00:00").copy()
    for column in ("feed_timestamp", "arrival_time", "departure_time"):
        next_day[column] += pd.Timedelta(days=1)
    result = prepare_early_traversal_comparison(
        pd.concat([updates, repeated, next_day], ignore_index=True),
        _segment(),
    )
    assert len(result.observations) == 3
    assert result.observations.early_implied_traversal_seconds.eq(510).all()
    assert result.observations.trip_instance_key.nunique() == 2


def test_conflicting_identity_and_no_eligible_origin_raise_explicitly():
    updates = _updates()
    conflict = updates.iloc[[1]].assign(stop_sequence=1)
    with pytest.raises(ValueError, match="conflict"):
        prepare_early_traversal_comparison(
            pd.concat([updates, conflict], ignore_index=True),
            _segment(),
        )
    updates.loc[1, "arrival_delay"] = np.nan
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        with pytest.raises(
            ValueError, match="No eligible earliest traversal comparisons"
        ):
            prepare_early_traversal_comparison(updates, _segment())


def test_zero_early_allowance_is_audited_without_replacing_schedule():
    updates = _updates()
    updates.loc[1, ["arrival_time", "arrival_delay"]] = [
        pd.Timestamp("2026-11-03 10:01Z"),
        60,
    ]
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == "nonpositive_early_allowance"


def test_origin_at_departure_is_not_a_predeparture_forecast():
    updates = _updates()
    updates.loc[1, "feed_timestamp"] = pd.Timestamp("2026-11-03 10:03Z")
    updates.loc[3, "feed_timestamp"] = pd.Timestamp("2026-11-03 10:06Z")
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == "origin_not_before_departure"


def test_single_downstream_publication_is_not_an_independent_later_reference():
    updates = _updates().drop(index=[3, 5])
    with pytest.warns(UserWarning, match="earliest traversal comparisons"):
        result = prepare_early_traversal_comparison(
            _with_good_visit(updates), _segment()
        )
    row = result.observations.loc[result.observations.trip_id.eq("trip")].iloc[0]
    assert row.comparison_exclusion_reason == "no_later_downstream_report"


def test_timeseries_plots_exact_scheduled_lead_in_second_shared_row():
    comparison = prepare_early_traversal_comparison(
        _updates(),
        _segment(),
    ).observations.assign(harmonic_traversal_seconds=600)
    original = comparison.copy(deep=True)
    figure = make_traversal_comparison_timeseries(comparison, title="Reserved week")
    np.testing.assert_allclose(figure.data[0].y, [8])
    np.testing.assert_allclose(figure.data[1].y, [8.5])
    np.testing.assert_allclose(figure.data[2].y, [10])
    np.testing.assert_allclose(figure.data[3].y, [128])
    assert figure.data[3].yaxis == "y2"
    assert list(figure.data[0].x) == list(figure.data[3].x)
    assert figure.data[3].x[0] == pd.Timestamp("2026-11-03 02:00")
    assert figure.layout.xaxis.matches == "x2"
    assert figure.layout.xaxis2.rangeslider.visible
    assert figure.layout.yaxis.fixedrange is False
    assert figure.layout.yaxis2.fixedrange is False
    pdt.assert_frame_equal(comparison, original)


def test_prediction_scatter_has_predicted_x_reference_y_and_separate_cohorts():
    training = prepare_early_traversal_comparison(
        _updates(),
        _segment(),
    ).observations.assign(harmonic_traversal_seconds=600)
    evaluation = training.assign(harmonic_traversal_seconds=420)
    originals = training.copy(deep=True), evaluation.copy(deep=True)
    figure = make_traversal_prediction_scatter(
        training,
        evaluation,
        prediction_column="harmonic_traversal_seconds",
        method_name="Harmonic",
    )
    np.testing.assert_allclose(figure.data[0].x, [10])
    np.testing.assert_allclose(figure.data[0].y, [8])
    np.testing.assert_allclose(figure.data[1].x, [7])
    np.testing.assert_allclose(figure.data[1].y, [8])
    assert figure.data[0].xaxis == "x"
    assert figure.data[1].xaxis == "x2"
    assert all(
        shape.x0 == shape.y0 and shape.x1 == shape.y1 for shape in figure.layout.shapes
    )
    assert figure.layout.yaxis.scaleanchor == "x"
    assert figure.layout.yaxis2.scaleanchor == "x2"
    pdt.assert_frame_equal(training, originals[0])
    pdt.assert_frame_equal(evaluation, originals[1])


def test_scatter_does_not_clip_nonpositive_early_predictions():
    frame = pd.DataFrame(
        {
            "trip_instance_key": ["trip"],
            "reference_traversal_seconds": [480],
            "early_implied_traversal_seconds": [-420],
        }
    )
    figure = make_traversal_prediction_scatter(
        frame,
        frame,
        prediction_column="early_implied_traversal_seconds",
        method_name="Earliest feed",
    )
    assert figure.data[0].x[0] == -7
    assert figure.layout.xaxis.range[0] < -7


def test_scatter_metrics_use_each_panels_points_and_minute_units():
    training = pd.DataFrame(
        {
            "trip_instance_key": ["a", "b"],
            "reference_traversal_seconds": [480, 480],
            "prediction_seconds": [600, 420],
        }
    )
    evaluation = pd.DataFrame(
        {
            "trip_instance_key": ["c"],
            "reference_traversal_seconds": [600],
            "prediction_seconds": [480],
        }
    )
    figure = make_traversal_prediction_scatter(
        training,
        evaluation,
        prediction_column="prediction_seconds",
        method_name="Test",
    )
    metrics = [
        annotation
        for annotation in figure.layout.annotations
        if annotation.xref in ("x domain", "x2 domain")
    ]
    assert len(metrics) == 2
    assert metrics[0].text == (
        "<b>N=2</b><br>MAE=1.500 | MedAE=1.500 min" "<br>RMSE=1.581 | Bias=+0.500 min"
    )
    assert metrics[1].text == (
        "<b>N=1</b><br>MAE=2.000 | MedAE=2.000 min" "<br>RMSE=2.000 | Bias=-2.000 min"
    )
    assert all(annotation.y < 0 for annotation in metrics)


@pytest.mark.parametrize(
    "problem", ["empty", "missing_column", "nonfinite", "naive_time"]
)
def test_timeseries_invalid_plot_data_is_explicit(problem):
    frame = prepare_early_traversal_comparison(
        _updates(),
        _segment(),
    ).observations.assign(harmonic_traversal_seconds=600)
    if problem == "empty":
        frame = frame.iloc[:0]
    elif problem == "missing_column":
        frame = frame.drop(columns="early_scheduled_arrival_lead_minutes")
    elif problem == "nonfinite":
        frame.loc[0, "harmonic_traversal_seconds"] = np.nan
    else:
        frame["origin_time"] = frame.origin_time.dt.tz_localize(None)
    with pytest.raises(ValueError):
        make_traversal_comparison_timeseries(frame, title="Invalid comparison")
