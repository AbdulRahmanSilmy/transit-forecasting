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
