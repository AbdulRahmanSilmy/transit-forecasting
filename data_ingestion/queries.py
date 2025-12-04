"""
SQL queries for creating table and inserting data into the database.

TODO:
- Move these queries to actual sql files and load them as needed.
"""
CREATE_TABLE_QUERY = """
CREATE TABLE IF NOT EXISTS TRANSIT_DATA (
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
INSERT_QUERY = """
INSERT INTO TRANSIT_DATA (
    feed_timestamp, entity_id, trip_id, trip_schedule_relationship,
    trip_route_id, trip_direction_id, position_latitude, position_longitude,
    position_bearing, position_odometer, position_speed, current_stop_sequence,
    current_status, timestamp, congestion_level, stop_id, vehicle_id,
    vehicle_label, trip_start_timestamp
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""