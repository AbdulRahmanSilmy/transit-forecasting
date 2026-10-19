import pandas as pd
import pandas.testing as pdt

from forecasting import get_trip_updates_for_trip


def _vehicle_updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trip_id": ["trip-1", "trip-1"],
            "stop_id": ["stop-1", "stop-1"],
            "feed_timestamp": pd.to_datetime(
                ["2024-01-01 10:00:12", "2024-01-01 10:00:05"]
            ),
        },
        index=[3, 1],
    )


def _trip_updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trip_id": ["trip-1", "trip-1", "trip-1", "trip-2"],
            "stop_id": ["stop-1", "stop-1", "stop-2", "stop-1"],
            "feed_timestamp": pd.to_datetime(
                [
                    "2024-01-01 10:00:11",
                    "2024-01-01 10:00:04",
                    "2024-01-01 10:00:11",
                    "2024-01-01 10:00:11",
                ]
            ),
            "arrival_delay": [11, 4, 99, 88],
            "departure_delay": [12, 5, 100, 89],
            "arrival_time": ["11:00:11", "11:00:04", "11:00:11", "11:00:11"],
            "departure_time": ["11:01:11", "11:01:04", "11:01:11", "11:01:11"],
        }
    )


def test_assigns_first_eligible_update_for_matching_trip_and_stop() -> None:
    vehicle_updates = _vehicle_updates()
    original = vehicle_updates.copy(deep=True)

    result = get_trip_updates_for_trip(vehicle_updates, _trip_updates())

    assert result.index.tolist() == [1, 3]
    assert result["arrival_delay"].tolist() == [4, 11]
    assert result["departure_delay"].tolist() == [5, 12]
    assert result["arrival_time"].tolist() == ["11:00:04", "11:00:11"]
    assert result["departure_time"].tolist() == ["11:01:04", "11:01:11"]
    pdt.assert_frame_equal(vehicle_updates, original)


def test_offset_moves_trip_update_cutoff_backwards() -> None:
    vehicle_updates = _vehicle_updates().iloc[[0]]

    result = get_trip_updates_for_trip(
        vehicle_updates,
        _trip_updates(),
        offset=pd.Timedelta(seconds=2),
    )

    assert result.iloc[0]["arrival_delay"] == 4


def test_missing_eligible_update_sets_update_fields_to_nan() -> None:
    vehicle_updates = _vehicle_updates().iloc[[1]]
    trip_updates = _trip_updates().iloc[0:0]

    result = get_trip_updates_for_trip(vehicle_updates, trip_updates)

    assert (
        result[["arrival_delay", "departure_delay", "arrival_time", "departure_time"]]
        .isna()
        .all()
        .all()
    )
