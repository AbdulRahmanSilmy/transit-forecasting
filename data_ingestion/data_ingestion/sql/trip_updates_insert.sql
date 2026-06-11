INSERT INTO {TRIP_UPDATE_TABLE} (
    {TripTableIngestionFields.FEED_TIMESTAMP},
    {TripTableIngestionFields.TRIP_ID},
    {TripTableIngestionFields.TRIP_SCHEDULE_RELATIONSHIP},
    {TripTableIngestionFields.TRIP_ROUTE_ID},
    {TripTableIngestionFields.TRIP_DIRECTION_ID},
    {TripTableIngestionFields.STOP_SEQUENCE},
    {TripTableIngestionFields.STOP_ID},
    {TripTableIngestionFields.SCHEDULE_RELATIONSHIP},
    {TripTableIngestionFields.ARRIVAL_DELAY},
    {TripTableIngestionFields.ARRIVAL_TIME},
    {TripTableIngestionFields.ARRIVAL_UNCERTAINTY},
    {TripTableIngestionFields.DEPARTURE_DELAY},
    {TripTableIngestionFields.DEPARTURE_TIME},
    {TripTableIngestionFields.DEPARTURE_UNCERTAINTY},
    {TripTableIngestionFields.TRIP_START_TIMESTAMP}
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
