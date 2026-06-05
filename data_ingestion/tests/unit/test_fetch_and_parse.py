from __future__ import annotations

import requests
from google.transit import gtfs_realtime_pb2

from data_ingestion import data_ingestion as di

# pylint: disable=no-member,missing-docstring,protected-access


def test_fetch_gtfs_data_success(monkeypatch):
    class FakeResponse:
        content = b"payload"

        @staticmethod
        def raise_for_status() -> None:
            return None

    def fake_get(url, timeout):
        assert url == "http://example.com/feed.pb"
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    data = di.fetch_gtfs_data("http://example.com/feed.pb")
    assert data == b"payload"


def test_fetch_gtfs_data_timeout(monkeypatch):
    def fake_get(url, timeout):
        raise requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(requests, "get", fake_get)
    data = di.fetch_gtfs_data("http://example.com/feed.pb")
    assert data is None


def test_parse_gtfs_data_round_trip():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 123
    entity = feed.entity.add()
    entity.id = "1"
    entity.vehicle.timestamp = 123

    data = feed.SerializeToString()
    parsed = di.parse_gtfs_data(data)

    assert parsed.header.timestamp == 123
    assert len(parsed.entity) == 1
