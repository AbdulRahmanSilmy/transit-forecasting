INSERT INTO {VEHICLE_UPDATE_TABLE} (
    {VehicleTableIngestionFields.feed_timestamp}, {VehicleTableIngestionFields.entity_id}, {VehicleTableIngestionFields.trip_id}, {VehicleTableIngestionFields.trip_schedule_relationship},
    {VehicleTableIngestionFields.trip_route_id}, {VehicleTableIngestionFields.trip_direction_id}, {VehicleTableIngestionFields.position_latitude}, {VehicleTableIngestionFields.position_longitude},
    {VehicleTableIngestionFields.position_bearing}, {VehicleTableIngestionFields.position_odometer}, {VehicleTableIngestionFields.position_speed}, {VehicleTableIngestionFields.current_stop_sequence},
    {VehicleTableIngestionFields.current_status}, {VehicleTableIngestionFields.timestamp}, {VehicleTableIngestionFields.congestion_level}, {VehicleTableIngestionFields.stop_id}, {VehicleTableIngestionFields.vehicle_id},
    {VehicleTableIngestionFields.vehicle_label}, {VehicleTableIngestionFields.trip_start_timestamp}
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
