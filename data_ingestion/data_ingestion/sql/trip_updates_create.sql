CREATE TABLE IF NOT EXISTS {TRIP_UPDATE_TABLE} (
    {ID_KEY} INT AUTO_INCREMENT PRIMARY KEY,
    {TripTableIngestionFields.FEED_TIMESTAMP} TIMESTAMP,
    {TripTableIngestionFields.TRIP_ID} VARCHAR(50),
    {TripTableIngestionFields.TRIP_SCHEDULE_RELATIONSHIP} INT,
    {TripTableIngestionFields.TRIP_ROUTE_ID} VARCHAR(20),
    {TripTableIngestionFields.TRIP_DIRECTION_ID} INT,
    {TripTableIngestionFields.STOP_SEQUENCE} INT,
    {TripTableIngestionFields.STOP_ID} VARCHAR(50),
    {TripTableIngestionFields.SCHEDULE_RELATIONSHIP} INT,
    {TripTableIngestionFields.ARRIVAL_DELAY} INT,
    {TripTableIngestionFields.ARRIVAL_TIME} TIMESTAMP,
    {TripTableIngestionFields.ARRIVAL_UNCERTAINTY} INT,
    {TripTableIngestionFields.DEPARTURE_DELAY} INT,
    {TripTableIngestionFields.DEPARTURE_TIME} TIMESTAMP,
    {TripTableIngestionFields.DEPARTURE_UNCERTAINTY} INT,
    {TripTableIngestionFields.TRIP_START_TIMESTAMP} TIMESTAMP
);
