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
    from dataclasses import fields as dc_fields

    trip_raw_names = {f.name for f in dc_fields(cons.TripTableRawFields)}
    trip_ingestion_names = {f.name for f in dc_fields(cons.TripTableIngestionFields)}
    vehicle_raw_names = {f.name for f in dc_fields(cons.VehicleTableRawFields)}
    vehicle_ingestion_names = {
        f.name for f in dc_fields(cons.VehicleTableIngestionFields)
    }

    assert trip_raw_names <= trip_ingestion_names
    assert vehicle_raw_names <= vehicle_ingestion_names


def test_field_groups_expose_field_values_as_snake_case():
    assert cons.TripTableIngestionFields.trip_id == "trip_id"
    assert cons.TripTableIngestionFields.feed_timestamp == "feed_timestamp"
    assert cons.VehicleTableIngestionFields.vehicle_label == "vehicle_label"
    assert cons.TripTableRawFields.feed_timestamp == "header.timestamp"


def test_field_groups_reject_unknown_attributes():
    try:
        cons.TripTableRawFields.nonexistent_field
    except AttributeError:
        pass
    else:
        raise AssertionError("Unknown attributes should raise AttributeError")
