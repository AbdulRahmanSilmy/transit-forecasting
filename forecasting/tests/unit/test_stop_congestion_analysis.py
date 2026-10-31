from dataclasses import replace

import pandas as pd
import pandas.testing as pdt
import pytest
from shapely.geometry import LineString

from forecasting.road_segment_congestion import CongestionResult
from forecasting.stop_congestion_analysis import (
    prepare_stop_congestion_analysis,
    summarize_stop_congestion_signal,
)
from forecasting.stop_congestion_display import (
    make_stop_congestion_cohort_figures,
    make_stop_congestion_example,
)


def _inputs():
    segments = pd.DataFrame(
        {
            "segment_id": [40, 7, 92, 99],
            "trip_direction_id": [0, 0, 0, 1],
            "shape_id": ["out", "out", "out", "back"],
            "from_stop_id": ["a", "b", "p", "p"],
            "to_stop_id": ["b", "p", "d", "b"],
            "from_stop_name": ["Origin", "Before", "Parkinson", "Parkinson"],
            "to_stop_name": ["Before", "Parkinson", "After", "Before"],
            "to_stop_sequence": [100, 200, 300, 400],
        }
    )
    probes = pd.DataFrame(
        [
            {
                "segment_id": segment,
                "time_bin": pd.Timestamp(f"2026-09-11 10:{minute:02}:00Z"),
                "trip_id": "other",
                "trip_start_timestamp": "2026-09-11 09:00:00",
                "vehicle_id": "external",
                "feed_timestamp": pd.Timestamp(f"2026-09-11 10:{minute + 1:02}:00Z"),
                "read_timestamp": pd.Timestamp(f"2026-09-11 10:{minute + 1:02}:03Z"),
                "timestamp": pd.Timestamp(f"2026-09-11 10:{minute:02}:59Z"),
            }
            for minute in (0, 5)
            for segment in (40, 7, 92, 99)
        ]
    )
    congestion = probes[["segment_id", "time_bin"]].copy()
    congestion["baseline_speed"] = 10.0
    congestion["congestion_index"] = [0.2, 0.4, 0.9, 0.99, 0.7, 0.8, 0.9, 0.99]
    result = CongestionResult(
        "97-KEL",
        "target",
        LineString([(0, 0), (1, 1)]),
        segments,
        probes,
        pd.DataFrame(),
        congestion,
    )
    targets = pd.DataFrame(
        {
            "visit_index": [235, 235],
            "trip_id": ["target", "target"],
            "trip_start_timestamp": ["2026-09-11 09:00:00"] * 2,
            "trip_direction_id": [0, 0],
            "stop_id": ["p", "p"],
            "stop_sequence": [9, 9],
            "horizon_minutes": [10, 15],
            "reference_arrival_time": pd.to_datetime(["2026-09-11 10:20Z"] * 2),
            "cutoff_time": pd.to_datetime(["2026-09-11 10:10Z", "2026-09-11 10:05Z"]),
            "true_delay_minutes": [19.5, 19.5],
            "estimated_delay_minutes": [15.0, 10.0],
        }
    )
    vehicles = pd.DataFrame(
        {
            "trip_route_id": ["97-KEL"],
            "trip_direction_id": [0],
            "trip_id": ["target"],
            "trip_start_timestamp": ["2026-09-11 09:00:00"],
            "stop_id": ["b"],
            "current_stop_sequence": [4],
            "current_status": [1],
            "feed_timestamp": pd.to_datetime(["2026-09-11 10:03Z"]),
            "read_timestamp": pd.to_datetime(["2026-09-11 10:03:03Z"]),
            "timestamp": pd.to_datetime(["2026-09-11 10:02:59Z"]),
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["target"] * 4,
            "stop_id": ["a", "b", "p", "d"],
            "stop_sequence": [1, 4, 9, 20],
        }
    )
    trips = pd.DataFrame(
        {
            "trip_id": ["target"],
            "route_id": ["97-KEL"],
            "direction_id": [0],
            "shape_id": ["out"],
        }
    )
    return result, targets, vehicles, stop_times, trips


def _summary(analysis, scope="corridor", horizon=10, age=15):
    rows = analysis.comparisons
    return rows.loc[
        rows["scope"].eq(scope)
        & rows["horizon_minutes"].eq(horizon)
        & rows["max_age_minutes"].eq(age)
    ].iloc[0]


def test_trip_specific_order_includes_incoming_and_excludes_downstream_and_reverse():
    analysis = prepare_stop_congestion_analysis(*_inputs())
    assert analysis.paths["segment_id"].tolist() == [40, 7]
    assert analysis.paths["path_stop_sequence"].tolist() == [4, 9]
    assert analysis.paths["path_order"].tolist() == [1, 2]
    assert analysis.comparisons["visit_index"].eq(235).all()
    row = _summary(analysis)
    assert row["mean_congestion"] == pytest.approx(0.75)
    assert row["remaining_delay_error_minutes"] == 4.5
    assert row["coverage_percent"] == 100
    assert row["probe_count"] == 2
    assert not row["target_bus_contributed"]
    assert not row["contribution_unknown"]
    earlier = _summary(analysis, horizon=15)
    assert earlier["mean_congestion"] == pytest.approx(0.3)


@pytest.mark.parametrize("status,expected", [(0, 2), (1, 1), (2, 2), ("STOPPED_AT", 1)])
def test_remaining_path_respects_current_status(status, expected):
    result, targets, vehicles, stop_times, trips = _inputs()
    vehicles["current_status"] = status
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert _summary(analysis, "remaining")["segment_count"] == expected


def test_exact_bin_end_and_late_receipt_are_not_available_at_bin_start():
    result, targets, vehicles, stop_times, trips = _inputs()
    targets = targets.iloc[[0]].copy()
    targets["cutoff_time"] = pd.Timestamp("2026-09-11 10:04:59Z")
    targets["reference_arrival_time"] = targets["cutoff_time"] + pd.Timedelta("10min")
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert pd.isna(_summary(analysis)["mean_congestion"])
    result.vehicle_probes.loc[
        result.vehicle_probes["time_bin"].eq(pd.Timestamp("2026-09-11 10:00Z")),
        "read_timestamp",
    ] = pd.Timestamp("2026-09-11 10:05:01Z")
    targets["cutoff_time"] = pd.Timestamp("2026-09-11 10:05Z")
    targets["reference_arrival_time"] = targets["cutoff_time"] + pd.Timedelta("10min")
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert pd.isna(_summary(analysis)["mean_congestion"])


def test_stale_is_missing_not_zero_and_fresh_zero_is_preserved():
    result, targets, vehicles, stop_times, trips = _inputs()
    result.congestion["congestion_index"] = 0.0
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert _summary(analysis)["mean_congestion"] == 0
    targets["cutoff_time"] += pd.Timedelta("30min")
    targets["reference_arrival_time"] += pd.Timedelta("30min")
    with pytest.warns(UserWarning, match="no fresh"):
        analysis = prepare_stop_congestion_analysis(
            result, targets, vehicles, stop_times, trips
        )
    assert pd.isna(_summary(analysis)["mean_congestion"])
    assert pd.isna(_summary(analysis, "remaining")["segment_count"])


def test_other_service_day_does_not_supply_remaining_position():
    result, targets, vehicles, stop_times, trips = _inputs()
    vehicles["trip_start_timestamp"] = "2026-09-10 09:00:00"
    with pytest.warns(UserWarning, match="no fresh"):
        analysis = prepare_stop_congestion_analysis(
            result, targets, vehicles, stop_times, trips
        )
    assert _summary(analysis)["coverage_percent"] == 100
    assert pd.isna(_summary(analysis, "remaining")["mean_congestion"])


def test_missing_start_retains_corridor_but_not_remaining_path():
    result, targets, vehicles, stop_times, trips = _inputs()
    targets["trip_start_timestamp"] = pd.NA
    with pytest.warns(UserWarning):
        analysis = prepare_stop_congestion_analysis(
            result, targets, vehicles, stop_times, trips
        )
    assert _summary(analysis)["mean_congestion"] == pytest.approx(0.75)
    assert pd.isna(_summary(analysis, "remaining")["mean_congestion"])


def test_future_probes_and_positions_do_not_change_earlier_selections():
    inputs = _inputs()
    expected = prepare_stop_congestion_analysis(*inputs)
    result, targets, vehicles, stop_times, trips = inputs
    future_probes = result.vehicle_probes.copy()
    for column in ("time_bin", "feed_timestamp", "read_timestamp", "timestamp"):
        future_probes[column] += pd.Timedelta("1day")
    future_congestion = result.congestion.copy()
    future_congestion["time_bin"] += pd.Timedelta("1day")
    future_congestion["congestion_index"] = 1.0
    future_vehicles = vehicles.copy()
    for column in ("feed_timestamp", "read_timestamp", "timestamp"):
        future_vehicles[column] += pd.Timedelta("1day")
    changed = replace(
        result,
        vehicle_probes=pd.concat([result.vehicle_probes, future_probes]),
        congestion=pd.concat([result.congestion, future_congestion]),
    )
    actual = prepare_stop_congestion_analysis(
        changed, targets, pd.concat([vehicles, future_vehicles]), stop_times, trips
    )
    pdt.assert_frame_equal(expected.comparisons, actual.comparisons)


def test_preserves_inputs_and_rejects_direction_and_future_predictions():
    inputs = _inputs()
    originals = [frame.copy(deep=True) for frame in inputs[1:]]
    prepare_stop_congestion_analysis(*inputs)
    for original, actual in zip(originals, inputs[1:]):
        pdt.assert_frame_equal(original, actual)
    result, targets, vehicles, stop_times, trips = inputs
    targets["trip_direction_id"] = 1
    with pytest.raises(ValueError, match="direction 0"):
        prepare_stop_congestion_analysis(*inputs)
    targets["trip_direction_id"] = 0
    targets["estimate_feed_timestamp"] = targets["cutoff_time"] + pd.Timedelta("1s")
    with pytest.raises(ValueError, match="published after"):
        prepare_stop_congestion_analysis(*inputs)


def test_no_static_path_is_an_explicit_failure():
    result, targets, vehicles, stop_times, trips = _inputs()
    trips["shape_id"] = "unmatched"
    with pytest.warns(UserWarning, match="Excluded"):
        with pytest.raises(ValueError, match="No visits"):
            prepare_stop_congestion_analysis(
                result, targets, vehicles, stop_times, trips
            )


def test_target_bus_contribution_is_identified_without_treating_unknown_as_external():
    result, targets, vehicles, stop_times, trips = _inputs()
    result.vehicle_probes["trip_id"] = "target"
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert _summary(analysis)["target_bus_contributed"]
    result.vehicle_probes["trip_start_timestamp"] = pd.NA
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    assert _summary(analysis)["contribution_unknown"]
    assert not _summary(analysis)["target_bus_contributed"]


def test_common_cohort_and_rank_associations_use_one_visit_per_horizon():
    analysis = prepare_stop_congestion_analysis(*_inputs())
    frames = []
    for visit in range(12):
        frame = analysis.comparisons.copy()
        frame["visit_index"] = visit
        frame["mean_congestion"] = visit / 12
        frame["remaining_delay_error_minutes"] = visit
        frames.append(frame)
    comparisons = pd.concat(frames, ignore_index=True)
    # One visit is absent at one freshness/horizon combination.
    comparisons.loc[
        comparisons["visit_index"].eq(0)
        & comparisons["horizon_minutes"].eq(15)
        & comparisons["max_age_minutes"].eq(5),
        "mean_congestion",
    ] = float("nan")
    summary = summarize_stop_congestion_signal(
        comparisons, horizons_minutes=[10, 15], minimum_pairs=10
    )
    common = summary.loc[
        summary["subset"].eq("common_visits") & summary["feature"].eq("mean_congestion")
    ]
    assert common["paired_visits"].eq(11).all()
    assert common["spearman"].eq(1).all()
    low_support = summarize_stop_congestion_signal(
        analysis.comparisons, horizons_minutes=[10, 15]
    )
    assert low_support["spearman"].isna().all()


def test_plot_shapes_path_order_gaps_and_empty_remaining_cohort():
    analysis = prepare_stop_congestion_analysis(*_inputs())
    figure = make_stop_congestion_example(analysis, 235)
    heatmap = next(trace for trace in figure.data if trace.type == "heatmap")
    assert list(heatmap.x) == [10, 15]
    assert list(heatmap.z[0]) == [0.7, 0.2]
    assert list(heatmap.z[1]) == [0.8, 0.4]
    figures = make_stop_congestion_cohort_figures(analysis, horizons_minutes=[15, 10])
    assert len(figures) == 5
    for item in figures.values():
        assert item.to_json()
    result, targets, vehicles, stop_times, trips = _inputs()
    vehicles["trip_start_timestamp"] = "previous-day"
    with pytest.warns(UserWarning, match="no fresh"):
        missing = prepare_stop_congestion_analysis(
            result, targets, vehicles, stop_times, trips
        )
    figures = make_stop_congestion_cohort_figures(missing, horizons_minutes=[15, 10])
    assert any(
        "n=0" in a.text for a in figures["horizon_associations"].layout.annotations
    )
    with pytest.raises(ValueError, match="not in the prepared reference visits"):
        make_stop_congestion_example(analysis, 999)
    with pytest.raises(ValueError, match="freshness"):
        make_stop_congestion_example(analysis, 235, max_age_minutes=1)


def test_stopped_at_target_has_empty_remaining_path_not_zero_congestion():
    result, targets, vehicles, stop_times, trips = _inputs()
    vehicles["stop_id"] = "p"
    vehicles["current_stop_sequence"] = 9
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips
    )
    remaining = _summary(analysis, "remaining")
    assert remaining["scope_available"]
    assert remaining["segment_count"] == 0
    assert pd.isna(remaining["mean_congestion"])
    assert pd.isna(remaining["coverage_percent"])


def test_late_old_position_cannot_override_newer_published_position():
    result, targets, vehicles, stop_times, trips = _inputs()
    old = vehicles.copy()
    old["feed_timestamp"] = pd.Timestamp("2026-09-11 10:02Z")
    old["timestamp"] = pd.Timestamp("2026-09-11 10:01:59Z")
    old["read_timestamp"] = pd.Timestamp("2026-09-11 10:04Z")
    old["stop_id"] = "a"
    old["current_stop_sequence"] = 1
    analysis = prepare_stop_congestion_analysis(
        result, targets, pd.concat([vehicles, old]), stop_times, trips
    )
    assert _summary(analysis, "remaining")["segment_count"] == 1


@pytest.mark.parametrize("limit,observed", [(4, False), (5, True)])
def test_freshness_limit_is_inclusive(limit, observed):
    result, targets, vehicles, stop_times, trips = _inputs()
    # At 10:10 the newest completed bin's last measurement is exactly 10:05.
    result.vehicle_probes.loc[
        result.vehicle_probes["time_bin"].eq(pd.Timestamp("2026-09-11 10:05Z")),
        "timestamp",
    ] = pd.Timestamp("2026-09-11 10:05Z")
    analysis = prepare_stop_congestion_analysis(
        result, targets, vehicles, stop_times, trips, freshness_minutes=[limit]
    )
    summary = _summary(analysis, age=limit)
    assert bool(pd.notna(summary["mean_congestion"])) == observed


@pytest.mark.parametrize(
    "missing_metadata,expected_reason",
    [
        ("trip", "Trip ID is absent"),
        ("stop", "Target stop/sequence is missing or ambiguous"),
        ("shape", "Upstream stop pairs have no exact shape/direction match"),
    ],
)
def test_excluded_example_keeps_its_own_delay_curves_without_substituting_a_path(
    missing_metadata, expected_reason
):
    result, targets, vehicles, stop_times, trips = _inputs()
    excluded = targets.copy()
    excluded["visit_index"] = 667
    excluded["trip_id"] = "excluded-trip"
    excluded["true_delay_minutes"] = 19.6
    excluded["estimated_delay_minutes"] = 10.4
    if missing_metadata != "trip":
        extra_trip = trips.copy()
        extra_trip["trip_id"] = "excluded-trip"
        if missing_metadata == "shape":
            extra_trip["shape_id"] = "unmatched"
        trips = pd.concat([trips, extra_trip], ignore_index=True)
        extra_stops = stop_times.copy()
        extra_stops["trip_id"] = "excluded-trip"
        if missing_metadata == "stop":
            extra_stops.loc[extra_stops["stop_id"].eq("p"), "stop_id"] = "another-stop"
        stop_times = pd.concat([stop_times, extra_stops], ignore_index=True)
    mixed = pd.concat([targets, excluded], ignore_index=True)
    original = mixed.copy(deep=True)
    with pytest.warns(UserWarning, match="Excluded 1 visits"):
        analysis = prepare_stop_congestion_analysis(
            result, mixed, vehicles, stop_times, trips
        )
    pdt.assert_frame_equal(mixed, original)
    references = analysis.reference_horizons
    missing = references.loc[references["visit_index"].eq(667)]
    assert len(missing) == 2
    assert not missing["path_available"].any()
    assert missing["path_exclusion_reason"].str.startswith(expected_reason).all()
    assert references.loc[references["visit_index"].eq(235), "path_available"].all()
    assert analysis.paths["visit_index"].eq(235).all()
    assert analysis.comparisons["visit_index"].eq(235).all()
    with pytest.warns(UserWarning, match="Visit 667: upstream congestion unavailable"):
        figure = make_stop_congestion_example(analysis, 667)
    assert len(figure.data) == 3
    assert all(trace.type == "scatter" for trace in figure.data)
    assert list(figure.data[0].y) == [19.6, 19.6]
    assert list(figure.data[1].y) == [10.4, 10.4]
    assert list(figure.data[2].y) == pytest.approx([9.2, 9.2])
    assert "Visit 667" in figure.layout.title.text
    assert "upstream congestion unavailable" in figure.layout.title.text
    assert any(expected_reason in note.text for note in figure.layout.annotations)
    assert any("not zero congestion" in note.text for note in figure.layout.annotations)
    assert figure.to_json()
