CREATE TABLE IF NOT EXISTS {TRIP_UPDATE_TABLE} (
    {ID_KEY} INT AUTO_INCREMENT PRIMARY KEY,
    {TripTableIngestionFields.feed_timestamp} TIMESTAMP,
    {TripTableIngestionFields.trip_id} VARCHAR(50),
    {TripTableIngestionFields.trip_schedule_relationship} INT,
    {TripTableIngestionFields.trip_route_id} VARCHAR(20),
    {TripTableIngestionFields.trip_direction_id} INT,
    {TripTableIngestionFields.stop_sequence} INT,
    {TripTableIngestionFields.stop_id} VARCHAR(50),
    {TripTableIngestionFields.schedule_relationship} INT,
    {TripTableIngestionFields.arrival_delay} INT,
    {TripTableIngestionFields.arrival_time} TIMESTAMP,
    {TripTableIngestionFields.arrival_uncertainty} INT,
    {TripTableIngestionFields.departure_delay} INT,
    {TripTableIngestionFields.departure_time} TIMESTAMP,
    {TripTableIngestionFields.departure_uncertainty} INT,
    {TripTableIngestionFields.trip_start_timestamp} TIMESTAMP
);
