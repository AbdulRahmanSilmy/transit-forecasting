from __future__ import annotations

import os

import mysql.connector
import pytest
from google.transit import gtfs_realtime_pb2

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di


def _get_connection_params() -> dict:
    return {
        cons.CP_HOST_KEY: os.getenv("MYSQL_HOST", "127.0.0.1"),
        cons.CP_USER_KEY: os.getenv("MYSQL_USER", "transit"),
        cons.CP_PASSWORD_KEY: os.getenv("MYSQL_PASSWORD", "transit123"),
        cons.CP_DATABASE_KEY: os.getenv("MYSQL_DATABASE", "transitdb"),
        cons.CP_PORT_KEY: int(os.getenv("MYSQL_PORT", "3307")),
    }


def _skip_if_disabled() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Integration tests require RUN_INTEGRATION_TESTS=1")


def _build_vehicle_feed_bytes() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1700000000

    entity = feed.entity.add()
    entity.id = "1"
    vehicle = entity.vehicle
    vehicle.trip.trip_id = "trip1"
    vehicle.trip.start_time = "12:00:00"
    vehicle.trip.start_date = "20240101"
    vehicle.trip.schedule_relationship = 0
    vehicle.trip.route_id = "route1"
    vehicle.trip.direction_id = 1
    vehicle.position.latitude = 1.23
    vehicle.position.longitude = 3.21
    vehicle.position.bearing = 90.0
    vehicle.position.odometer = 100.5
    vehicle.position.speed = 12.5
    vehicle.current_stop_sequence = 1
    vehicle.current_status = 0
    vehicle.timestamp = 1700000001
    vehicle.congestion_level = 0
    vehicle.stop_id = "stop1"
    vehicle.vehicle.id = "veh1"
    vehicle.vehicle.label = "label1"

    return feed.SerializeToString()


def _build_trip_feed_bytes() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1700001000

    entity = feed.entity.add()
    entity.id = "1"
    trip_update = entity.trip_update
    trip_update.trip.trip_id = "trip1"
    trip_update.trip.start_time = "12:00:00"
    trip_update.trip.start_date = "20240101"
    trip_update.trip.schedule_relationship = 0
    trip_update.trip.route_id = "route1"
    trip_update.trip.direction_id = 1

    stu = trip_update.stop_time_update.add()
    stu.stop_sequence = 1
    stu.stop_id = "stop1"
    stu.schedule_relationship = 0
    stu.arrival.delay = 5
    stu.arrival.time = 1700001001
    stu.arrival.uncertainty = 0
    stu.departure.delay = 3
    stu.departure.time = 1700001002
    stu.departure.uncertainty = 0

    return feed.SerializeToString()


@pytest.mark.integration
def test_vehicle_updates_insert_round_trip():
    _skip_if_disabled()

    test_table = f"{cons.VEHICLE_UPDATE_TABLE}_TEST"

    ingestion = di.VehicleUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/vehicle.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )

    ingestion.TABLE_NAME = test_table
    ingestion.INSERT_QUERY = ingestion.INSERT_QUERY.replace(
        cons.VEHICLE_UPDATE_TABLE, test_table
    )

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        create_query = ingestion.CREATE_TABLE_QUERY.replace(
            cons.VEHICLE_UPDATE_TABLE, test_table
        )
        cur.execute(create_query)
        conn.commit()

        ingestion._process_and_insert_data(_build_vehicle_feed_bytes(), conn, cur)

        cur.execute(f"SELECT COUNT(*) FROM {ingestion.TABLE_NAME}")
        count = cur.fetchone()[0]
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {test_table}")
        conn.commit()
        cur.close()
        conn.close()

    assert count == 1


@pytest.mark.integration
def test_trip_updates_insert_round_trip():
    _skip_if_disabled()

    test_table = f"{cons.TRIP_UPDATE_TABLE}_TEST"

    ingestion = di.TripUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/trip.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )

    ingestion.TABLE_NAME = test_table
    ingestion.INSERT_QUERY = ingestion.INSERT_QUERY.replace(
        cons.TRIP_UPDATE_TABLE, test_table
    )

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        create_query = ingestion.CREATE_TABLE_QUERY.replace(
            cons.TRIP_UPDATE_TABLE, test_table
        )
        cur.execute(create_query)
        conn.commit()

        ingestion._process_and_insert_data(_build_trip_feed_bytes(), conn, cur)

        cur.execute(f"SELECT COUNT(*) FROM {ingestion.TABLE_NAME}")
        count = cur.fetchone()[0]
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {test_table}")
        conn.commit()
        cur.close()
        conn.close()

    assert count == 1
