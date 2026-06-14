"""Shared constants for configuration, schema, and GTFS feed fields."""

from dataclasses import dataclass
from dataclasses import fields as dataclass_fields

# expose for use by other modules in the package
__all__ = ["dataclass_fields"]

# Configuration
CONFIG_PATH = "config.yaml"

CONNECTION_PARAMS_KEY = "connection_params"
CP_HOST_KEY = "host"
CP_USER_KEY = "user"
CP_PASSWORD_KEY = "password"
CP_DATABASE_KEY = "database"
CP_PORT_KEY = "port"
REQUIRED_CONNECTION_KEYS = [
    CP_HOST_KEY,
    CP_USER_KEY,
    CP_PASSWORD_KEY,
    CP_DATABASE_KEY,
    CP_PORT_KEY,
]

DATA_INGESTION_PARAMS_KEY = "data_ingestion_params"
DIP_INGESTION_URL_KEY = "ingestion_url"
DIP_FETCH_DELAY_SECONDS_KEY = "fetch_delay_seconds"
DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY = "connection_retry_delay_seconds"
REQUIRED_INGESTION_KEYS = [
    DIP_INGESTION_URL_KEY,
    DIP_FETCH_DELAY_SECONDS_KEY,
    DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY,
]

LOGGING_FILE_KEY = "logging_file"
VEHICLE_UPDATES_KEY = "vehicle_updates"
TRIP_UPDATES_KEY = "trip_updates"

# Table and schema names
VEHICLE_UPDATE_TABLE = "TRANSIT_VEHICLE_TABLE"
TRIP_UPDATE_TABLE = "TRANSIT_TRIP_TABLE"

# SQL metadata column names used by create-table templates.
ID_KEY = "id"
READ_TIMESTAMP_KEY = "read_timestamp"

# Shared ingestion transform settings
TRIP_START_TIMESTAMP_FORMAT = "%Y%m%d %H:%M:%S"
UNIX_TIMESTAMP_UNIT = "s"


@dataclass(frozen=True)
class _TripSharedFields:
    """Field names shared by raw and ingestion trip update mappings."""

    feed_timestamp: str
    trip_id: str
    trip_start_time: str
    trip_start_date: str
    trip_schedule_relationship: str
    trip_route_id: str
    trip_direction_id: str
    stop_sequence: str
    stop_id: str
    schedule_relationship: str
    arrival_delay: str
    arrival_time: str
    arrival_uncertainty: str
    departure_delay: str
    departure_time: str
    departure_uncertainty: str


@dataclass(frozen=True)
class _TripRawFields(_TripSharedFields):
    """Raw GTFS feed field names for trip updates."""


@dataclass(frozen=True)
class _TripIngestionFields(_TripSharedFields):
    """Flattened GTFS feed field names used in ingestion for trip updates."""

    trip_start_timestamp: str


@dataclass(frozen=True)
class _VehicleSharedFields:
    """Field names shared by raw and ingestion vehicle update mappings."""

    feed_timestamp: str
    entity_id: str
    trip_id: str
    trip_start_time: str
    trip_start_date: str
    trip_schedule_relationship: str
    trip_route_id: str
    trip_direction_id: str
    position_latitude: str
    position_longitude: str
    position_bearing: str
    position_odometer: str
    position_speed: str
    current_stop_sequence: str
    current_status: str
    timestamp: str
    congestion_level: str
    stop_id: str
    vehicle_id: str
    vehicle_label: str


@dataclass(frozen=True)
class _VehicleRawFields(_VehicleSharedFields):
    """Raw GTFS feed field names for vehicle updates."""


@dataclass(frozen=True)
class _VehicleIngestionFields(_VehicleSharedFields):
    """Flattened GTFS feed field names used in ingestion for vehicle updates."""

    trip_start_timestamp: str


def _validate_ingestion_fields(raw_fields, ingestion_fields) -> None:
    """Ensure every raw field name has a matching ingestion field name."""

    raw_names = {f.name for f in dataclass_fields(raw_fields)}
    ingestion_names = {f.name for f in dataclass_fields(ingestion_fields)}
    missing_fields = sorted(raw_names - ingestion_names)
    if missing_fields:
        raise ValueError(
            "Missing ingestion field definitions for raw fields: "
            + ", ".join(missing_fields)
        )


def _build_ingestion_fields(raw_fields, ingestion_cls, extra_fields=None):
    """Build ingestion mappings from raw field names with optional extras."""

    mapping = {f.name: f.name for f in dataclass_fields(raw_fields)}
    if extra_fields:
        mapping.update(extra_fields)

    return ingestion_cls(**mapping)


TripTableRawFields = _TripRawFields(
    feed_timestamp="header.timestamp",
    trip_id="entity.trip_update.trip.trip_id",
    trip_start_time="entity.trip_update.trip.start_time",
    trip_start_date="entity.trip_update.trip.start_date",
    trip_schedule_relationship="entity.trip_update.trip.schedule_relationship",
    trip_route_id="entity.trip_update.trip.route_id",
    trip_direction_id="entity.trip_update.trip.direction_id",
    stop_sequence="entity.trip_update.stop_time_update.stop_sequence",
    stop_id="entity.trip_update.stop_time_update.stop_id",
    schedule_relationship="entity.trip_update.stop_time_update.schedule_relationship",
    arrival_delay="entity.trip_update.stop_time_update.arrival.delay",
    arrival_time="entity.trip_update.stop_time_update.arrival.time",
    arrival_uncertainty="entity.trip_update.stop_time_update.arrival.uncertainty",
    departure_delay="entity.trip_update.stop_time_update.departure.delay",
    departure_time="entity.trip_update.stop_time_update.departure.time",
    departure_uncertainty="entity.trip_update.stop_time_update.departure.uncertainty",
)

TripTableIngestionFields = _build_ingestion_fields(
    raw_fields=TripTableRawFields,
    ingestion_cls=_TripIngestionFields,
    extra_fields={"trip_start_timestamp": "trip_start_timestamp"},
)

VehicleTableRawFields = _VehicleRawFields(
    feed_timestamp="header.timestamp",
    entity_id="entity.id",
    trip_id="entity.vehicle.trip.trip_id",
    trip_start_time="entity.vehicle.trip.start_time",
    trip_start_date="entity.vehicle.trip.start_date",
    trip_schedule_relationship="entity.vehicle.trip.schedule_relationship",
    trip_route_id="entity.vehicle.trip.route_id",
    trip_direction_id="entity.vehicle.trip.direction_id",
    position_latitude="entity.vehicle.position.latitude",
    position_longitude="entity.vehicle.position.longitude",
    position_bearing="entity.vehicle.position.bearing",
    position_odometer="entity.vehicle.position.odometer",
    position_speed="entity.vehicle.position.speed",
    current_stop_sequence="entity.vehicle.current_stop_sequence",
    current_status="entity.vehicle.current_status",
    timestamp="entity.vehicle.timestamp",
    congestion_level="entity.vehicle.congestion_level",
    stop_id="entity.vehicle.stop_id",
    vehicle_id="entity.vehicle.vehicle.id",
    vehicle_label="entity.vehicle.vehicle.label",
)

VehicleTableIngestionFields = _build_ingestion_fields(
    raw_fields=VehicleTableRawFields,
    ingestion_cls=_VehicleIngestionFields,
    extra_fields={"trip_start_timestamp": "trip_start_timestamp"},
)

_validate_ingestion_fields(TripTableRawFields, TripTableIngestionFields)
_validate_ingestion_fields(VehicleTableRawFields, VehicleTableIngestionFields)
