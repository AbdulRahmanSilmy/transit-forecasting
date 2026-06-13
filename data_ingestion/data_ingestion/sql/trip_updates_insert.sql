INSERT INTO {TRIP_UPDATE_TABLE} (
    {TripTableIngestionFields.feed_timestamp},
    {TripTableIngestionFields.trip_id},
    {TripTableIngestionFields.trip_schedule_relationship},
    {TripTableIngestionFields.trip_route_id},
    {TripTableIngestionFields.trip_direction_id},
    {TripTableIngestionFields.stop_sequence},
    {TripTableIngestionFields.stop_id},
    {TripTableIngestionFields.schedule_relationship},
    {TripTableIngestionFields.arrival_delay},
    {TripTableIngestionFields.arrival_time},
    {TripTableIngestionFields.arrival_uncertainty},
    {TripTableIngestionFields.departure_delay},
    {TripTableIngestionFields.departure_time},
    {TripTableIngestionFields.departure_uncertainty},
    {TripTableIngestionFields.trip_start_timestamp}
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
