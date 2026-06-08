from __future__ import annotations

from data_ingestion import constants as cons
from data_ingestion import queries

# pylint: disable=no-member,missing-docstring,protected-access


def test_queries_are_formatted():
    assert "{" not in queries.VEHICLE_UPDATES_CREATE_TABLE_QUERY
    assert "{" not in queries.TRIP_UPDATES_CREATE_TABLE_QUERY
    assert cons.VEHICLE_UPDATE_TABLE in queries.VEHICLE_UPDATES_CREATE_TABLE_QUERY
    assert cons.TRIP_UPDATE_TABLE in queries.TRIP_UPDATES_CREATE_TABLE_QUERY
