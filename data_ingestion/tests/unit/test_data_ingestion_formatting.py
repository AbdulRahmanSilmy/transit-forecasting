from __future__ import annotations

import pandas as pd

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
            trip_fields.TRIP_START_DATE_KEY: ["20240101"],
            trip_fields.TRIP_START_TIME_KEY: ["12:00:00"],
            vehicle_fields.TIMESTAMP_KEY: [1700000000],
            vehicle_fields.FEED_TIMESTAMP_KEY: [1700000001],
            vehicle_fields.ENTITY_ID_KEY: ["1"],
            vehicle_fields.VEHICLE_ID_KEY: ["veh1"],
        }
    )

    formatted = ingestion._format_df_feed(df)

    assert trip_fields.TRIP_START_TIME_KEY not in formatted.columns
    assert trip_fields.TRIP_START_DATE_KEY not in formatted.columns
    assert trip_fields.TRIP_START_TIMESTAMP_KEY in formatted.columns
    assert formatted[vehicle_fields.TIMESTAMP_KEY].dtype == object
    assert formatted[vehicle_fields.FEED_TIMESTAMP_KEY].dtype == object


def test_prepare_insert_rows_uses_ingestion_field_order():
    ingestion = _ingestion()
    fields = cons.VehicleTableIngestionFields

    # Intentionally shuffled DataFrame columns to ensure method reorders safely.
    df = pd.DataFrame(
        {
            fields.POSITION_LONGITUDE: [-119.5],
            fields.FEED_TIMESTAMP: [pd.Timestamp("2024-01-01 00:00:00")],
            fields.ENTITY_ID: [1],
            fields.TRIP_ID: ["trip-1"],
            fields.TRIP_START_TIME: ["12:00:00"],
            fields.TRIP_START_DATE: ["20240101"],
            fields.TRIP_SCHEDULE_RELATIONSHIP: [0],
            fields.TRIP_ROUTE_ID: ["97"],
            fields.TRIP_DIRECTION_ID: [0],
            fields.POSITION_LATITUDE: [49.88],
            fields.POSITION_BEARING: [1.0],
            fields.POSITION_ODOMETER: [None],
            fields.POSITION_SPEED: [10.0],
            fields.CURRENT_STOP_SEQUENCE: [12],
            fields.CURRENT_STATUS: [2],
            fields.TIMESTAMP: [pd.Timestamp("2024-01-01 00:00:01")],
            fields.CONGESTION_LEVEL: [None],
            fields.STOP_ID: ["stop-1"],
            fields.VEHICLE_ID: ["veh-1"],
            fields.VEHICLE_LABEL: ["label-1"],
            fields.TRIP_START_TIMESTAMP: [pd.Timestamp("2024-01-01 12:00:00")],
        }
    )

    rows = ingestion._prepare_insert_rows(df)
    assert len(rows) == 1

    expected = tuple(df[ingestion._ordered_ingestion_columns].iloc[0])
    assert rows[0] == expected
