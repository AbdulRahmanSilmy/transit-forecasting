INSERT INTO {VEHICLE_UPDATE_TABLE} (
    {VehicleTableIngestionFields.FEED_TIMESTAMP}, {VehicleTableIngestionFields.ENTITY_ID}, {VehicleTableIngestionFields.TRIP_ID}, {VehicleTableIngestionFields.TRIP_SCHEDULE_RELATIONSHIP},
    {VehicleTableIngestionFields.TRIP_ROUTE_ID}, {VehicleTableIngestionFields.TRIP_DIRECTION_ID}, {VehicleTableIngestionFields.POSITION_LATITUDE}, {VehicleTableIngestionFields.POSITION_LONGITUDE},
    {VehicleTableIngestionFields.POSITION_BEARING}, {VehicleTableIngestionFields.POSITION_ODOMETER}, {VehicleTableIngestionFields.POSITION_SPEED}, {VehicleTableIngestionFields.CURRENT_STOP_SEQUENCE},
    {VehicleTableIngestionFields.CURRENT_STATUS}, {VehicleTableIngestionFields.TIMESTAMP}, {VehicleTableIngestionFields.CONGESTION_LEVEL}, {VehicleTableIngestionFields.STOP_ID}, {VehicleTableIngestionFields.VEHICLE_ID},
    {VehicleTableIngestionFields.VEHICLE_LABEL}, {VehicleTableIngestionFields.TRIP_START_TIMESTAMP}
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
