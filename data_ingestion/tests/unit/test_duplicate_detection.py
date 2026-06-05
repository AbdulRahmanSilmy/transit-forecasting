from __future__ import annotations

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def test_vehicle_update_duplicate_detection():
    latest = {}
    entity = {cons.VEHICLE_ID_KEY: "veh1", cons.TIMESTAMP_KEY: 10}

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is True

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is False

    newer = {cons.VEHICLE_ID_KEY: "veh1", cons.TIMESTAMP_KEY: 11}
    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(newer, latest)
    assert cond is True


def test_trip_update_duplicate_detection():
    latest = {}
    base = {
        cons.TRIP_ID_KEY: "trip1",
        cons.STOP_ID_KEY: "stop1",
        cons.ARRIVAL_DELAY_KEY: 0,
        cons.ARRIVAL_TIME_KEY: 1,
        cons.ARRIVAL_UNCERTAINTY_KEY: 0,
        cons.DEPARTURE_DELAY_KEY: 0,
        cons.DEPARTURE_TIME_KEY: 2,
        cons.DEPARTURE_UNCERTAINTY_KEY: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is True

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is False

    changed = dict(base)
    changed[cons.ARRIVAL_DELAY_KEY] = 5
    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(changed, latest)
    assert cond is True


def test_trip_update_missing_identifiers():
    latest = {}
    entity = {
        cons.TRIP_ID_KEY: None,
        cons.STOP_ID_KEY: None,
        cons.ARRIVAL_DELAY_KEY: 0,
        cons.ARRIVAL_TIME_KEY: 1,
        cons.ARRIVAL_UNCERTAINTY_KEY: 0,
        cons.DEPARTURE_DELAY_KEY: 0,
        cons.DEPARTURE_TIME_KEY: 2,
        cons.DEPARTURE_UNCERTAINTY_KEY: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(entity, latest)
    assert cond is False
