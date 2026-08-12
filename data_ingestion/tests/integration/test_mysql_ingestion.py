from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import mysql.connector
import pytest
from google.transit import gtfs_realtime_pb2

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def _get_connection_params() -> dict:
    mysql_host = os.getenv("MYSQL_HOST")
    mysql_user = os.getenv("MYSQL_USER")
    mysql_password = os.getenv("MYSQL_PASSWORD")
    mysql_database = os.getenv("MYSQL_DATABASE")
    mysql_port = os.getenv("MYSQL_PORT")

    missing = [
        key
        for key, value in {
            "MYSQL_HOST": mysql_host,
            "MYSQL_USER": mysql_user,
            "MYSQL_PASSWORD": mysql_password,
            "MYSQL_DATABASE": mysql_database,
            "MYSQL_PORT": mysql_port,
        }.items()
        if not value
    ]
    if missing:
        missing_keys = ", ".join(missing)
        pytest.skip(f"Missing required integration-test env vars: {missing_keys}")

    try:
        parsed_port = int(mysql_port)
    except ValueError:
        pytest.skip("Invalid MYSQL_PORT for integration tests; expected an integer")

    return {
        cons.CP_HOST_KEY: mysql_host,
        cons.CP_USER_KEY: mysql_user,
        cons.CP_PASSWORD_KEY: mysql_password,
        cons.CP_DATABASE_KEY: mysql_database,
        cons.CP_PORT_KEY: parsed_port,
    }


def _skip_if_disabled() -> None:
    run_integration_tests = os.getenv("RUN_INTEGRATION_TESTS", "0").strip().lower()
    if run_integration_tests not in {"1", "true", "yes"}:
        pytest.skip("Integration tests require RUN_INTEGRATION_TESTS=1")


def _build_vehicle_feed_bytes() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1700000000

    for i in range(3):
        entity = feed.entity.add()
        entity.id = str(i + 1)
        vehicle = entity.vehicle
        vehicle.trip.trip_id = f"trip{i + 1}"
        vehicle.trip.start_time = "12:00:00"
        vehicle.trip.start_date = "20240101"
        vehicle.trip.schedule_relationship = 0
        vehicle.trip.route_id = f"route{i + 1}"
        vehicle.trip.direction_id = i % 2
        vehicle.position.latitude = 1.23 + (i * 0.01)
        vehicle.position.longitude = 3.21 + (i * 0.01)
        vehicle.position.bearing = 90.0 + i
        vehicle.position.odometer = 100.5 + i
        vehicle.position.speed = 12.5 + i
        vehicle.current_stop_sequence = i + 1
        vehicle.current_status = 0
        vehicle.timestamp = 1700000001 + i
        vehicle.congestion_level = 0
        vehicle.stop_id = f"stop{i + 1}"
        vehicle.vehicle.id = f"veh{i + 1}"
        vehicle.vehicle.label = f"label{i + 1}"

    return feed.SerializeToString()


def _build_trip_feed_bytes() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1700001000

    for i in range(2):
        entity = feed.entity.add()
        entity.id = str(i + 1)
        trip_update = entity.trip_update
        trip_update.trip.trip_id = f"trip{i + 1}"
        trip_update.trip.start_time = "12:00:00"
        trip_update.trip.start_date = "20240101"
        trip_update.trip.schedule_relationship = 0
        trip_update.trip.route_id = f"route{i + 1}"
        trip_update.trip.direction_id = i % 2

        for j in range(2):
            stu = trip_update.stop_time_update.add()
            stu.stop_sequence = (j + 1) * 10
            stu.stop_id = f"stop{i + 1}_{j + 1}"
            stu.schedule_relationship = 0
            stu.arrival.delay = 5 + j
            stu.arrival.time = 1700001001 + (i * 10) + j
            stu.arrival.uncertainty = 0
            stu.departure.delay = 3 + j
            stu.departure.time = 1700001002 + (i * 10) + j
            stu.departure.uncertainty = 0

    return feed.SerializeToString()


def _index_exists(conn, database_name: str, table_name: str, index_name: str) -> bool:
    """Return whether an index exists on a table."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (database_name, table_name, index_name),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


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

    assert count == 3


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

    assert count == 4


@pytest.mark.integration
def test_vehicle_schema_setup_adds_feed_timestamp_index_idempotently():
    _skip_if_disabled()

    test_table = f"{cons.VEHICLE_UPDATE_TABLE}_INDEX_TEST"

    ingestion = di.VehicleUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/vehicle.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )
    ingestion.TABLE_NAME = test_table

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        create_query = ingestion.CREATE_TABLE_QUERY.replace(
            cons.VEHICLE_UPDATE_TABLE, test_table
        )
        cur.execute(create_query)
        conn.commit()

        # Run twice to verify idempotency of the index migration.
        ingestion._create_table_if_not_exists(conn, cur)
        ingestion._create_table_if_not_exists(conn, cur)

        assert _index_exists(
            conn,
            ingestion.connection_params[cons.CP_DATABASE_KEY],
            test_table,
            cons.INDEX_FEED_TIMESTAMP_NAME,
        )
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {test_table}")
        conn.commit()
        cur.close()
        conn.close()


@pytest.mark.integration
def test_trip_schema_setup_adds_feed_timestamp_index_idempotently():
    _skip_if_disabled()

    test_table = f"{cons.TRIP_UPDATE_TABLE}_INDEX_TEST"

    ingestion = di.TripUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/trip.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )
    ingestion.TABLE_NAME = test_table

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        create_query = ingestion.CREATE_TABLE_QUERY.replace(
            cons.TRIP_UPDATE_TABLE, test_table
        )
        cur.execute(create_query)
        conn.commit()

        # Run twice to verify idempotency of the index migration.
        ingestion._create_table_if_not_exists(conn, cur)
        ingestion._create_table_if_not_exists(conn, cur)

        assert _index_exists(
            conn,
            ingestion.connection_params[cons.CP_DATABASE_KEY],
            test_table,
            cons.INDEX_FEED_TIMESTAMP_NAME,
        )
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {test_table}")
        conn.commit()
        cur.close()
        conn.close()


@pytest.mark.integration
def test_vehicle_ingestion_pipeline_with_test_table_and_clean_logs(
    tmp_path: Path, monkeypatch
):
    _skip_if_disabled()

    test_table = f"{cons.VEHICLE_UPDATE_TABLE}_PIPELINE_TEST"

    ingestion = di.VehicleUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/vehicle.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )

    ingestion.TABLE_NAME = test_table
    ingestion.CREATE_TABLE_QUERY = ingestion.CREATE_TABLE_QUERY.replace(
        cons.VEHICLE_UPDATE_TABLE, test_table
    )
    ingestion.INSERT_QUERY = ingestion.INSERT_QUERY.replace(
        cons.VEHICLE_UPDATE_TABLE, test_table
    )

    feed_bytes = _build_vehicle_feed_bytes()
    monkeypatch.setattr(di, "fetch_gtfs_data", lambda _url: feed_bytes)

    log_path = tmp_path / "vehicle_ingestion_pipeline.log"
    module_logger = logging.getLogger(di.__name__)
    previous_handlers = list(module_logger.handlers)
    previous_level = module_logger.level
    previous_propagate = module_logger.propagate

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    module_logger.handlers = [handler]
    module_logger.setLevel(logging.INFO)
    module_logger.propagate = False

    stop_event = threading.Event()
    thread = threading.Thread(
        target=ingestion.run_ingestion_loop,
        args=(stop_event,),
        daemon=True,
    )
    thread.start()

    # Allow at least one fetch/process cycle to complete.
    time.sleep(3)

    stop_event.set()
    thread.join(timeout=10)

    handler.flush()
    handler.close()
    module_logger.handlers = previous_handlers
    module_logger.setLevel(previous_level)
    module_logger.propagate = previous_propagate

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {ingestion.TABLE_NAME}")
        inserted_count = cur.fetchone()[0]
        cur.execute(f"DROP TABLE IF EXISTS {ingestion.TABLE_NAME}")
        conn.commit()
    finally:
        cur.close()
        conn.close()

    log_contents = log_path.read_text(encoding="utf-8")
    assert inserted_count >= 1
    assert "[ERROR]" not in log_contents
    assert "Traceback" not in log_contents


@pytest.mark.integration
def test_trip_ingestion_pipeline_with_test_table_and_clean_logs(
    tmp_path: Path, monkeypatch
):
    _skip_if_disabled()

    test_table = f"{cons.TRIP_UPDATE_TABLE}_PIPELINE_TEST"

    ingestion = di.TripUpdatesDataIngestion(
        connection_params=_get_connection_params(),
        data_ingestion_params={
            cons.DIP_INGESTION_URL_KEY: "http://example.com/trip.pb",
            cons.DIP_FETCH_DELAY_SECONDS_KEY: 1,
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 1,
        },
    )

    ingestion.TABLE_NAME = test_table
    ingestion.CREATE_TABLE_QUERY = ingestion.CREATE_TABLE_QUERY.replace(
        cons.TRIP_UPDATE_TABLE, test_table
    )
    ingestion.INSERT_QUERY = ingestion.INSERT_QUERY.replace(
        cons.TRIP_UPDATE_TABLE, test_table
    )

    feed_bytes = _build_trip_feed_bytes()
    monkeypatch.setattr(di, "fetch_gtfs_data", lambda _url: feed_bytes)

    log_path = tmp_path / "trip_ingestion_pipeline.log"
    module_logger = logging.getLogger(di.__name__)
    previous_handlers = list(module_logger.handlers)
    previous_level = module_logger.level
    previous_propagate = module_logger.propagate

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    module_logger.handlers = [handler]
    module_logger.setLevel(logging.INFO)
    module_logger.propagate = False

    stop_event = threading.Event()
    thread = threading.Thread(
        target=ingestion.run_ingestion_loop,
        args=(stop_event,),
        daemon=True,
    )
    thread.start()

    # Allow at least one fetch/process cycle to complete.
    time.sleep(3)

    stop_event.set()
    thread.join(timeout=10)

    handler.flush()
    handler.close()
    module_logger.handlers = previous_handlers
    module_logger.setLevel(previous_level)
    module_logger.propagate = previous_propagate

    conn = mysql.connector.connect(**ingestion.connection_params)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {ingestion.TABLE_NAME}")
        inserted_count = cur.fetchone()[0]
        cur.execute(f"DROP TABLE IF EXISTS {ingestion.TABLE_NAME}")
        conn.commit()
    finally:
        cur.close()
        conn.close()

    log_contents = log_path.read_text(encoding="utf-8")
    assert inserted_count >= 1
    assert "[ERROR]" not in log_contents
    assert "Traceback" not in log_contents
