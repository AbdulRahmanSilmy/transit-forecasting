from __future__ import annotations

import pandas as pd
import pytest

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def _ingestion():
    return di.VehicleUpdatesDataIngestion(
        connection_params={
            cons.CP_HOST_KEY: "localhost",
            cons.CP_USER_KEY: "user",
            cons.CP_PASSWORD_KEY: "pass",
            cons.CP_DATABASE_KEY: "db",
            cons.CP_PORT_KEY: 3306,
        },
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/feed.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 5,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 10,
        },
    )


def test_to_python_datetime_rejects_small_year():
    assert di.DataIngestion._to_python_datetime("0001-01-01") is None


def test_format_df_feed_adds_timestamp_and_normalizes():
    ingestion = _ingestion()
    trip_fields = cons.TripTableIngestionFields
    vehicle_fields = cons.VehicleTableIngestionFields
    df = pd.DataFrame(
        {
            trip_fields.trip_start_date: ["20240101"],
            trip_fields.trip_start_time: ["12:00:00"],
            vehicle_fields.timestamp: [1700000000],
            vehicle_fields.feed_timestamp: [1700000001],
            vehicle_fields.entity_id: ["1"],
            vehicle_fields.vehicle_id: ["veh1"],
        }
    )

    formatted = ingestion._format_df_feed(df)

    assert trip_fields.trip_start_time not in formatted.columns
    assert trip_fields.trip_start_date not in formatted.columns
    assert trip_fields.trip_start_timestamp in formatted.columns
    assert formatted[vehicle_fields.timestamp].dtype == object
    assert formatted[vehicle_fields.feed_timestamp].dtype == object


@pytest.mark.parametrize(
    ("start_date", "start_time", "expected"),
    [
        ("20261104", "12:00:00", pd.Timestamp("2026-11-04 12:00:00")),
        ("20261104", "08:05:30", pd.Timestamp("2026-11-04 08:05:30")),
        ("20261104", "24:14:00", pd.Timestamp("2026-11-05 00:14:00")),
        ("20261104", "25:58:00", pd.Timestamp("2026-11-05 01:58:00")),
        ("20261231", "24:30:00", pd.Timestamp("2027-01-01 00:30:00")),
    ],
)
def test_parse_trip_start_handles_post_midnight_times(start_date, start_time, expected):
    result = di.DataIngestion._parse_trip_start(
        pd.Series([start_date]), pd.Series([start_time])
    )

    assert result.iloc[0] == expected


@pytest.mark.parametrize(
    ("start_date", "start_time"),
    [
        (None, "12:00:00"),
        ("20261104", None),
        ("", "12:00:00"),
        ("20261104", ""),
        ("2026-11-04", "12:00:00"),
        ("20261104", "1 day"),
        ("20261104", "12:00"),
    ],
)
def test_parse_trip_start_returns_nat_for_missing_or_malformed(start_date, start_time):
    result = di.DataIngestion._parse_trip_start(
        pd.Series([start_date], dtype=object), pd.Series([start_time], dtype=object)
    )

    assert pd.isna(result.iloc[0])


def test_format_df_feed_keeps_post_midnight_trip_start():
    ingestion = _ingestion()
    trip_fields = cons.TripTableIngestionFields
    df = pd.DataFrame(
        {
            trip_fields.trip_start_date: ["20261104", "20261104"],
            trip_fields.trip_start_time: ["23:50:00", "24:14:00"],
        }
    )

    formatted = ingestion._format_df_feed(df)

    assert list(formatted[trip_fields.trip_start_timestamp]) == [
        pd.Timestamp("2026-11-04 23:50:00"),
        pd.Timestamp("2026-11-05 00:14:00"),
    ]


def test_prepare_insert_rows_uses_ingestion_field_order():
    ingestion = _ingestion()
    fields = cons.VehicleTableIngestionFields

    # Intentionally shuffled DataFrame columns to ensure method reorders safely.
    df = pd.DataFrame(
        {
            fields.position_longitude: [-119.5],
            fields.feed_timestamp: [pd.Timestamp("2024-01-01 00:00:00")],
            fields.entity_id: [1],
            fields.trip_id: ["trip-1"],
            fields.trip_start_time: ["12:00:00"],
            fields.trip_start_date: ["20240101"],
            fields.trip_schedule_relationship: [0],
            fields.trip_route_id: ["97"],
            fields.trip_direction_id: [0],
            fields.position_latitude: [49.88],
            fields.position_bearing: [1.0],
            fields.position_odometer: [None],
            fields.position_speed: [10.0],
            fields.current_stop_sequence: [12],
            fields.current_status: [2],
            fields.timestamp: [pd.Timestamp("2024-01-01 00:00:01")],
            fields.congestion_level: [None],
            fields.stop_id: ["stop-1"],
            fields.vehicle_id: ["veh-1"],
            fields.vehicle_label: ["label-1"],
            fields.trip_start_timestamp: [pd.Timestamp("2024-01-01 12:00:00")],
        }
    )

    rows = ingestion._prepare_insert_rows(df)
    assert len(rows) == 1

    expected = tuple(df[ingestion._ordered_ingestion_columns].iloc[0])
    assert rows[0] == expected
