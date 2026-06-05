from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_ingestion import config
from data_ingestion.constants import (
    CONNECTION_PARAMS_KEY,
    CP_DATABASE_KEY,
    CP_HOST_KEY,
    CP_PASSWORD_KEY,
    CP_PORT_KEY,
    CP_USER_KEY,
    DATA_INGESTION_PARAMS_KEY,
    DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY,
    DIP_FETCH_DELAY_SECONDS_KEY,
    DIP_INGESTION_URL_KEY,
    LOGGING_FILE_KEY,
    TRIP_UPDATES_KEY,
    VEHICLE_UPDATES_KEY,
)


def _base_config() -> dict:
    return {
        CONNECTION_PARAMS_KEY: {
            CP_HOST_KEY: "localhost",
            CP_USER_KEY: "user",
            CP_PASSWORD_KEY: "pass",
            CP_DATABASE_KEY: "db",
            CP_PORT_KEY: 3306,
        },
        VEHICLE_UPDATES_KEY: {
            DATA_INGESTION_PARAMS_KEY: {
                DIP_INGESTION_URL_KEY: "http://example.com/vehicle.pb",
                DIP_FETCH_DELAY_SECONDS_KEY: 5,
                DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 10,
            }
        },
        TRIP_UPDATES_KEY: {
            DATA_INGESTION_PARAMS_KEY: {
                DIP_INGESTION_URL_KEY: "http://example.com/trip.pb",
                DIP_FETCH_DELAY_SECONDS_KEY: 5,
                DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY: 10,
            }
        },
        LOGGING_FILE_KEY: "data_ingestion.log",
    }


def test_validate_config_success():
    cfg = _base_config()
    config.validate_config(cfg)


def test_validate_config_missing_connection_key():
    cfg = _base_config()
    del cfg[CONNECTION_PARAMS_KEY][CP_HOST_KEY]
    with pytest.raises(config.ConfigError, match="Missing required keys"):
        config.validate_config(cfg)


def test_validate_config_missing_section():
    cfg = _base_config()
    del cfg[TRIP_UPDATES_KEY]
    with pytest.raises(config.ConfigError, match="Expected a mapping"):
        config.validate_config(cfg)


def test_load_config_success(tmp_path: Path):
    cfg = _base_config()
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    loaded = config.load_config(str(path))
    assert loaded[LOGGING_FILE_KEY] == "data_ingestion.log"


def test_load_config_empty_file(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="Configuration file is empty"):
        config.load_config(str(path))
