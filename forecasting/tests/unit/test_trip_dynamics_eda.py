from dataclasses import replace

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    make_trip_dynamics_figure,
    prepare_segment_timing_eda,
    prepare_stop_dynamics_eda,
    prepare_trip_dynamics_eda,
    select_trip_dynamics_examples,
)


@pytest.fixture
def inputs():
    stops = pd.DataFrame(
        {
            "stop_id": ["a", "b", "c", "d"],
            "stop_name": ["First", "Second", "Third", "Fourth"],
        }
    )
    times = pd.DataFrame(
        {
            "trip_id": ["main"] * 4,
            "stop_id": ["a", "b", "c", "d"],
            "stop_sequence": [10, 20, 40, 80],
        }
    )
    trips = pd.DataFrame(
        {
            "route_id": ["97-KEL"],
            "trip_id": ["main"],
            "direction_id": [0],
            "shape_id": ["forward"],
        }
    )
    updates, probes = [], []
    for index, delays in enumerate(
        [
            [60, 120, 420, 360],
            [-300, -120, -240, -60],
            [0, 60, 180, 120],
        ]
    ):
        base = pd.Timestamp("2026-09-08 10:00Z") + pd.Timedelta(days=index)
        arrivals = pd.date_range(base, periods=4, freq="2min")
        start = base.tz_convert("America/Vancouver").strftime("%Y-%m-%d %H:%M")
        updates.append(
            pd.DataFrame(
                {
                    "id": np.arange(4) + index * 10,
                    "trip_route_id": ["97-KEL"] * 4,
                    "trip_direction_id": [0] * 4,
                    "trip_id": ["main"] * 4,
                    "trip_start_timestamp": [start] * 4,
                    "stop_id": ["a", "b", "c", "d"],
                    "stop_sequence": [10, 20, 40, 80],
                    "arrival_delay": delays,
                    "arrival_time": arrivals,
                    "departure_time": arrivals
                    + pd.to_timedelta(np.arange(4) * 5 + 20 * (index + 1), unit="s"),
                    "feed_timestamp": [
                        arrivals[0] - pd.Timedelta(seconds=10),
                        *[t + pd.Timedelta(seconds=10) for t in arrivals[1:]],
                    ],
                }
            )
        )
        probes.append(
            pd.DataFrame(
                {
                    "trip_route_id": ["97-KEL"] * 4,
                    "trip_direction_id": [0] * 4,
                    "trip_id": ["main"] * 4,
                    "trip_start_timestamp": [start] * 4,
                    "stop_id": ["b", "b", "c", "d"],
                    "current_stop_sequence": [20, 20, 40, 80],
                    "position_speed": [5 + index, 15 + index, 10 + index, 20 + index],
                    "vehicle_id": [f"bus-{index}"] * 4,
                    "feed_timestamp": [base + pd.Timedelta(minutes=1)] * 4,
                }
            )
        )
    return (
        pd.concat(probes, ignore_index=True),
        pd.concat(updates, ignore_index=True),
        stops,
        times,
        trips,
    )


def _journey(analysis, start="2026-09-08 03:00"):
    return analysis.visits.loc[
        analysis.visits.trip_start_timestamp.eq(start)
    ].sort_values("position")


def test_full_journeys_signed_maxima_adjacent_changes_speed_and_input_preservation(
    inputs,
):
    originals = [frame.copy(deep=True) for frame in inputs]
    result = prepare_trip_dynamics_eda(*inputs)

    assert result.journeys.max_delay_minutes.tolist() == [7, 3, -1]
    assert result.journeys.trip_instance_key.is_unique
    assert result.journeys.stop_coverage.eq(1).all()
    first = _journey(result)
    assert first.stop_sequence.tolist() == [10, 20, 40, 80]
    np.testing.assert_allclose(first.delta_delay_seconds, [np.nan, 60, 300, -60])
    np.testing.assert_allclose(first.observed_speed_kmh, [np.nan, 36, 36, 72])
    np.testing.assert_allclose(first.baseline_speed_kmh, [np.nan, 39.6, 39.6, 75.6])
    assert first.probe_count.tolist() == [0, 2, 1, 1]
    assert first.baseline_probe_count.tolist() == [0, 6, 3, 3]
    np.testing.assert_allclose(first.traversal_seconds, [np.nan, 100, 95, 90])
    np.testing.assert_allclose(first.baseline_traversal_seconds, [np.nan, 80, 75, 70])
    assert first.baseline_traversal_count.tolist() == [0, 3, 3, 3]
    assert first.iloc[0].traversal_exclusion_reason == "no_incoming_segment"
    np.testing.assert_allclose(first.dwell_seconds, [np.nan, 20, 25, 30])
    np.testing.assert_allclose(first.baseline_dwell_seconds, [np.nan, 40, 45, 50])
    assert first.baseline_dwell_count.tolist() == [0, 3, 3, 3]
    np.testing.assert_allclose(
        first.traversal_seconds + first.dwell_seconds,
        (first.arrival_time - first.upstream_arrival_time).dt.total_seconds(),
        equal_nan=True,
    )
    assert result.journeys.vehicle_count.eq(1).all()
    for frame, original in zip(inputs, originals):
        pdt.assert_frame_equal(frame, original)


def test_missing_intermediate_visit_is_visible_and_never_bridged(inputs):
    probes, updates, *static = inputs
    updates = updates.drop(index=2)
    with pytest.warns(UserWarning, match="incomplete"):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    first = _journey(result)
    assert first.stop_sequence.tolist() == [10, 20, 40, 80]
    assert first.exclusion_reason.tolist()[2] == "missing_stop_visit"
    np.testing.assert_allclose(first.delta_delay_seconds, [np.nan, 60, np.nan, np.nan])
    np.testing.assert_allclose(first.traversal_seconds, [np.nan, 100, np.nan, np.nan])
    journey = result.journeys.query("trip_start_timestamp == '2026-09-08 03:00'").iloc[
        0
    ]
    assert journey.max_delay_minutes == 6
    assert journey.incomplete
    assert journey.stop_coverage == 0.75


def test_latest_missing_delay_wins_over_older_prediction_and_timestamp_ties(inputs):
    probes, updates, *static = inputs
    newer = updates.iloc[[2]].assign(id=999, arrival_delay=np.nan)
    updates = pd.concat([updates, newer], ignore_index=True)
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    first = _journey(result)
    assert first.iloc[2].exclusion_reason == "missing_latest_delay"
    assert pd.isna(first.iloc[2].arrival_delay)
    assert first.iloc[3].delta_exclusion_reason == "from_missing_latest_delay"


@pytest.mark.parametrize(
    "column,value,reason",
    [
        ("schedule_relationship", 1, "skipped_visit"),
        ("schedule_relationship", 2, "no_data_visit"),
        ("trip_schedule_relationship", 3, "cancelled_trip"),
        ("arrival_time", pd.NaT, "missing_arrival_time"),
        ("arrival_time", pd.Timestamp("2030-01-01", tz="UTC"), "unfinished_visit"),
    ],
)
def test_unusable_latest_measurements_do_not_influence_maximum(
    inputs, column, value, reason
):
    probes, updates, *static = inputs
    updates = updates.copy()
    if column not in updates:
        updates[column] = 0
    updates.loc[2, column] = value
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    first = _journey(result)
    assert first.iloc[2].exclusion_reason == reason
    assert pd.isna(first.iloc[2].arrival_delay)
    assert result.journeys.max_delay_minutes.max() == 6


def test_daily_instances_and_repeated_visits_remain_distinct(inputs):
    probes, updates, stops, times, trips = inputs
    updates = updates.copy()
    times = times.copy()
    times.loc[3, "stop_id"] = "b"
    updates.loc[updates.stop_sequence.eq(80), "stop_id"] = "b"
    probes = probes.copy()
    probes.loc[probes.current_stop_sequence.eq(80), "stop_id"] = "b"
    result = prepare_trip_dynamics_eda(probes, updates, stops, times, trips)
    assert len(result.journeys) == 3
    first = _journey(result)
    assert first.stop_id.tolist() == ["a", "b", "c", "b"]
    assert first.arrival_delay.tolist() == [60, 120, 420, 360]
    assert first.observed_speed_kmh.tolist()[1:] == [36, 36, 72]
    np.testing.assert_allclose(first.traversal_seconds, [np.nan, 100, 95, 90])
    np.testing.assert_allclose(
        _journey(result, "2026-09-09 03:00").traversal_seconds, [np.nan, 80, 75, 70]
    )


def test_unmatched_missing_start_and_wrong_direction_updates_are_not_paired(inputs):
    probes, updates, *static = inputs
    extras = pd.concat(
        [
            updates.iloc[[2]].assign(trip_id="unknown"),
            updates.iloc[[2]].assign(trip_start_timestamp=" "),
            updates.iloc[[2]].assign(stop_sequence=999),
            updates.iloc[[2]].assign(trip_direction_id=1),
        ],
        ignore_index=True,
    ).assign(arrival_delay=99999)
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(
            probes, pd.concat([updates, extras]), *static
        )
    assert result.journeys.max_delay_minutes.tolist() == [7, 3, -1]
    audit = result.audit.query("stage == 'updates'").set_index("reason")["count"]
    assert audit["missing_trip_start"] == 1
    assert audit["unmatched_static_visit"] == 2


def test_nonincreasing_arrivals_exclude_changes_not_standalone_delays(inputs):
    probes, updates, *static = inputs
    updates = updates.copy()
    updates.loc[2, "arrival_time"] = updates.loc[1, "arrival_time"]
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    first = _journey(result)
    assert first.iloc[2].delta_exclusion_reason == "nonincreasing_arrival_times"
    assert pd.isna(first.iloc[2].delta_delay_seconds)
    assert first.iloc[2].arrival_delay == 420


def test_boundary_upstream_evidence_can_support_a_completed_pair(inputs):
    probes, updates, *static = inputs
    updates = updates.copy()
    updates.loc[0, "arrival_time"] -= pd.Timedelta(minutes=1)
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    first = _journey(result)
    assert first.iloc[0].exclusion_reason == "arrival_before_recording"
    assert pd.isna(first.iloc[0].arrival_delay)
    assert first.iloc[1].delta_delay_seconds == 60


def test_missing_journey_speed_does_not_remove_delay_journeys(inputs):
    probes, updates, *static = inputs
    probes = probes.copy()
    probes["trip_start_timestamp"] = pd.NA
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    assert len(result.journeys) == 3
    assert result.visits.observed_speed_kmh.isna().all()
    assert result.visits.baseline_speed_kmh.notna().any()
    assert result.journeys.vehicle_identity_status.eq("unavailable").all()


def test_empty_probes_and_ineligible_baselines_remain_explicit(inputs):
    probes, updates, *static = inputs
    with pytest.warns(UserWarning):
        empty = prepare_trip_dynamics_eda(probes.iloc[:0], updates, *static)
        stationary = prepare_trip_dynamics_eda(
            probes.assign(position_speed=0), updates, *static
        )
    assert len(empty.journeys) == len(stationary.journeys) == 3
    assert empty.visits.observed_speed_kmh.isna().all()
    assert stationary.visits.baseline_speed_kmh.isna().all()
    assert stationary.visits.observed_speed_kmh.dropna().eq(0).all()


def test_ambiguous_vehicle_identity_never_pools_two_bus_speeds(inputs):
    probes, updates, *static = inputs
    probes = probes.copy()
    probes.loc[0, "vehicle_id"] = "different-bus"
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    assert _journey(result).observed_speed_kmh.isna().all()
    np.testing.assert_allclose(
        _journey(result).traversal_seconds, [np.nan, 100, 95, 90]
    )
    assert result.journeys.iloc[0].vehicle_identity_status == "ambiguous"
    assert pd.isna(result.journeys.iloc[0].vehicle_id)


def test_blank_vehicle_ids_are_not_presented_as_unique_bus_identity(inputs):
    probes, updates, *static = inputs
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(
            probes.assign(vehicle_id=" "), updates, *static
        )
    assert result.journeys.vehicle_identity_status.eq("unavailable").all()
    assert result.journeys.vehicle_id.isna().all()
    assert _journey(result).observed_speed_kmh.notna().any()


def test_journeys_with_no_usable_delays_are_audited(inputs):
    probes, updates, *static = inputs
    updates = updates.copy()
    updates.loc[:3, "arrival_delay"] = np.nan
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, updates, *static)
    assert len(result.journeys) == 2
    assert (
        result.audit.query("stage == 'journeys' and reason == 'no_usable_delay'")[
            "count"
        ].iloc[0]
        == 1
    )
    with pytest.raises(ValueError, match="No usable final"):
        prepare_trip_dynamics_eda(probes, updates.assign(arrival_delay=np.nan), *static)


@pytest.fixture
def catalogue():
    return pd.DataFrame(
        {
            "trip_instance_key": ["a", "b", "c", "d", "e"],
            "max_delay_minutes": [-2.0, 2.0, 4.0, 6.0, 10.0],
            "stop_coverage": [1.0, 0.5, 1.0, 1.0, 1.0],
        }
    )


def test_nearest_selection_uses_catalogue_percentile_not_largest_values(catalogue):
    original = catalogue.copy(deep=True)
    selected = select_trip_dynamics_examples(catalogue, k=3)
    assert selected.target_max_delay_minutes.eq(8.4).all()
    assert selected.trip_instance_key.tolist() == ["e", "d", "c"]
    np.testing.assert_allclose(selected.absolute_difference_minutes, [1.6, 2.4, 4.4])
    pdt.assert_frame_equal(catalogue, original)


def test_explicit_target_uses_both_sides_no_cutoff_and_deterministic_ties(catalogue):
    selected = select_trip_dynamics_examples(
        catalogue.sample(frac=1, random_state=8),
        target_max_delay_minutes=3,
        k=4,
    )
    assert selected.trip_instance_key.tolist() == ["c", "b", "d", "a"]
    assert selected.absolute_difference_minutes.tolist() == [1, 1, 3, 5]
    ties = catalogue.assign(stop_coverage=1)
    assert select_trip_dynamics_examples(
        ties, target_max_delay_minutes=3, k=2
    ).trip_instance_key.tolist() == ["b", "c"]


def test_negative_target_and_k_exceeding_population(catalogue):
    with pytest.warns(UserWarning, match="only 5"):
        selected = select_trip_dynamics_examples(
            catalogue,
            target_max_delay_minutes=-3,
            k=20,
        )
    assert len(selected) == 5
    assert selected.iloc[0].trip_instance_key == "a"


def test_reference_journey_overrides_target_and_selects_nearest_other_journeys(
    catalogue,
):
    original = catalogue.copy(deep=True)
    selected = select_trip_dynamics_examples(
        catalogue,
        reference_trip_instance_key="c",
        target_max_delay_minutes=100,
        max_delay_quantile=0,
        k=3,
    )
    assert selected.trip_instance_key.tolist() == ["c", "d", "b"]
    assert selected.target_max_delay_minutes.eq(4).all()
    assert selected.absolute_difference_minutes.tolist() == [0, 2, 2]
    pdt.assert_frame_equal(catalogue, original)


@pytest.mark.parametrize("k", [1, 3])
def test_reference_journey_is_guaranteed_even_when_equal_maximum_has_better_coverage(
    catalogue, k
):
    catalogue = catalogue.copy()
    catalogue.loc[0, ["max_delay_minutes", "stop_coverage"]] = [4, 0.25]
    selected = select_trip_dynamics_examples(
        catalogue.sample(frac=1, random_state=8),
        reference_trip_instance_key="a",
        k=k,
    )
    assert selected.trip_instance_key.tolist() == ["a", "c", "d"][:k]
    assert selected.trip_instance_key.is_unique


def test_none_reference_preserves_existing_maximum_delay_selection(catalogue):
    for target in (None, 3):
        expected = select_trip_dynamics_examples(
            catalogue, target_max_delay_minutes=target
        )
        selected = select_trip_dynamics_examples(
            catalogue,
            target_max_delay_minutes=target,
            reference_trip_instance_key=None,
        )
        pdt.assert_frame_equal(selected, expected)


def test_reference_journey_with_fewer_than_k_candidates(catalogue):
    with pytest.warns(UserWarning, match="only 5"):
        selected = select_trip_dynamics_examples(
            catalogue,
            reference_trip_instance_key="b",
            k=10,
        )
    assert len(selected) == 5
    assert selected.iloc[0].trip_instance_key == "b"
    assert selected.trip_instance_key.is_unique


@pytest.mark.parametrize("reference", ["", " ", "unknown", 123, True])
def test_invalid_reference_never_falls_back_to_maximum_delay_selection(
    catalogue, reference
):
    with pytest.raises(ValueError, match="reference_trip_instance_key"):
        select_trip_dynamics_examples(catalogue, reference_trip_instance_key=reference)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"k": 0},
        {"k": True},
        {"k": 1.5},
        {"max_delay_quantile": -0.1},
        {"max_delay_quantile": np.nan},
        {"target_max_delay_minutes": np.inf},
        {"target_max_delay_minutes": True},
    ],
)
def test_invalid_selection_controls_raise(catalogue, kwargs):
    with pytest.raises(ValueError):
        select_trip_dynamics_examples(catalogue, **kwargs)


def test_invalid_catalogue_is_rejected(catalogue):
    for frame in (
        catalogue.iloc[:0],
        pd.concat([catalogue, catalogue.iloc[[0]]]),
        catalogue.assign(max_delay_minutes=np.inf),
        catalogue.assign(max_delay_minutes="not numeric"),
        catalogue.assign(stop_coverage=2),
    ):
        with pytest.raises(ValueError):
            select_trip_dynamics_examples(frame)


def test_figure_retains_missing_stop_gaps_and_selected_trip_only(inputs):
    probes, updates, *static = inputs
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(
            probes.iloc[:0], updates.drop(index=2), *static
        )
    key = "main|2026-09-08 03:00"
    figure = make_trip_dynamics_figure(result, key)
    assert list(figure.data[0].x) == [0, 1, 2, 3]
    np.testing.assert_allclose(figure.data[0].y, [1, 2, np.nan, 6])
    np.testing.assert_allclose(figure.data[1].y, [np.nan, 1, np.nan, np.nan])
    assert figure.data[0].connectgaps is False
    assert "INCOMPLETE" in figure.layout.title.text
    np.testing.assert_allclose(figure.data[2].y, [np.nan, 100, np.nan, np.nan])
    np.testing.assert_allclose(figure.data[3].y, [np.nan, 80, 65, 60])
    assert figure.data[2].connectgaps is False
    assert figure.layout.yaxis3.title.text == "Traversal time (s)"
    assert [trace.yaxis for trace in figure.data] == ["y", "y2", "y3", "y3", "y4", "y4"]
    assert all(
        figure.layout[axis].matches == "x4" for axis in ("xaxis", "xaxis2", "xaxis3")
    )
    assert figure.layout.yaxis4.title.text == "Upstream dwell (s)"
    np.testing.assert_allclose(figure.data[4].y, [np.nan, 20, np.nan, np.nan])
    np.testing.assert_allclose(figure.data[5].y, [np.nan, 40, 55, 60])
    assert figure.data[4].connectgaps is False
    assert "Median upstream dwell:" in figure.data[5].hovertemplate
    assert "Baseline traversals:" in figure.data[3].hovertemplate
    assert list(figure.layout.xaxis4.tickvals) == [0, 1, 2, 3]
    assert "Third" in figure.layout.xaxis4.ticktext[2]
    with pytest.raises(ValueError, match="No prepared"):
        make_trip_dynamics_figure(
            replace(result, journeys=result.journeys.iloc[:0]), key
        )


@pytest.mark.parametrize(
    "row,column,value,reason",
    [
        (0, "departure_time", pd.NaT, "missing_upstream_departure"),
        (
            0,
            "departure_time",
            pd.Timestamp("2026-09-08 10:02Z"),
            "nonpositive_traversal",
        ),
        (
            0,
            "departure_time",
            pd.Timestamp("2026-09-08 09:59Z"),
            "invalid_upstream_chronology",
        ),
        (1, "arrival_time", pd.Timestamp("2030-01-01", tz="UTC"), "unfinished_arrival"),
        (0, "schedule_relationship", 2, "upstream_no_data_visit"),
        (1, "schedule_relationship", 1, "skipped_visit"),
        (0, "trip_schedule_relationship", 7, "upstream_cancelled_trip"),
    ],
)
def test_traversal_uses_latest_exact_endpoints_without_older_fallback(
    inputs, row, column, value, reason
):
    probes, updates, *static = inputs
    updates = updates.assign(schedule_relationship=0, trip_schedule_relationship=0)
    newer = updates.iloc[[row]].assign(id=999)
    newer.loc[:, column] = value
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(
            probes, pd.concat([updates, newer], ignore_index=True), *static
        )
    visit = _journey(result).iloc[1]
    assert visit.traversal_exclusion_reason == reason
    assert pd.isna(visit.traversal_seconds)
    assert visit.baseline_traversal_count == 2
    assert visit.baseline_traversal_seconds == 70


def test_traversal_baselines_include_unselected_and_delay_unusable_journeys(inputs):
    probes, updates, *static = inputs
    updates = updates.copy()
    updates.loc[:3, "arrival_delay"] = np.nan
    repeated = pd.concat([updates, updates.iloc[:4]], ignore_index=True)
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes, repeated, *static)
    assert len(result.journeys) == 2
    first = _journey(result)
    assert first.arrival_delay.isna().all()
    np.testing.assert_allclose(first.traversal_seconds, [np.nan, 100, 95, 90])
    assert first.baseline_traversal_count.tolist() == [0, 3, 3, 3]
    np.testing.assert_allclose(first.baseline_traversal_seconds, [np.nan, 80, 75, 70])
    selected = select_trip_dynamics_examples(result.journeys, k=1)
    figure = make_trip_dynamics_figure(result, selected.trip_instance_key.iloc[0])
    np.testing.assert_allclose(figure.data[3].y, [np.nan, 80, 75, 70])


def test_missing_departure_support_is_audited_without_removing_delay_journeys(inputs):
    probes, updates, *static = inputs
    with pytest.warns(UserWarning, match="incomplete"):
        result = prepare_trip_dynamics_eda(
            probes, updates.drop(columns="departure_time"), *static
        )
    assert result.journeys.max_delay_minutes.tolist() == [7, 3, -1]
    assert result.visits.traversal_seconds.isna().all()
    assert result.visits.baseline_traversal_count.eq(0).all()
    assert result.visits.baseline_dwell_seconds.isna().all()
    assert result.visits.baseline_dwell_count.eq(0).all()
    audit = result.audit.query("stage == 'timing_inputs'").set_index("reason")["count"]
    assert audit["missing_departure_time_column"] == len(updates)
    figure = make_trip_dynamics_figure(
        result, result.journeys.trip_instance_key.iloc[0]
    )
    assert any(
        "No completed journey traversals" in a.text for a in figure.layout.annotations
    )
    assert any("No usable upstream dwell" in a.text for a in figure.layout.annotations)


def test_journey_and_single_segment_traversal_measurements_agree(inputs):
    journeys = prepare_trip_dynamics_eda(*inputs)
    segment = prepare_segment_timing_eda(
        prepare_stop_dynamics_eda(*inputs, segment_id=0)
    )
    expected = segment.observations.sort_values("trip_instance_key")
    actual = journeys.visits.query("segment_id == 0").sort_values("trip_instance_key")
    assert actual.trip_instance_key.tolist() == expected.trip_instance_key.tolist()
    np.testing.assert_allclose(actual.traversal_seconds, expected.traversal_seconds)
    assert actual.baseline_traversal_seconds.eq(
        segment.bins.baseline_traversal_seconds.iloc[0]
    ).all()
    assert actual.baseline_traversal_count.eq(segment.bins.traversal_count.sum()).all()


def test_upstream_dwell_median_weights_valid_visits_not_updates_or_selected_journeys(
    inputs,
):
    probes, updates, *static = inputs
    updates = updates.copy()
    for row, seconds in zip((0, 4, 8), (0, 20, 100)):
        updates.loc[row, "departure_time"] = updates.loc[
            row, "arrival_time"
        ] + pd.Timedelta(seconds=seconds)
    updates.loc[:3, "arrival_delay"] = np.nan
    repeated = pd.concat(
        [updates, updates.iloc[:4], updates.iloc[:4]], ignore_index=True
    )
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(probes.iloc[:0], repeated, *static)
    segment = result.visits.query("segment_id == 0").sort_values("trip_instance_key")
    assert segment.dwell_seconds.tolist() == [0, 20, 100]
    assert segment.baseline_dwell_seconds.eq(20).all()
    assert segment.baseline_dwell_count.eq(3).all()
    assert len(result.journeys) == 2
    selected = select_trip_dynamics_examples(result.journeys, k=1)
    figure = make_trip_dynamics_figure(result, selected.trip_instance_key.iloc[0])
    assert figure.data[4].y[1] == 100
    assert figure.data[5].y[1] == 20
    audit = result.audit.query("stage == 'upstream_dwells'").set_index("reason")[
        "count"
    ]
    assert audit["included"] == 9
    assert audit["no_incoming_segment"] == 3


@pytest.mark.parametrize(
    "column,value,reason",
    [
        ("arrival_time", pd.NaT, "missing_upstream_arrival"),
        ("departure_time", pd.NaT, "missing_upstream_departure"),
        ("departure_time", pd.Timestamp("2026-09-08 09:59Z"), "negative_dwell"),
        ("departure_time", pd.Timestamp("2030-01-01", tz="UTC"), "unfinished_dwell"),
        ("schedule_relationship", 2, "upstream_no_data_visit"),
    ],
)
def test_invalid_latest_upstream_dwell_stays_a_gap_and_does_not_bias_median(
    inputs, column, value, reason
):
    probes, updates, *static = inputs
    updates = updates.assign(schedule_relationship=0)
    newer = updates.iloc[[0]].assign(id=999)
    newer.loc[:, column] = value
    with pytest.warns(UserWarning):
        result = prepare_trip_dynamics_eda(
            probes, pd.concat([updates, newer], ignore_index=True), *static
        )
    visit = _journey(result).iloc[1]
    assert visit.dwell_exclusion_reason == reason
    assert pd.isna(visit.dwell_seconds)
    assert visit.baseline_dwell_seconds == 50
    assert visit.baseline_dwell_count == 2
