"""Configuration loading and validation helpers."""

from __future__ import annotations

from typing import Any, Dict

import yaml

from .constants import (
    CONFIG_PATH,
    CONNECTION_PARAMS_KEY,
    DATA_INGESTION_PARAMS_KEY,
    LOGGING_FILE_KEY,
    REQUIRED_CONNECTION_KEYS,
    REQUIRED_INGESTION_KEYS,
    TRIP_UPDATES_KEY,
    VEHICLE_UPDATES_KEY,
)


class ConfigError(ValueError):
    """Raised when configuration validation fails."""


def _validate_required_keys(
    section: Dict[str, Any], required_keys: list[str], path: str
) -> None:
    """Validate that required keys are present in a section.

    Parameters
    ----------
    section : dict[str, Any]
        Section to validate.
    required_keys : list[str]
        Keys that must be present.
    path : str
        Configuration path used in error messages.

    Raises
    ------
    ConfigError
        If any required keys are missing.
    """
    missing = [key for key in required_keys if key not in section]
    if missing:
        missing_list = ", ".join(missing)
        raise ConfigError(f"Missing required keys at '{path}': {missing_list}")


def _ensure_dict(value: Any, path: str) -> Dict[str, Any]:
    """Ensure a value is a mapping.

    Parameters
    ----------
    value : Any
        Value to validate.
    path : str
        Configuration path used in error messages.

    Returns
    -------
    dict[str, Any]
        The validated mapping.

    Raises
    ------
    ConfigError
        If the value is not a mapping.
    """
    if not isinstance(value, dict):
        raise ConfigError(f"Expected a mapping at '{path}'")
    return value


def validate_config(config: Dict[str, Any]) -> None:
    """Validate configuration structure and required fields.

    Parameters
    ----------
    config : dict[str, Any]
        Parsed configuration mapping.

    Raises
    ------
    ConfigError
        If required sections or keys are missing.
    """
    _ensure_dict(config, "root")

    connection_params = _ensure_dict(
        config.get(CONNECTION_PARAMS_KEY), CONNECTION_PARAMS_KEY
    )
    _validate_required_keys(
        connection_params, REQUIRED_CONNECTION_KEYS, CONNECTION_PARAMS_KEY
    )

    vehicle_section = _ensure_dict(config.get(VEHICLE_UPDATES_KEY), VEHICLE_UPDATES_KEY)
    vehicle_params = _ensure_dict(
        vehicle_section.get(DATA_INGESTION_PARAMS_KEY),
        f"{VEHICLE_UPDATES_KEY}.{DATA_INGESTION_PARAMS_KEY}",
    )
    _validate_required_keys(
        vehicle_params,
        REQUIRED_INGESTION_KEYS,
        f"{VEHICLE_UPDATES_KEY}.{DATA_INGESTION_PARAMS_KEY}",
    )

    trip_section = _ensure_dict(config.get(TRIP_UPDATES_KEY), TRIP_UPDATES_KEY)
    trip_params = _ensure_dict(
        trip_section.get(DATA_INGESTION_PARAMS_KEY),
        f"{TRIP_UPDATES_KEY}.{DATA_INGESTION_PARAMS_KEY}",
    )
    _validate_required_keys(
        trip_params,
        REQUIRED_INGESTION_KEYS,
        f"{TRIP_UPDATES_KEY}.{DATA_INGESTION_PARAMS_KEY}",
    )

    logging_file = config.get(LOGGING_FILE_KEY)
    if not logging_file:
        raise ConfigError(f"Missing required key at 'root': {LOGGING_FILE_KEY}")


def load_config(config_path: str = CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from a YAML file and validate required fields.

    Parameters
    ----------
    config_path : str, optional
        Path to the YAML configuration file.

    Returns
    -------
    dict[str, Any]
        Validated configuration mapping.

    Raises
    ------
    ConfigError
        If the file is empty or missing required keys.
    """
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if config is None:
        raise ConfigError("Configuration file is empty")

    validate_config(config)
    return config
