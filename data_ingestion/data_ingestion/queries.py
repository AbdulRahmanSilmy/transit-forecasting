"""
SQL queries for creating table and inserting data into the database.

TODO:
- Move these queries to actual sql files and load them as needed.
- check if docstring can support f-strings for table names.
"""
VEHICLE_UPDATE_TABLE = "TRANSIT_VEHICLE_TABLE"
TRIP_UPDATE_TABLE = "TRANSIT_TRIP_TABLE"

VEHICLE_UPDATES_CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS TRANSIT_VEHICLE_TABLE (
    id INT AUTO_INCREMENT PRIMARY KEY,
    feed_timestamp TIMESTAMP,
    entity_id INT,
    trip_id VARCHAR(50),
    trip_schedule_relationship INT,
    trip_route_id VARCHAR(20),
    trip_direction_id INT,
    position_latitude DOUBLE,
    position_longitude DOUBLE,
    position_bearing DOUBLE,
    position_odometer DOUBLE,
    position_speed DOUBLE,
    current_stop_sequence INT,
    current_status INT,
    timestamp TIMESTAMP,
    congestion_level INT,
    stop_id VARCHAR(50),
    vehicle_id VARCHAR(50),
    vehicle_label VARCHAR(50),
    trip_start_timestamp TIMESTAMP,
    read_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
VEHICLE_UPDATES_INSERT_QUERY = """
INSERT INTO TRANSIT_VEHICLE_TABLE (
    feed_timestamp, entity_id, trip_id, trip_schedule_relationship,
    trip_route_id, trip_direction_id, position_latitude, position_longitude,
    position_bearing, position_odometer, position_speed, current_stop_sequence,
    current_status, timestamp, congestion_level, stop_id, vehicle_id,
    vehicle_label, trip_start_timestamp
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

TRIP_UPDATES_CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS TRANSIT_TRIP_TABLE (
    id INT AUTO_INCREMENT PRIMARY KEY,
    feed_timestamp TIMESTAMP,
    trip_id VARCHAR(50),
    trip_schedule_relationship INT,
    trip_route_id VARCHAR(20),
    trip_direction_id INT,
    stop_sequence INT,
    stop_id VARCHAR(50),
    schedule_relationship INT,
    arrival_delay INT,
    arrival_time TIMESTAMP,
    arrival_uncertainty INT,
    departure_delay INT,
    departure_time TIMESTAMP,
    departure_uncertainty INT,
    trip_start_timestamp TIMESTAMP
);
"""
TRIP_UPDATES_INSERT_QUERY = """
INSERT INTO TRANSIT_TRIP_TABLE (
    feed_timestamp,
    trip_id,
    trip_schedule_relationship,
    trip_route_id,
    trip_direction_id,
    stop_sequence,
    stop_id,
    schedule_relationship,
    arrival_delay,
    arrival_time,
    arrival_uncertainty,
    departure_delay,
    departure_time,
    departure_uncertainty,
    trip_start_timestamp
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""