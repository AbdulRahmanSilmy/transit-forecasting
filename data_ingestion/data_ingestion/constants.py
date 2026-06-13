"""Shared constants for configuration, schema, and GTFS feed fields."""

from dataclasses import dataclass

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


class _FieldGroup:
    """Base helper for field-group dataclasses."""

    def __getattr__(self, name: str) -> str:
        """Resolve derived uppercase ``*_KEY`` accessors."""

        if name.endswith("_KEY"):
            base_name = name[:-4]
            if base_name.isupper() and base_name in self.__dataclass_fields__:
                value = object.__getattribute__(self, base_name)
                return value.rsplit(".", maxsplit=1)[-1]

        raise AttributeError(
            f"{self.__class__.__name__!s} object has no attribute {name!r}"
        )


@dataclass(frozen=True)
class _TripSharedFields(_FieldGroup):
    """Field names shared by raw and ingestion trip update mappings."""

    FEED_TIMESTAMP: str
    TRIP_ID: str
    TRIP_START_TIME: str
    TRIP_START_DATE: str
    TRIP_SCHEDULE_RELATIONSHIP: str
    TRIP_ROUTE_ID: str
    TRIP_DIRECTION_ID: str
    STOP_SEQUENCE: str
    STOP_ID: str
    SCHEDULE_RELATIONSHIP: str
    ARRIVAL_DELAY: str
    ARRIVAL_TIME: str
    ARRIVAL_UNCERTAINTY: str
    DEPARTURE_DELAY: str
    DEPARTURE_TIME: str
    DEPARTURE_UNCERTAINTY: str


@dataclass(frozen=True)
class _TripRawFields(_TripSharedFields):
    """Raw GTFS feed field names for trip updates."""


@dataclass(frozen=True)
class _TripIngestionFields(_TripSharedFields):
    """Flattened GTFS feed field names used in ingestion for trip updates."""

    TRIP_START_TIMESTAMP: str


@dataclass(frozen=True)
class _VehicleSharedFields(_FieldGroup):
    """Field names shared by raw and ingestion vehicle update mappings."""

    FEED_TIMESTAMP: str
    ENTITY_ID: str
    TRIP_ID: str
    TRIP_START_TIME: str
    TRIP_START_DATE: str
    TRIP_SCHEDULE_RELATIONSHIP: str
    TRIP_ROUTE_ID: str
    TRIP_DIRECTION_ID: str
    POSITION_LATITUDE: str
    POSITION_LONGITUDE: str
    POSITION_BEARING: str
    POSITION_ODOMETER: str
    POSITION_SPEED: str
    CURRENT_STOP_SEQUENCE: str
    CURRENT_STATUS: str
    TIMESTAMP: str
    CONGESTION_LEVEL: str
    STOP_ID: str
    VEHICLE_ID: str
    VEHICLE_LABEL: str


@dataclass(frozen=True)
class _VehicleRawFields(_VehicleSharedFields):
    """Raw GTFS feed field names for vehicle updates."""


@dataclass(frozen=True)
class _VehicleIngestionFields(_VehicleSharedFields):
    """Flattened GTFS feed field names used in ingestion for vehicle updates."""

    TRIP_START_TIMESTAMP: str


def _uppercase_field_names(field_group) -> set[str]:
    """Return uppercase attribute names defined on a field-group object."""

    return {name for name in vars(field_group) if name.isupper()}


def _validate_ingestion_fields(raw_fields, ingestion_fields) -> None:
    """Ensure every raw field name has a matching ingestion field name."""

    missing_fields = sorted(
        _uppercase_field_names(raw_fields) - _uppercase_field_names(ingestion_fields)
    )
    if missing_fields:
        raise ValueError(
            "Missing ingestion field definitions for raw fields: "
            + ", ".join(missing_fields)
        )


TripTableRawFields = _TripRawFields(
    FEED_TIMESTAMP="header.timestamp",
    TRIP_ID="entity.trip_update.trip.trip_id",
    TRIP_START_TIME="entity.trip_update.trip.start_time",
    TRIP_START_DATE="entity.trip_update.trip.start_date",
    TRIP_SCHEDULE_RELATIONSHIP="entity.trip_update.trip.schedule_relationship",
    TRIP_ROUTE_ID="entity.trip_update.trip.route_id",
    TRIP_DIRECTION_ID="entity.trip_update.trip.direction_id",
    STOP_SEQUENCE="stop_time_update.stop_sequence",
    STOP_ID="stop_time_update.stop_id",
    SCHEDULE_RELATIONSHIP="stop_time_update.schedule_relationship",
    ARRIVAL_DELAY="stop_time_update.arrival.delay",
    ARRIVAL_TIME="stop_time_update.arrival.time",
    ARRIVAL_UNCERTAINTY="stop_time_update.arrival.uncertainty",
    DEPARTURE_DELAY="stop_time_update.departure.delay",
    DEPARTURE_TIME="stop_time_update.departure.time",
    DEPARTURE_UNCERTAINTY="stop_time_update.departure.uncertainty",
)

TripTableIngestionFields = _TripIngestionFields(
    FEED_TIMESTAMP="feed_timestamp",
    TRIP_ID="trip_id",
    TRIP_START_TIME="trip_start_time",
    TRIP_START_DATE="trip_start_date",
    TRIP_SCHEDULE_RELATIONSHIP="trip_schedule_relationship",
    TRIP_ROUTE_ID="trip_route_id",
    TRIP_DIRECTION_ID="trip_direction_id",
    STOP_SEQUENCE="stop_sequence",
    STOP_ID="stop_id",
    SCHEDULE_RELATIONSHIP="schedule_relationship",
    ARRIVAL_DELAY="arrival_delay",
    ARRIVAL_TIME="arrival_time",
    ARRIVAL_UNCERTAINTY="arrival_uncertainty",
    DEPARTURE_DELAY="departure_delay",
    DEPARTURE_TIME="departure_time",
    DEPARTURE_UNCERTAINTY="departure_uncertainty",
    TRIP_START_TIMESTAMP="trip_start_timestamp",
)

VehicleTableRawFields = _VehicleRawFields(
    FEED_TIMESTAMP="header.timestamp",
    ENTITY_ID="entity.id",
    TRIP_ID="entity.vehicle.trip.trip_id",
    TRIP_START_TIME="entity.vehicle.trip.start_time",
    TRIP_START_DATE="entity.vehicle.trip.start_date",
    TRIP_SCHEDULE_RELATIONSHIP="entity.vehicle.trip.schedule_relationship",
    TRIP_ROUTE_ID="entity.vehicle.trip.route_id",
    TRIP_DIRECTION_ID="entity.vehicle.trip.direction_id",
    POSITION_LATITUDE="entity.vehicle.position.latitude",
    POSITION_LONGITUDE="entity.vehicle.position.longitude",
    POSITION_BEARING="entity.vehicle.position.bearing",
    POSITION_ODOMETER="entity.vehicle.position.odometer",
    POSITION_SPEED="entity.vehicle.position.speed",
    CURRENT_STOP_SEQUENCE="entity.vehicle.current_stop_sequence",
    CURRENT_STATUS="entity.vehicle.current_status",
    TIMESTAMP="entity.vehicle.timestamp",
    CONGESTION_LEVEL="entity.vehicle.congestion_level",
    STOP_ID="entity.vehicle.stop_id",
    VEHICLE_ID="entity.vehicle.vehicle.id",
    VEHICLE_LABEL="entity.vehicle.vehicle.label",
)

VehicleTableIngestionFields = _VehicleIngestionFields(
    FEED_TIMESTAMP="feed_timestamp",
    ENTITY_ID="entity_id",
    TRIP_ID="trip_id",
    TRIP_START_TIME="trip_start_time",
    TRIP_START_DATE="trip_start_date",
    TRIP_SCHEDULE_RELATIONSHIP="trip_schedule_relationship",
    TRIP_ROUTE_ID="trip_route_id",
    TRIP_DIRECTION_ID="trip_direction_id",
    POSITION_LATITUDE="position_latitude",
    POSITION_LONGITUDE="position_longitude",
    POSITION_BEARING="position_bearing",
    POSITION_ODOMETER="position_odometer",
    POSITION_SPEED="position_speed",
    CURRENT_STOP_SEQUENCE="current_stop_sequence",
    CURRENT_STATUS="current_status",
    TIMESTAMP="timestamp",
    CONGESTION_LEVEL="congestion_level",
    STOP_ID="stop_id",
    VEHICLE_ID="vehicle_id",
    VEHICLE_LABEL="vehicle_label",
    TRIP_START_TIMESTAMP="trip_start_timestamp",
)

_validate_ingestion_fields(TripTableRawFields, TripTableIngestionFields)
_validate_ingestion_fields(VehicleTableRawFields, VehicleTableIngestionFields)


ID_KEY = "id"
READ_TIMESTAMP_KEY = "read_timestamp"

# Raw GTFS feed object keys shared by both update types
HEADER_KEY = "header"
ENTITY_KEY = "entity"

# Raw GTFS feed object keys used by vehicle updates
VEHICLE_KEY = "vehicle"
TRIP_KEY = "trip"
POSITION_KEY = "position"

# Raw GTFS feed object keys used by trip updates
TRIP_UPDATE_KEY = "trip_update"
STOP_TIME_UPDATE_KEY = "stop_time_update"
ARRIVAL_KEY = "arrival"
DEPARTURE_KEY = "departure"
DELAY_KEY = "delay"
TIME_KEY = "time"
UNCERTAINTY_KEY = "uncertainty"

# Shared ingestion transform settings
TRIP_START_TIMESTAMP_FORMAT = "%Y%m%d %H:%M:%S"
UNIX_TIMESTAMP_UNIT = "s"
