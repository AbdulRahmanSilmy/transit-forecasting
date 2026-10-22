import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import run_congestion_engine


def _static_data() -> tuple[pd.DataFrame, ...]:
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
            "shape_id": ["shape-main", "shape-short"],
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
    original_vehicle_updates = vehicle_updates.copy(deep=True)

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
    pdt.assert_frame_equal(vehicle_updates, original_vehicle_updates)


def test_omits_segments_with_near_stationary_baseline() -> None:
    stops, stop_times, trips, shapes = _static_data()
    vehicle_updates = pd.DataFrame(
        {
            "trip_route_id": ["97-KEL"],
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
