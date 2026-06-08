"""Shared constants for configuration, schema, and GTFS feed fields."""

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

ID_KEY = "id"
READ_TIMESTAMP_KEY = "read_timestamp"

# Raw GTFS feed object keys shared by both update types
HEADER_KEY = "header"
ENTITY_KEY = "entity"

# Raw GTFS feed object keys used by vehicle updates
VEHICLE_KEY = "vehicle"
TRIP_KEY = "trip"
POSITION_KEY = "position"
LABEL_KEY = "label"
START_TIME_KEY = "start_time"
START_DATE_KEY = "start_date"
ROUTE_ID_KEY = "route_id"
DIRECTION_ID_KEY = "direction_id"
LATITUDE_KEY = "latitude"
LONGITUDE_KEY = "longitude"
BEARING_KEY = "bearing"
ODOMETER_KEY = "odometer"
SPEED_KEY = "speed"
CURRENT_STOP_SEQUENCE_KEY = "current_stop_sequence"
CURRENT_STATUS_KEY = "current_status"
TIMESTAMP_KEY = "timestamp"
CONGESTION_LEVEL_KEY = "congestion_level"
STOP_ID_KEY = "stop_id"

# Raw GTFS feed object keys used by trip updates
TRIP_UPDATE_KEY = "trip_update"
STOP_TIME_UPDATE_KEY = "stop_time_update"
ARRIVAL_KEY = "arrival"
DEPARTURE_KEY = "departure"
DELAY_KEY = "delay"
TIME_KEY = "time"
UNCERTAINTY_KEY = "uncertainty"
SCHEDULE_RELATIONSHIP_KEY = "schedule_relationship"

# Flattened ingestion column names shared by both update types
FEED_TIMESTAMP_KEY = "feed_timestamp"
TRIP_ID_KEY = "trip_id"
TRIP_START_TIME_KEY = "trip_start_time"
TRIP_START_DATE_KEY = "trip_start_date"
TRIP_START_TIMESTAMP_KEY = "trip_start_timestamp"
TRIP_SCHEDULE_RELATIONSHIP_KEY = "trip_schedule_relationship"
TRIP_ROUTE_ID_KEY = "trip_route_id"
TRIP_DIRECTION_ID_KEY = "trip_direction_id"

# Flattened ingestion column names used by vehicle updates
ENTITY_ID_KEY = "entity_id"
POSITION_LATITUDE_KEY = "position_latitude"
POSITION_LONGITUDE_KEY = "position_longitude"
POSITION_BEARING_KEY = "position_bearing"
POSITION_ODOMETER_KEY = "position_odometer"
POSITION_SPEED_KEY = "position_speed"
VEHICLE_ID_KEY = "vehicle_id"
VEHICLE_LABEL_KEY = "vehicle_label"

# Flattened ingestion column names used by trip updates
STOP_SEQUENCE_KEY = "stop_sequence"
ARRIVAL_DELAY_KEY = "arrival_delay"
ARRIVAL_TIME_KEY = "arrival_time"
ARRIVAL_UNCERTAINTY_KEY = "arrival_uncertainty"
DEPARTURE_DELAY_KEY = "departure_delay"
DEPARTURE_TIME_KEY = "departure_time"
DEPARTURE_UNCERTAINTY_KEY = "departure_uncertainty"

# Shared ingestion transform settings
TRIP_START_TIMESTAMP_FORMAT = "%Y%m%d %H:%M:%S"
UNIX_TIMESTAMP_UNIT = "s"
