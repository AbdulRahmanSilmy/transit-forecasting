import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import get_latest_stop_delays, get_trip_updates_for_trip


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


def _stop_delay_updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [2, 1, 3, 4, 5],
            "trip_route_id": ["97-KEL", "97-KEL", "97-KEL", "other", "97-KEL"],
            "trip_id": ["trip-1", "trip-1", "trip-2", "trip-1", "trip-1"],
            "trip_start_timestamp": ["2026-09-08 16:00:00"] * 5,
            "stop_id": ["140002", "140002", "140003", "140002", "elsewhere"],
            "stop_sequence": [7, 7, 8, 7, 9],
            "feed_timestamp": [
                "2026-09-09 00:12:00",
                "2026-09-09 00:10:00",
                "2026-09-09 00:11:00",
                "2026-09-09 00:13:00",
                "2026-09-09 00:14:00",
            ],
            "arrival_delay": [120, 60, -30, 999, 999],
        },
        index=[8, 5, 3, 1, 2],
    )


def _delay_stops() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stop_id": [140002, 140003, 999999],
            "stop_name": [
                "Harvey Ave at Gordon Stn",
                "Harvey Ave at Gordon Stn",
                "Another stop",
            ],
        }
    )


def _latest_stop_delays(updates: pd.DataFrame) -> pd.DataFrame:
    return get_latest_stop_delays(
        updates,
        _delay_stops(),
        route_id="97-KEL",
        stop_name="Harvey Ave at Gordon Stn",
    )


def test_latest_stop_delays_matches_route_and_both_platforms_without_mutation() -> None:
    updates = _stop_delay_updates()
    stops = _delay_stops()
    original_updates = updates.copy(deep=True)
    original_stops = stops.copy(deep=True)

    result = get_latest_stop_delays(
        updates,
        stops,
        route_id="97-KEL",
        stop_name="Harvey Ave at Gordon Stn",
    )

    assert result["id"].tolist() == [3, 2]
    assert result["stop_id"].tolist() == ["140003", "140002"]
    assert result["arrival_delay"].tolist() == [-30, 120]
    assert result["delay_minutes"].tolist() == [-0.5, 2.0]
    assert str(result["feed_timestamp"].dt.tz) == "UTC"
    assert result["stop_name"].eq("Harvey Ave at Gordon Stn").all()
    pdt.assert_frame_equal(updates, original_updates)
    pdt.assert_frame_equal(stops, original_stops)


def test_latest_stop_delays_is_independent_of_input_order() -> None:
    updates = _stop_delay_updates()

    pdt.assert_frame_equal(
        _latest_stop_delays(updates),
        _latest_stop_delays(updates.iloc[::-1]),
    )


def test_latest_stop_delays_keeps_daily_instances_and_repeated_visits() -> None:
    updates = _stop_delay_updates().iloc[[0, 0, 0, 0]].copy()
    updates["id"] = [1, 2, 3, 4]
    updates["trip_start_timestamp"] = [
        "2026-09-08 16:00:00",
        "2026-09-09 16:00:00",
        "2026-09-08 16:00:00",
        "2026-09-08 16:00:00",
    ]
    updates["feed_timestamp"] = [
        "2026-09-08 23:59:00",
        "2026-09-10 00:12:00",
        "2026-09-09 00:12:00",
        "2026-09-09 00:15:00",
    ]
    updates["stop_sequence"] = [7, 7, 7, 12]

    result = _latest_stop_delays(updates)

    assert result["id"].tolist() == [3, 4, 2]
    assert result["trip_instance_key"].nunique() == 2


def test_latest_stop_delays_breaks_timestamp_ties_by_database_id() -> None:
    updates = _stop_delay_updates().iloc[[0, 1]].copy()
    updates["feed_timestamp"] = "2026-09-09 00:12:00"

    assert _latest_stop_delays(updates.iloc[::-1])["id"].tolist() == [2]

    without_ids = updates.drop(columns="id")
    assert _latest_stop_delays(without_ids)["arrival_delay"].tolist() == [120]


def test_latest_stop_delays_does_not_replace_missing_latest_delay() -> None:
    updates = _stop_delay_updates().iloc[[0, 1]].copy()
    updates.loc[updates["id"].eq(2), "arrival_delay"] = float("nan")

    result = _latest_stop_delays(updates)

    assert result["id"].tolist() == [2]
    assert result["arrival_delay"].isna().all()
    assert result["delay_minutes"].isna().all()


def test_latest_stop_delays_warns_when_start_descriptors_are_missing() -> None:
    updates = _stop_delay_updates().iloc[[0, 1]].copy()
    updates["trip_start_timestamp"] = None
    updates["feed_timestamp"] = [
        "2026-09-09 00:12:00",
        "2026-09-08 00:12:00",
    ]

    with pytest.warns(UserWarning, match="publication date is approximate"):
        result = _latest_stop_delays(updates)

    assert len(result) == 2
    assert result["trip_instance_key"].str.contains("missing-start-").all()


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("feed_timestamp", None, "missing feed timestamps"),
        ("feed_timestamp", "not a timestamp", "not a timestamp"),
        ("trip_id", None, "missing trip IDs"),
        ("trip_id", " ", "missing trip IDs"),
        ("arrival_delay", "not a delay", "not a delay"),
        ("arrival_delay", float("inf"), "infinite arrival delays"),
    ],
)
def test_latest_stop_delays_rejects_invalid_matching_rows(
    column: str, value: object, message: str
) -> None:
    updates = _stop_delay_updates()
    updates[column] = value

    with pytest.raises(ValueError, match=message):
        _latest_stop_delays(updates)


def test_latest_stop_delays_reports_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing required columns: arrival_delay"):
        _latest_stop_delays(_stop_delay_updates().drop(columns="arrival_delay"))


def test_latest_stop_delays_reports_unmatched_stop_name() -> None:
    with pytest.raises(ValueError, match="No GTFS stops"):
        get_latest_stop_delays(
            _stop_delay_updates(),
            _delay_stops(),
            route_id="97-KEL",
            stop_name="Unknown stop",
        )


def test_latest_stop_delays_reports_empty_or_unmatched_route_updates() -> None:
    updates = _stop_delay_updates()
    for frame in (
        updates.iloc[0:0],
        updates.assign(trip_route_id="another-route"),
    ):
        with pytest.raises(ValueError, match="No trip updates were found"):
            _latest_stop_delays(frame)


def test_latest_stop_delays_can_select_all_route_stops_and_audit_unknown_ids() -> None:
    updates = _stop_delay_updates()
    result = get_latest_stop_delays(updates, _delay_stops(), route_id="97-KEL")

    assert result["id"].tolist() == [3, 2, 5]
    assert result["stop_name"].iloc[:2].eq("Harvey Ave at Gordon Stn").all()
    assert pd.isna(result["stop_name"].iloc[-1])
    assert result["stop_id"].iloc[-1] == "elsewhere"


def test_latest_stop_delays_rejects_ambiguous_stop_metadata_for_all_stops() -> None:
    stops = pd.concat([_delay_stops(), _delay_stops().iloc[[0]]])
    with pytest.raises(ValueError, match="unique, nonmissing stop IDs"):
        get_latest_stop_delays(_stop_delay_updates(), stops, route_id="97-KEL")
