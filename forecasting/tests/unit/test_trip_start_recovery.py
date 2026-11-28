from __future__ import annotations

import pandas as pd
import pytest

from forecasting.trip_start_recovery import (
    RECOVERED_COLUMN,
    fill_missing_trip_starts,
    fill_missing_vehicle_trip_starts,
)

# pylint: disable=missing-docstring


def _publication(
    feed: str,
    trip_id: str,
    start,
    stops: list[tuple[int, str, int]],
    *,
    first_id: int,
) -> list[dict]:
    """Rows of one publication: (stop_sequence, scheduled UTC, delay seconds)."""
    rows = []
    for offset, (sequence, scheduled, delay) in enumerate(stops):
        event = pd.Timestamp(scheduled) + pd.Timedelta(seconds=delay)
        rows.append(
            {
                "id": first_id + offset,
                "feed_timestamp": pd.Timestamp(feed),
                "trip_id": trip_id,
                "trip_start_timestamp": start,
                "stop_sequence": sequence,
                "arrival_time": event,
                "arrival_delay": delay,
                "departure_time": event,
                "departure_delay": delay,
            }
        )
    return rows


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["trip_start_timestamp"] = pd.to_datetime(frame["trip_start_timestamp"])
    return frame


def test_fills_post_midnight_journeys_per_day():
    # 2026-11-05 00:14 local (UTC-7) is 07:14 UTC; the same trip runs next day.
    rows = (
        _publication(
            "2026-11-05 07:00",
            "late",
            None,
            [(1, "2026-11-05 07:14", 0), (2, "2026-11-05 07:40", 0)],
            first_id=1,
        )
        + _publication(
            "2026-11-05 07:20",
            "late",
            None,
            [(2, "2026-11-05 07:40", 120)],
            first_id=10,
        )
        + _publication(
            "2026-11-06 07:00",
            "late",
            None,
            [(1, "2026-11-06 07:14", 60), (2, "2026-11-06 07:40", 60)],
            first_id=20,
        )
        + _publication(
            "2026-11-05 15:00",
            "day",
            "2026-11-05 08:30",
            [(1, "2026-11-05 15:30", 0)],
            first_id=30,
        )
    )
    trip_updates = _frame(rows)

    result = fill_missing_trip_starts(trip_updates)

    late = result.loc[result["trip_id"].eq("late")]
    assert (
        late["trip_start_timestamp"].tolist()
        == [pd.Timestamp("2026-11-05 00:14")] * 3
        + [pd.Timestamp("2026-11-06 00:14")] * 2
    )
    assert late[RECOVERED_COLUMN].all()
    day = result.loc[result["trip_id"].eq("day")]
    assert day["trip_start_timestamp"].tolist() == [pd.Timestamp("2026-11-05 08:30")]
    assert not day[RECOVERED_COLUMN].any()
    assert (
        result["trip_start_timestamp"].dtype
        == trip_updates["trip_start_timestamp"].dtype
    )
    assert trip_updates["trip_start_timestamp"].isna().sum() == 5


def test_uses_earliest_publication_of_first_stop():
    # A later publication reports a shifted first-stop schedule; the earliest wins.
    rows = _publication(
        "2026-11-05 07:00", "late", None, [(1, "2026-11-05 07:14", 0)], first_id=1
    ) + _publication(
        "2026-11-05 07:05", "late", None, [(1, "2026-11-05 07:20", 0)], first_id=2
    )

    result = fill_missing_trip_starts(_frame(rows))

    assert (
        result["trip_start_timestamp"].tolist()
        == [pd.Timestamp("2026-11-05 00:14")] * 2
    )


def test_falls_back_to_arrival_schedule_at_last_stop():
    rows = _publication(
        "2026-11-05 07:00", "late", None, [(1, "2026-11-05 07:14", 30)], first_id=1
    )
    rows[0]["departure_time"] = pd.NaT

    result = fill_missing_trip_starts(_frame(rows))

    assert result["trip_start_timestamp"].iloc[0] == pd.Timestamp("2026-11-05 00:14")


def test_leaves_journeys_first_seen_mid_route_null():
    rows = _publication(
        "2026-11-05 07:00", "late", None, [(1, "2026-11-05 07:14", 0)], first_id=1
    ) + _publication(
        "2026-11-06 07:30", "late", None, [(3, "2026-11-06 07:50", 0)], first_id=2
    )

    result = fill_missing_trip_starts(_frame(rows))

    assert result["trip_start_timestamp"].iloc[0] == pd.Timestamp("2026-11-05 00:14")
    assert pd.isna(result["trip_start_timestamp"].iloc[1])
    assert result[RECOVERED_COLUMN].tolist() == [True, False]


def test_string_descriptors_stay_strings():
    rows = _publication(
        "2026-11-05 07:00", "late", "", [(1, "2026-11-05 07:14", 0)], first_id=1
    ) + _publication(
        "2026-11-05 15:00",
        "day",
        "2026-11-05 08:30:00",
        [(1, "2026-11-05 15:30", 0)],
        first_id=2,
    )
    trip_updates = pd.DataFrame(rows)

    result = fill_missing_trip_starts(trip_updates)

    assert result["trip_start_timestamp"].tolist() == [
        "2026-11-05 00:14:00",
        "2026-11-05 08:30:00",
    ]


def test_no_missing_descriptors_returns_copy():
    rows = _publication(
        "2026-11-05 15:00",
        "day",
        "2026-11-05 08:30",
        [(1, "2026-11-05 15:30", 0)],
        first_id=1,
    )
    trip_updates = _frame(rows)

    result = fill_missing_trip_starts(trip_updates)

    assert result is not trip_updates
    assert not result[RECOVERED_COLUMN].any()
    pd.testing.assert_frame_equal(result.drop(columns=RECOVERED_COLUMN), trip_updates)


def test_trip_updates_require_columns():
    with pytest.raises(ValueError, match="arrival_delay"):
        fill_missing_trip_starts(
            pd.DataFrame(
                columns=[
                    "trip_id",
                    "trip_start_timestamp",
                    "stop_sequence",
                    "feed_timestamp",
                    "arrival_time",
                ]
            )
        )


def _vehicles(rows: list[tuple[str, str | None, str]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["trip_id", "trip_start_timestamp", "timestamp"]
    ).assign(feed_timestamp=lambda df: df["timestamp"])
    for column in ("trip_start_timestamp", "timestamp", "feed_timestamp"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def test_vehicles_take_nearest_journey_of_same_trip():
    trip_updates = pd.DataFrame(
        {
            "trip_id": ["late", "late", "other"],
            "trip_start_timestamp": pd.to_datetime(
                ["2026-11-05 00:14", "2026-11-06 00:14", "2026-11-05 00:14"]
            ),
        }
    )
    vehicles = _vehicles(
        [
            ("late", None, "2026-11-05 07:30"),
            ("late", None, "2026-11-06 07:05"),
            ("late", None, "2026-11-05 20:00"),  # 12+ hours from both journeys
            ("day", "2026-11-05 08:30", "2026-11-05 15:30"),
        ]
    )

    result = fill_missing_vehicle_trip_starts(vehicles, trip_updates)

    assert result["trip_start_timestamp"].iloc[0] == pd.Timestamp("2026-11-05 00:14")
    assert result["trip_start_timestamp"].iloc[1] == pd.Timestamp("2026-11-06 00:14")
    assert pd.isna(result["trip_start_timestamp"].iloc[2])
    assert result["trip_start_timestamp"].iloc[3] == pd.Timestamp("2026-11-05 08:30")
    assert result[RECOVERED_COLUMN].tolist() == [True, True, False, False]
    assert vehicles["trip_start_timestamp"].isna().sum() == 3


def test_vehicles_fall_back_to_feed_timestamp():
    trip_updates = pd.DataFrame(
        {
            "trip_id": ["late"],
            "trip_start_timestamp": pd.to_datetime(["2026-11-05 00:14"]),
        }
    )
    vehicles = _vehicles([("late", None, "2026-11-05 07:30")])
    vehicles["timestamp"] = pd.NaT

    result = fill_missing_vehicle_trip_starts(vehicles, trip_updates)

    assert result["trip_start_timestamp"].iloc[0] == pd.Timestamp("2026-11-05 00:14")
