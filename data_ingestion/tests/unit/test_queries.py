from __future__ import annotations

from data_ingestion import constants as cons
from data_ingestion import queries

# pylint: disable=no-member,missing-docstring,protected-access


def test_queries_are_formatted():
    assert "{" not in queries.VEHICLE_UPDATES_CREATE_TABLE_QUERY
    assert "{" not in queries.TRIP_UPDATES_CREATE_TABLE_QUERY
    assert cons.VEHICLE_UPDATE_TABLE in queries.VEHICLE_UPDATES_CREATE_TABLE_QUERY
    assert cons.TRIP_UPDATE_TABLE in queries.TRIP_UPDATES_CREATE_TABLE_QUERY


def test_raw_field_names_are_present_in_ingestion_field_names():
    trip_raw_names = {name for name in vars(cons.TripTableRawFields) if name.isupper()}
    trip_ingestion_names = {
        name for name in vars(cons.TripTableIngestionFields) if name.isupper()
    }
    vehicle_raw_names = {
        name for name in vars(cons.VehicleTableRawFields) if name.isupper()
    }
    vehicle_ingestion_names = {
        name for name in vars(cons.VehicleTableIngestionFields) if name.isupper()
    }

    assert trip_raw_names <= trip_ingestion_names
    assert vehicle_raw_names <= vehicle_ingestion_names


def test_field_groups_expose_uppercase_key_accessors():
    assert cons.TripTableRawFields.TRIP_ID_KEY == "trip_id"
    assert cons.TripTableRawFields.FEED_TIMESTAMP_KEY == "timestamp"
    assert (
        cons.TripTableIngestionFields.TRIP_ID_KEY
        == cons.TripTableIngestionFields.TRIP_ID
    )
    assert cons.VehicleTableRawFields.VEHICLE_LABEL_KEY == "label"


def test_field_groups_reject_lowercase_key_accessors():
    try:
        cons.TripTableRawFields.trip_id_key
    except AttributeError:
        pass
    else:
        raise AssertionError("Lowercase *_key accessors should not be supported")
