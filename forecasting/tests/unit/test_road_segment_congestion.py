import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import run_congestion_engine


def _static_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stops = pd.DataFrame(
        {
            "stop_id": ["stop-1", "stop-2", "stop-3"],
            "stop_name": ["First", "Second", "Third"],
            "stop_lat": [0.0, 0.0, 0.0],
            "stop_lon": [0.0, 1.0, 2.0],
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": [
                "trip-main",
                "trip-main",
                "trip-main",
                "trip-short",
                "trip-short",
            ],
            "stop_id": ["stop-1", "stop-2", "stop-3", "stop-1", "stop-2"],
            "stop_sequence": [1, 2, 3, 1, 2],
        }
    )
    trips = pd.DataFrame(
        {
            "route_id": ["97-KEL", "97-KEL"],
            "trip_id": ["trip-main", "trip-short"],
            "direction_id": [0, 0],
            "shape_id": ["shape-main", "shape-main"],
        }
    )
    shapes = pd.DataFrame(
        {
            "shape_id": ["shape-main"] * 3,
            "shape_pt_sequence": [1, 2, 3],
            "shape_pt_lat": [0.0, 0.0, 0.0],
            "shape_pt_lon": [0.0, 1.0, 2.0],
        }
    )
    return stops, stop_times, trips, shapes


def _vehicle_updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trip_id": ["trip-main"] * 8,
            "trip_direction_id": [0] * 8,
            "stop_id": [
                "stop-2",
                "stop-2",
                "stop-2",
                "stop-3",
                "stop-3",
                "stop-2",
                "stop-2",
                "stop-2",
            ],
            "trip_route_id": [
                "97-KEL",
                "97-KEL",
                "97-KEL",
                "97-KEL",
                "97-KEL",
                "other-route",
                "97-KEL",
                "97-KEL",
            ],
            "feed_timestamp": pd.to_datetime(
                [
                    "2024-01-01 10:01:02",
                    "2024-01-01 10:03:02",
                    "2024-01-01 10:06:05",
                    "2024-01-01 10:02:00",
                    "2024-01-01 10:07:00",
                    "2024-01-01 10:02:00",
                    "2024-01-01 10:02:00",
                    "2024-01-01 10:02:00",
                ]
            ),
            "position_speed": [10.0, 20.0, 5.0, 4.0, 8.0, 100.0, -1.0, None],
            "current_stop_sequence": [2, 2, 2, 3, 3, 2, 2, 2],
            "vehicle_id": ["v1", "v2", "v1", "v3", "v3", "v4", "v5", "v6"],
        }
    )


def test_computes_segments_and_congestion_without_mutating_inputs() -> None:
    stops, stop_times, trips, shapes = _static_data()
    vehicle_updates = _vehicle_updates()
    frames = (vehicle_updates, stops, stop_times, trips, shapes)
    originals = [frame.copy(deep=True) for frame in frames]

    result = run_congestion_engine(
        vehicle_updates,
        stops,
        stop_times,
        trips,
        shapes,
    )

    assert result.representative_trip_id == "trip-main"
    assert result.segments["segment_id"].tolist() == [0, 1]
    assert result.segments["to_stop_sequence"].tolist() == [2, 3]
    assert result.segments["from_stop_name"].tolist() == ["First", "Second"]
    assert result.segments["to_stop_name"].tolist() == ["Second", "Third"]
    assert result.segments["trip_direction_id"].eq(0).all()
    assert result.congestion["trip_direction_id"].eq(0).all()
    assert result.congestion["shape_id"].eq("shape-main").all()
    assert len(result.vehicle_probes) == 5
    assert result.vehicle_probes["feed_timestamp"].dt.tz is not None

    congestion = result.congestion.set_index(["segment_id", "time_bin"])
    first_bin = pd.Timestamp("2024-01-01 10:00:00", tz="UTC")
    second_bin = pd.Timestamp("2024-01-01 10:05:00", tz="UTC")

    assert congestion.loc[(0, first_bin), "observed_speed"] == 15.0
    assert congestion.loc[(0, first_bin), "baseline_speed"] == 10.0
    assert congestion.loc[(0, first_bin), "congestion_index"] == 0.0
    assert congestion.loc[(0, second_bin), "congestion_index"] == 0.5
    assert congestion.loc[(1, first_bin), "congestion_index"] == pytest.approx(1 / 3)
    assert congestion.loc[(1, second_bin), "congestion_index"] == 0.0
    for frame, original in zip(frames, originals):
        pdt.assert_frame_equal(frame, original)


def test_omits_segments_with_near_stationary_baseline() -> None:
    stops, stop_times, trips, shapes = _static_data()
    vehicle_updates = pd.DataFrame(
        {
            "trip_route_id": ["97-KEL"],
            "trip_id": ["trip-main"],
            "trip_direction_id": [0],
            "stop_id": ["stop-2"],
            "feed_timestamp": [pd.Timestamp("2024-01-01 10:01:00")],
            "position_speed": [0.25],
            "current_stop_sequence": [2],
            "vehicle_id": ["v1"],
        }
    )

    result = run_congestion_engine(
        vehicle_updates,
        stops,
        stop_times,
        trips,
        shapes,
    )

    assert result.baseline_speeds.loc[0, "baseline_speed"] == 0.25
    assert result.congestion.empty
    assert result.congestion.columns.tolist() == [
        "segment_id",
        "trip_direction_id",
        "shape_id",
        "time_bin",
        "observed_speed",
        "baseline_speed",
        "congestion_index",
    ]


def test_returns_empty_congestion_for_no_matching_probes() -> None:
    stops, stop_times, trips, shapes = _static_data()
    vehicle_updates = _vehicle_updates().iloc[0:0]

    result = run_congestion_engine(
        vehicle_updates,
        stops,
        stop_times,
        trips,
        shapes,
    )

    assert result.vehicle_probes.empty
    assert result.baseline_speeds.empty
    assert result.congestion.empty


def test_rejects_missing_vehicle_update_columns() -> None:
    stops, stop_times, trips, shapes = _static_data()

    with pytest.raises(ValueError, match="trip_route_id"):
        run_congestion_engine(
            pd.DataFrame(),
            stops,
            stop_times,
            trips,
            shapes,
        )


def _two_way_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stops, stop_times, trips, shapes = _static_data()
    stop_times = pd.concat(
        [
            stop_times,
            pd.DataFrame(
                {
                    "trip_id": ["trip-return"] * 3,
                    "stop_id": ["stop-3", "stop-2", "stop-1"],
                    "stop_sequence": [1, 2, 3],
                }
            ),
        ],
        ignore_index=True,
    )
    trips = pd.concat(
        [
            trips,
            pd.DataFrame(
                {
                    "route_id": ["97-KEL"],
                    "trip_id": ["trip-return"],
                    "direction_id": [1],
                    "shape_id": ["shape-return"],
                }
            ),
        ],
        ignore_index=True,
    )
    shapes = pd.concat(
        [
            shapes,
            pd.DataFrame(
                {
                    "shape_id": ["shape-return"] * 3,
                    "shape_pt_sequence": [1, 2, 3],
                    "shape_pt_lat": [0.0, 0.0, 0.0],
                    "shape_pt_lon": [2.0, 1.0, 0.0],
                }
            ),
        ],
        ignore_index=True,
    )
    return stops, stop_times, trips, shapes


def _two_way_updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trip_route_id": ["97-KEL"] * 4,
            "trip_id": ["trip-main", "trip-main", "trip-return", "trip-return"],
            "trip_direction_id": [0, 0, 1, 1],
            "stop_id": ["stop-2"] * 4,
            "current_stop_sequence": [2] * 4,
            "feed_timestamp": pd.to_datetime(
                [
                    "2024-01-01 10:01:00",
                    "2024-01-01 10:06:00",
                    "2024-01-01 10:01:00",
                    "2024-01-01 10:06:00",
                ]
            ),
            "position_speed": [10.0, 0.0, 4.0, 4.0],
            "vehicle_id": ["outbound", "outbound", "return", "return"],
        }
    )


def test_separates_two_way_geometry_probes_baselines_and_congestion() -> None:
    result = run_congestion_engine(_two_way_updates(), *_two_way_data())

    assert result.segments["segment_id"].is_unique
    assert set(result.segments["trip_direction_id"]) == {0, 1}
    assert result.route_geometry.geom_type == "MultiLineString"
    assert (
        result.vehicle_probes.groupby("segment_id")["trip_direction_id"]
        .nunique()
        .eq(1)
        .all()
    )
    assert (
        result.vehicle_probes.groupby("trip_direction_id")["segment_id"]
        .nunique()
        .eq(1)
        .all()
    )
    baselines = result.baseline_speeds.set_index("trip_direction_id")
    assert baselines.loc[0, "baseline_speed"] == 5.0
    assert baselines.loc[1, "baseline_speed"] == 4.0
    second_bin = result.congestion.loc[
        result.congestion["time_bin"].eq(pd.Timestamp("2024-01-01 10:05:00", tz="UTC"))
    ].set_index("trip_direction_id")
    assert second_bin.loc[0, "congestion_index"] == 1.0
    assert second_bin.loc[1, "congestion_index"] == 0.0
    reverse = result.segments.loc[result.segments["trip_direction_id"].eq(1)]
    assert reverse.iloc[0]["from_stop_id"] == "stop-3"
    assert reverse.iloc[0]["to_stop_id"] == "stop-2"
    assert list(reverse.iloc[0]["geometry"].coords) == [(2.0, 0.0), (1.0, 0.0)]


@pytest.mark.parametrize("direction", [0, 1])
def test_filters_vehicle_updates_and_gtfs_geometry_to_requested_direction(
    direction: int, recwarn: pytest.WarningsRecorder
) -> None:
    result = run_congestion_engine(
        _two_way_updates(), *_two_way_data(), trip_direction_id=direction
    )

    assert result.segments["trip_direction_id"].eq(direction).all()
    assert result.vehicle_probes["trip_direction_id"].eq(direction).all()
    assert result.baseline_speeds["trip_direction_id"].eq(direction).all()
    assert result.congestion["trip_direction_id"].eq(direction).all()
    assert result.route_geometry.geom_type == "LineString"
    assert not recwarn


def test_uses_each_trips_sequence_for_shared_directed_segment() -> None:
    stops, times, trips, shapes = _static_data()
    times.loc[
        times["trip_id"].eq("trip-short") & times["stop_id"].eq("stop-2"),
        "stop_sequence",
    ] = 20
    updates = _vehicle_updates().iloc[[0, 1]].copy()
    updates["trip_id"] = ["trip-main", "trip-short"]
    updates["current_stop_sequence"] = [2, 20]

    result = run_congestion_engine(updates, stops, times, trips, shapes)

    assert len(result.segments) == 2
    assert result.vehicle_probes["segment_id"].tolist() == [0, 0]
    assert result.baseline_speeds.iloc[0]["baseline_speed"] == 15.0


def test_distinguishes_express_and_local_stop_pairs_with_same_sequence_number() -> None:
    stops, times, trips, shapes = _static_data()
    times.loc[
        times["trip_id"].eq("trip-short") & times["stop_sequence"].eq(2),
        "stop_id",
    ] = "stop-3"
    updates = _vehicle_updates().iloc[[0, 1]].copy()
    updates["trip_id"] = ["trip-main", "trip-short"]
    updates["stop_id"] = ["stop-2", "stop-3"]

    result = run_congestion_engine(updates, stops, times, trips, shapes)

    assert result.vehicle_probes["segment_id"].nunique() == 2
    matched = result.vehicle_probes.set_index("trip_id")
    assert matched.loc["trip-main", "to_stop_name"] == "Second"
    assert matched.loc["trip-short", "to_stop_name"] == "Third"
    assert len(result.baseline_speeds) == 2


def test_keeps_different_shapes_separate_even_with_the_same_directed_stop_pair() -> (
    None
):
    stops, times, trips, shapes = _static_data()
    trips.loc[trips["trip_id"].eq("trip-short"), "shape_id"] = "detour"
    shapes = pd.concat(
        [
            shapes,
            pd.DataFrame(
                {
                    "shape_id": ["detour"] * 3,
                    "shape_pt_sequence": [1, 2, 3],
                    "shape_pt_lat": [0.0, 1.0, 0.0],
                    "shape_pt_lon": [0.0, 0.5, 1.0],
                }
            ),
        ],
        ignore_index=True,
    )
    updates = _vehicle_updates().iloc[[0, 1]].copy()
    updates["trip_id"] = ["trip-main", "trip-short"]

    result = run_congestion_engine(updates, stops, times, trips, shapes)

    assert result.vehicle_probes["segment_id"].nunique() == 2
    assert result.baseline_speeds["shape_id"].nunique() == 2
    assert sorted(result.baseline_speeds["baseline_speed"]) == [10.0, 20.0]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("trip_id", "unknown-trip"),
        ("trip_direction_id", 1),
        ("trip_direction_id", None),
        ("stop_id", "stop-1"),
        ("current_stop_sequence", 99),
    ],
)
def test_warns_and_skips_updates_without_an_exact_gtfs_match(
    column: str, value: object
) -> None:
    updates = _vehicle_updates().iloc[[0, 1]].copy()
    updates[column] = [updates.iloc[0][column], value]

    with pytest.warns(UserWarning, match="Skipping 1 vehicle updates"):
        result = run_congestion_engine(updates, *_static_data())

    assert len(result.vehicle_probes) == 1
    assert result.vehicle_probes.iloc[0]["position_speed"] == 10.0


def test_returns_explicit_empty_outputs_when_all_updates_are_unmatched() -> None:
    updates = _vehicle_updates().iloc[[0]].assign(trip_id="unknown-trip")

    with pytest.warns(UserWarning, match="no GTFS match"):
        result = run_congestion_engine(updates, *_static_data())

    assert result.vehicle_probes.empty
    assert result.baseline_speeds.empty
    assert result.congestion.empty
    assert "trip_direction_id" in result.congestion
    assert "shape_id" in result.congestion


def test_excludes_origin_stop_without_warning(recwarn: pytest.WarningsRecorder) -> None:
    updates = (
        _vehicle_updates().iloc[[0]].assign(current_stop_sequence=1, stop_id="stop-1")
    )

    result = run_congestion_engine(updates, *_static_data())

    assert result.vehicle_probes.empty
    assert not recwarn


@pytest.mark.parametrize("direction", [-1, 2, True])
def test_rejects_invalid_requested_direction(direction: int) -> None:
    with pytest.raises(ValueError, match="trip_direction_id must be"):
        run_congestion_engine(
            _vehicle_updates(), *_static_data(), trip_direction_id=direction
        )


@pytest.mark.parametrize("direction", [None, 2])
def test_requires_unambiguous_gtfs_directions(direction: int | None) -> None:
    stops, times, trips, shapes = _static_data()
    trips["direction_id"] = direction

    with pytest.raises(ValueError, match="direction IDs"):
        run_congestion_engine(_vehicle_updates(), stops, times, trips, shapes)


def test_rejects_duplicate_trip_stop_sequences() -> None:
    stops, times, trips, shapes = _static_data()
    times = pd.concat([times, times.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate trip/stop sequences"):
        run_congestion_engine(_vehicle_updates(), stops, times, trips, shapes)


def test_stop_sequence_order_is_not_silently_replaced_by_geometry_order() -> None:
    stops, times, trips, shapes = _static_data()
    times.loc[times["trip_id"].eq("trip-main"), "stop_sequence"] = [3, 2, 1]

    with pytest.raises(ValueError, match="stop_sequence order"):
        run_congestion_engine(_vehicle_updates(), stops, times, trips, shapes)
