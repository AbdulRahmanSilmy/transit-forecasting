from dataclasses import replace

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest
from shapely.geometry import LineString

from forecasting import get_segment_traversal_speeds, run_congestion_engine


@pytest.fixture
def inputs():
    stops = pd.DataFrame(
        {
            "stop_id": ["a", "b", "c"],
            "stop_name": ["First", "Second", "Third"],
            "stop_lat": [0.0, 0.0, 0.0],
            "stop_lon": [0.0, 0.01, 0.02],
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["main"] * 3,
            "stop_id": ["a", "b", "c"],
            "stop_sequence": [10, 20, 40],
        }
    )
    trips = pd.DataFrame(
        {
            "route_id": ["97-KEL"],
            "trip_id": ["main"],
            "direction_id": [0],
            "shape_id": ["outbound"],
        }
    )
    shapes = pd.DataFrame(
        {
            "shape_id": ["outbound"] * 3,
            "shape_pt_sequence": [1, 2, 3],
            "shape_pt_lat": [0.0] * 3,
            "shape_pt_lon": [0.0, 0.01, 0.02],
        }
    )
    update_frames, probe_frames = [], []
    for index, seconds in enumerate([100, 200]):
        base = pd.Timestamp("2026-09-08 10:00Z") + pd.Timedelta(minutes=10 * index)
        start = f"2026-09-08 03:{10 * index:02}:00"
        arrivals = [
            base,
            base + pd.Timedelta(seconds=seconds + 20),
            base + pd.Timedelta(seconds=seconds + 120),
        ]
        update_frames.append(
            pd.DataFrame(
                {
                    "id": np.arange(3) + index * 3,
                    "trip_route_id": ["97-KEL"] * 3,
                    "trip_id": ["main"] * 3,
                    "trip_direction_id": [0] * 3,
                    "trip_start_timestamp": [start] * 3,
                    "stop_id": ["a", "b", "c"],
                    "stop_sequence": [10, 20, 40],
                    "arrival_delay": [0.0] * 3,
                    "arrival_time": arrivals,
                    "departure_time": [
                        time + pd.Timedelta(seconds=20) for time in arrivals
                    ],
                    "feed_timestamp": [base + pd.Timedelta(minutes=8)] * 3,
                    "schedule_relationship": [0] * 3,
                }
            )
        )
        probe_frames.append(
            pd.DataFrame(
                {
                    "trip_route_id": ["97-KEL"] * 2,
                    "trip_id": ["main"] * 2,
                    "trip_direction_id": [0] * 2,
                    "trip_start_timestamp": [start] * 2,
                    "stop_id": ["b", "c"],
                    "current_stop_sequence": [20, 40],
                    "position_speed": [10.0, 10.0],
                    "vehicle_id": [f"bus-{index + 1}"] * 2,
                    "feed_timestamp": arrivals[1:],
                }
            )
        )
    updates = pd.concat(update_frames, ignore_index=True)
    probes = pd.concat(probe_frames, ignore_index=True)
    result = run_congestion_engine(probes, stops, stop_times, trips, shapes)
    return result, updates, stops, stop_times, trips


def _run(inputs, **kwargs):
    return get_segment_traversal_speeds(*inputs, segment_id=0, **kwargs)


def test_full_path_over_departure_to_arrival_and_median_baseline(inputs):
    output = _run(inputs)
    distance = 6_371_008.8 * np.radians(0.01)

    assert output["vehicle_id"].tolist() == ["bus-1", "bus-2"]
    assert output["travel_time_seconds"].tolist() == [100.0, 200.0]
    np.testing.assert_allclose(output["distance_meters"], distance)
    np.testing.assert_allclose(
        output["speed_kmh"], distance / np.array([100, 200]) * 3.6
    )
    np.testing.assert_allclose(
        output["baseline_speed_kmh"], output["speed_kmh"].median()
    )
    assert output["from_stop_sequence"].tolist() == [10, 10]
    assert output["to_stop_sequence"].tolist() == [20, 20]
    assert (
        output["arrival_time"].tolist()
        == inputs[1].loc[[1, 4], "arrival_time"].tolist()
    )
    assert str(output["arrival_time"].dt.tz) == "UTC"
    assert not output.duplicated(
        ["trip_instance_key", "from_stop_sequence", "to_stop_sequence"]
    ).any()


def test_latest_estimates_and_database_id_ties_are_selected_once(inputs):
    result, updates, *static = inputs
    older = updates.assign(arrival_time=updates["arrival_time"] + pd.Timedelta(hours=1))
    older["feed_timestamp"] -= pd.Timedelta(seconds=1)
    tied = updates.iloc[[1]].assign(id=100)
    tied["arrival_time"] += pd.Timedelta(seconds=100)
    combined = pd.concat([older, updates, tied, tied], ignore_index=True)

    output = _run((result, combined, *static))

    assert len(output) == 2
    assert output["travel_time_seconds"].tolist() == [200.0, 200.0]
    pdt.assert_frame_equal(output, _run((result, combined.iloc[::-1], *static)))


@pytest.mark.parametrize(
    ("row", "field", "value", "reason"),
    [
        (0, "departure_time", pd.NaT, "missing_upstream_departure"),
        (1, "arrival_time", pd.NaT, "missing_downstream_arrival"),
        (
            1,
            "arrival_time",
            pd.Timestamp("2026-09-08 10:00:20Z"),
            "nonpositive_travel_time",
        ),
        (
            1,
            "arrival_time",
            pd.Timestamp("2026-09-08 10:00:10Z"),
            "nonpositive_travel_time",
        ),
        (1, "arrival_time", pd.Timestamp("2026-09-08 12:00Z"), "unfinished_traversal"),
        (0, "schedule_relationship", 1, "skipped_upstream_visit"),
        (1, "schedule_relationship", 2, "skipped_downstream_visit"),
    ],
)
def test_invalid_latest_endpoints_are_excluded_without_old_prediction_fallback(
    inputs, row, field, value, reason
):
    result, updates, *static = inputs
    older = updates.copy()
    older["feed_timestamp"] -= pd.Timedelta(seconds=1)
    updates = updates.copy()
    updates.loc[row, field] = value
    with pytest.warns(UserWarning, match=reason):
        output = _run((result, pd.concat([older, updates]), *static))

    assert output["vehicle_id"].tolist() == ["bus-2"]


def test_missing_visit_is_not_bridged_or_matched_by_another_stop_sequence(inputs):
    result, updates, *static = inputs
    updates = updates.drop(index=1)
    wrong_stop = inputs[1].iloc[[1]].assign(stop_id="c")
    with pytest.warns(UserWarning, match="missing_downstream_arrival"):
        output = _run((result, pd.concat([updates, wrong_stop]), *static))
    assert output["vehicle_id"].tolist() == ["bus-2"]


@pytest.mark.parametrize("vehicle_id", [None, "", "bus-other"])
def test_missing_or_ambiguous_bus_identity_is_excluded(inputs, vehicle_id):
    result, updates, *static = inputs
    probes = result.vehicle_probes.copy()
    first_instance = probes["vehicle_id"].eq("bus-1")
    probes.loc[first_instance, "vehicle_id"] = vehicle_id
    reason = "missing_vehicle_identity"
    if vehicle_id == "bus-other":
        probes = pd.concat([probes, result.vehicle_probes.loc[first_instance]])
        reason = "ambiguous_vehicle_identity"
    with pytest.warns(UserWarning, match=reason):
        output = _run((replace(result, vehicle_probes=probes), updates, *static))
    assert output["vehicle_id"].tolist() == ["bus-2"]


def test_trip_instances_stay_separate_when_the_same_bus_returns(inputs):
    result, updates, *static = inputs
    probes = result.vehicle_probes.assign(vehicle_id="same-bus")
    output = _run((replace(result, vehicle_probes=probes), updates, *static))
    assert output["vehicle_id"].tolist() == ["same-bus", "same-bus"]
    assert output["trip_instance_key"].nunique() == 2
    assert output["travel_time_seconds"].tolist() == [100.0, 200.0]


def test_missing_trip_starts_are_excluded_not_grouped_by_date(inputs):
    result, updates, *static = inputs
    updates = updates.copy()
    updates.loc[:2, "trip_start_timestamp"] = None
    with pytest.warns(UserWarning, match="missing trip starts"):
        output = _run((result, updates, *static))
    assert output["vehicle_id"].tolist() == ["bus-2"]


def test_start_and_sequence_normalization_and_missing_delays(inputs):
    result, updates, *static = inputs
    updates = updates.assign(
        trip_start_timestamp=" " + updates["trip_start_timestamp"] + " ",
        stop_sequence=updates["stop_sequence"].astype(str),
        arrival_delay=float("nan"),
    )
    output = _run((result, updates, *static))
    assert output["travel_time_seconds"].tolist() == [100.0, 200.0]


def test_different_directions_shapes_and_express_stop_pairs_are_not_pooled(inputs):
    result, updates, stops, stop_times, trips = inputs
    other_trips = pd.DataFrame(
        {
            "route_id": ["97-KEL"] * 3,
            "trip_id": ["reverse", "variant", "express"],
            "direction_id": [1, 0, 0],
            "shape_id": ["outbound", "other-shape", "outbound"],
        }
    )
    other_stops = pd.concat(
        [
            stop_times.assign(trip_id="reverse"),
            stop_times.assign(trip_id="variant"),
            stop_times.iloc[[0, 2]].assign(trip_id="express"),
        ]
    )
    other_updates = pd.concat(
        [
            updates.assign(trip_id="reverse", trip_direction_id=1),
            updates.assign(trip_id="variant"),
            updates.assign(trip_id="express"),
        ]
    )
    output = _run(
        (
            result,
            pd.concat([updates, other_updates]),
            stops,
            pd.concat([stop_times, other_stops]),
            pd.concat([trips, other_trips]),
        )
    )
    assert output["trip_id"].eq("main").all()
    assert len(output) == 2


def test_shape_path_length_is_not_the_endpoint_shortcut(inputs):
    result, updates, *static = inputs
    segments = result.segments.copy()
    segments.at[0, "geometry"] = LineString(
        [(0, 0), (0, 0.01), (0.01, 0.01), (0.01, 0)]
    )
    output = _run((replace(result, segments=segments), updates, *static))
    assert output["distance_meters"].iloc[0] == pytest.approx(
        3 * 6_371_008.8 * np.radians(0.01), rel=1e-7
    )


def test_inputs_are_not_modified(inputs):
    result, *frames = inputs
    originals = [frame.copy(deep=True) for frame in frames]
    probes = result.vehicle_probes.copy(deep=True)
    segments = result.segments.copy(deep=True)
    _run(inputs)
    for frame, original in zip(frames, originals):
        pdt.assert_frame_equal(frame, original)
    pdt.assert_frame_equal(result.vehicle_probes, probes)
    pdt.assert_frame_equal(result.segments, segments)


@pytest.mark.parametrize("segment_id", [999, True, 0.5])
def test_invalid_or_unknown_segment_raises(inputs, segment_id):
    with pytest.raises(ValueError, match="segment_id"):
        get_segment_traversal_speeds(*inputs, segment_id=segment_id)


def test_no_usable_completed_traversals_raises_explicit_error(inputs):
    result, updates, *static = inputs
    updates = updates.assign(arrival_time=pd.NaT)
    with pytest.warns(UserWarning, match="missing_downstream_arrival"):
        with pytest.raises(ValueError, match="No completed segment traversals"):
            _run((result, updates, *static))


def test_missing_required_times_are_not_substituted(inputs):
    result, updates, *static = inputs
    with pytest.raises(ValueError, match="departure_time"):
        _run((result, updates.drop(columns="departure_time"), *static))


def test_single_traversal_defines_its_own_median(inputs):
    result, updates, *static = inputs
    output = _run((result, updates.iloc[:3], *static))
    assert len(output) == 1
    assert output["baseline_speed_kmh"].iloc[0] == output["speed_kmh"].iloc[0]


def test_daily_instances_and_crossings_over_local_midnight_stay_separate(inputs):
    result, updates, *static = inputs
    updates = updates.copy()
    probes = result.vehicle_probes.copy()
    old_starts = updates["trip_start_timestamp"].drop_duplicates().tolist()
    for index, (old_start, new_start, shift) in enumerate(
        [
            (old_starts[0], "2026-09-07 23:59:00", "-3h30s"),
            (old_starts[1], "2026-09-08 23:59:00", "1day"),
        ]
    ):
        selected = updates["trip_start_timestamp"].eq(old_start)
        for field in ("arrival_time", "departure_time", "feed_timestamp"):
            updates.loc[selected, field] += pd.Timedelta(shift)
        updates.loc[selected, "trip_start_timestamp"] = new_start
        probes.loc[
            probes["trip_start_timestamp"].eq(old_start), "trip_start_timestamp"
        ] = new_start
    output = _run((replace(result, vehicle_probes=probes), updates, *static))
    assert output["travel_time_seconds"].tolist() == [100.0, 200.0]
    assert output["trip_instance_key"].nunique() == 2
    first = output.iloc[0]
    assert (
        first["departure_time"].tz_convert("America/Vancouver").date()
        != first["arrival_time"].tz_convert("America/Vancouver").date()
    )


def test_receipt_timestamp_can_confirm_a_later_completed_estimate(inputs):
    result, updates, *static = inputs
    updates = updates.copy()
    updates.loc[1, "arrival_time"] = pd.Timestamp("2026-09-08 10:19Z")
    updates["read_timestamp"] = pd.Timestamp("2026-09-08 10:20Z")
    output = _run((result, updates, *static))
    assert len(output) == 2
    assert output["arrival_time"].iloc[-1] == pd.Timestamp("2026-09-08 10:19Z")


def test_zero_length_segment_raises(inputs):
    result, updates, *static = inputs
    segments = result.segments.copy()
    segments.at[0, "geometry"] = LineString([(0, 0), (0, 0)])
    with pytest.raises(ValueError, match="positive finite path length"):
        _run((replace(result, segments=segments), updates, *static))
