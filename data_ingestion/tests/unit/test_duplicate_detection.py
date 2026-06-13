from __future__ import annotations

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def test_vehicle_update_duplicate_detection():
    fields = cons.VehicleTableIngestionFields
    latest = {}
    entity = {fields.VEHICLE_ID: "veh1", fields.TIMESTAMP: 10}

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is True

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is False

    newer = {fields.VEHICLE_ID: "veh1", fields.TIMESTAMP: 11}
    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(newer, latest)
    assert cond is True


def test_trip_update_duplicate_detection():
    fields = cons.TripTableIngestionFields
    latest = {}
    base = {
        fields.TRIP_ID: "trip1",
        fields.STOP_ID: "stop1",
        fields.ARRIVAL_DELAY: 0,
        fields.ARRIVAL_TIME: 1,
        fields.ARRIVAL_UNCERTAINTY: 0,
        fields.DEPARTURE_DELAY: 0,
        fields.DEPARTURE_TIME: 2,
        fields.DEPARTURE_UNCERTAINTY: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is True

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is False

    changed = dict(base)
    changed[fields.ARRIVAL_DELAY] = 5
    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(changed, latest)
    assert cond is True


def test_trip_update_missing_identifiers():
    fields = cons.TripTableIngestionFields
    latest = {}
    entity = {
        fields.TRIP_ID: None,
        fields.STOP_ID: None,
        fields.ARRIVAL_DELAY: 0,
        fields.ARRIVAL_TIME: 1,
        fields.ARRIVAL_UNCERTAINTY: 0,
        fields.DEPARTURE_DELAY: 0,
        fields.DEPARTURE_TIME: 2,
        fields.DEPARTURE_UNCERTAINTY: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(entity, latest)
    assert cond is False
