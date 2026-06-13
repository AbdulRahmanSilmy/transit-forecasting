from __future__ import annotations

from data_ingestion import constants as cons
from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def test_vehicle_update_duplicate_detection():
    fields = cons.VehicleTableIngestionFields
    latest = {}
    entity = {fields.vehicle_id: "veh1", fields.timestamp: 10}

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is True

    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(
        entity, latest
    )
    assert cond is False

    newer = {fields.vehicle_id: "veh1", fields.timestamp: 11}
    cond, latest = di.VehicleUpdatesDataIngestion._check_duplicate_entity(newer, latest)
    assert cond is True


def test_trip_update_duplicate_detection():
    fields = cons.TripTableIngestionFields
    latest = {}
    base = {
        fields.trip_id: "trip1",
        fields.stop_id: "stop1",
        fields.arrival_delay: 0,
        fields.arrival_time: 1,
        fields.arrival_uncertainty: 0,
        fields.departure_delay: 0,
        fields.departure_time: 2,
        fields.departure_uncertainty: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is True

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(base, latest)
    assert cond is False

    changed = dict(base)
    changed[fields.arrival_delay] = 5
    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(changed, latest)
    assert cond is True


def test_trip_update_missing_identifiers():
    fields = cons.TripTableIngestionFields
    latest = {}
    entity = {
        fields.trip_id: None,
        fields.stop_id: None,
        fields.arrival_delay: 0,
        fields.arrival_time: 1,
        fields.arrival_uncertainty: 0,
        fields.departure_delay: 0,
        fields.departure_time: 2,
        fields.departure_uncertainty: 0,
    }

    cond, latest = di.TripUpdatesDataIngestion._check_duplicate_entity(entity, latest)
    assert cond is False
