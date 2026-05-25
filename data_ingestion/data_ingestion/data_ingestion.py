"""
INTRO
-----
Contains the data ingestion scripts for populating each table in the database 
is encapsulated within its respective data ingestion classes 

The following are currently populated tables and their respective classes:
- Vehicle Updates -> VehicleUpdatesDataIngestion
- Trip Updates -> TripUpdatesDataIngestion

"""
from abc import ABC, abstractmethod
import time
from typing import List, Tuple, Dict
import logging
import mysql.connector
import pandas as pd

from .utils import (
    fetch_gtfs_data,
    parse_gtfs_data,
    get_field
)
from .queries import (
    VEHICLE_UPDATE_TABLE,
    VEHICLE_UPDATES_CREATE_TABLE_QUERY,
    VEHICLE_UPDATES_INSERT_QUERY,
    TRIP_UPDATE_TABLE,
    TRIP_UPDATES_CREATE_TABLE_QUERY,
    TRIP_UPDATES_INSERT_QUERY
)

logger = logging.getLogger(__name__)

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
    CP_PORT_KEY
]

DATA_INGESTION_PARAMS_KEY = "data_ingestion_params"
DIP_INGESTION_URL_KEY = "ingestion_url"
DIP_FETCH_DELAY_SECONDS_KEY = "fetch_delay_seconds"
DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY = "connection_retry_delay_seconds"
REQUIRED_INGESTION_KEYS = [
    DIP_INGESTION_URL_KEY,
    DIP_FETCH_DELAY_SECONDS_KEY,
    DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY
]

LOGGING_FILE_KEY = "logging_file"


class DataIngestion(ABC):
    """
    Class to handle data ingestion from GTFS-Realtime feed to MySQL database.

    Parameters
    ----------
    connection_params : dict
        Dictionary containing MySQL connection parameters.
        Should include keys: host, user, password, database, port.

    data_ingestion_params : dict
        Dictionary containing data ingestion parameters.
        Should include keys: ingestion_url, fetch_delay_seconds, connection_retry_delay_seconds.

    Methods
    -------
    run_ingestion_loop()
        Starts the continuous data ingestion loop.
    """
    CREATE_TABLE_QUERY: str = None
    INSERT_QUERY: str = None
    TABLE_NAME: str = None

    def __init_subclass__(cls):
        super().__init_subclass__()
        if cls.CREATE_TABLE_QUERY is None:
            raise TypeError(
                f"{cls.__name__} must define 'CREATE_TABLE_QUERY'")
        if cls.INSERT_QUERY is None:
            raise TypeError(f"{cls.__name__} must define 'INSERT_QUERY'")
        if cls.TABLE_NAME is None:
            raise TypeError(f"{cls.__name__} must define 'TABLE_NAME'")

    def __init__(self, connection_params: dict, data_ingestion_params: dict):
        self.connection_params = connection_params
        self.data_ingestion_params = data_ingestion_params
        self._latest_update = {}

        self._check_parameters()
        self.fetch_delay_seconds = self.data_ingestion_params[DIP_FETCH_DELAY_SECONDS_KEY]
        self.connection_retry_delay_seconds = self.data_ingestion_params[
            DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY
        ]

        self.table_db = self.connection_params[CP_DATABASE_KEY]

    @staticmethod
    @abstractmethod
    def _get_dicts_from_feed(header, entity) -> List[dict]:
        pass

    @staticmethod
    @abstractmethod
    def _check_duplicate_entity(entity, latest_update) -> bool:
        pass

    @staticmethod
    @abstractmethod
    def _format_df_feed(df_feed: pd.DataFrame) -> pd.DataFrame:
        pass

    def _extract_feed_info(self, feed) -> List[Dict]:
        """
        Convert a FeedEntity to a dictionary, handling missing fields gracefully.

        Parameters:
        -----------
        feed: gtfs_realtime_pb2.Feed
            The Feed to convert.

        Returns
        -------
        List[dict]
            A list of dictionary representations of the FeedEntities.
        """
        header = get_field(feed, "header")
        list_entity = get_field(feed, "entity")
        list_dict_entities = []
        for entity in list_entity:
            list_dict_entities.extend(
                self._get_dicts_from_feed(header, entity))

        return list_dict_entities

    def _get_df_feed(
        self,
        list_dict_entities: List[dict],
        latest_updates: dict
    ) -> pd.DataFrame:
        """
        Convert a list of dictionary representations of FeedEntities to a DataFrame.

        Parameters:
        -----------
        list_dict_entities: List[dict]
            A list of dictionary representations of the FeedEntities.

        latest_updates: dict
            A dictionary mapping vehicle IDs to their latest timestamps.

        Returns
        -------
        df_feed: pd.DataFrame
            A DataFrame representation of the FeedEntities.

        vehicle_current_timestamp: dict
            A dictionary mapping vehicle IDs to their latest timestamps.
        """
        list_non_duplicates = []
        for dict_entity in list_dict_entities:
            cond, latest_updates = self._check_duplicate_entity(
                dict_entity, latest_updates
            )
            if cond:
                list_non_duplicates.append(dict_entity)

        df_feed = pd.DataFrame(list_non_duplicates)
        return df_feed, latest_updates

    def _check_parameters(self):
        """
            Check if all required parameters are present.
        """
        for key in REQUIRED_CONNECTION_KEYS:
            if key not in self.connection_params:
                raise ValueError(f"Missing connection parameter: {key}")

        for key in REQUIRED_INGESTION_KEYS:
            if key not in self.data_ingestion_params:
                raise ValueError(f"Missing data ingestion parameter: {key}")

    def _get_sql_connection(self) -> tuple:
        """
        Establishes a connection to the MySQL database with retry logic.

        Returns
        -------
        conn : mysql.connector.connection.MySQLConnection
            Active MySQL connection object.

        cur : mysql.connector.cursor.MySQLCursor
            Cursor object for executing queries.
        """
        connection_state = False
        while connection_state is False:
            try:
                conn = mysql.connector.connect(
                    **self.connection_params
                )
                cur = conn.cursor()
                logger.info("%s: Connected to MySQL database.",
                            self.TABLE_NAME)
                connection_state = True
            except mysql.connector.Error:
                logger.exception(
                    "%s: Failed to connect to MySQL", self.TABLE_NAME)
                time.sleep(self.connection_retry_delay_seconds)

        return conn, cur

    def _create_table_if_not_exists(self, conn, cur):
        """
        Creates the table if it does not already exist.

        Parameters
        ----------
        conn : mysql.connector.connection.MySQLConnection
            Active MySQL connection object.

        cur : mysql.connector.cursor.MySQLCursor
            Cursor object for executing queries.
        """
        cur.execute(self.CREATE_TABLE_QUERY)
        conn.commit()
        logger.info("%s: Ensured table exists.", self.TABLE_NAME)

    def _process_and_insert_data(self, data, conn, cur):
        """
        Processes GTFS-Realtime data and inserts it into the database.

        Parameters
        ----------
        data : bytes
            Raw GTFS-Realtime feed data.

        conn : mysql.connector.connection.MySQLConnection
            Active MySQL connection object.

        cur : mysql.connector.cursor.MySQLCursor
            Cursor object for executing queries.
        """
        if data:
            feed = parse_gtfs_data(data)
            entities = self._extract_feed_info(feed)
            logger.info("%s: Feed retrieved with %d entities.",
                        self.TABLE_NAME, len(feed.entity))

            df_feed, self._latest_update = self._get_df_feed(
                entities, self._latest_update)

            if not df_feed.empty:
                df_feed = self._format_df_feed(df_feed)

                # Convert DataFrame to list of tuples
                data_to_insert = [tuple(row)[1:]
                                  for row in df_feed.itertuples()]

                logger.info("%s: Inserting %d rows into the database...",
                            self.TABLE_NAME, len(data_to_insert))
                cur.executemany(self.INSERT_QUERY, data_to_insert)
                conn.commit()
                logger.info("%s: Insert committed successfully.",
                            self.TABLE_NAME)
            else:
                logger.info("%s: No new data to insert.", self.TABLE_NAME)

        else:
            logger.warning("%s: No data received from feed.", self.TABLE_NAME)

    def run_ingestion_loop(self):
        """
        Starts the continuous data ingestion loop. Continually fetches data 
        from the GTFS-Realtime feed, processes it, and inserts it into the 
        MySQL database.
        """
        conn, cur = self._get_sql_connection()
        self._create_table_if_not_exists(conn, cur)

        while True:
            try:
                logger.info("%s: Fetching GTFS feed...", self.TABLE_NAME)
                data = fetch_gtfs_data(
                    self.data_ingestion_params[DIP_INGESTION_URL_KEY])
                self._process_and_insert_data(data, conn, cur)

            except mysql.connector.Error:
                logger.exception(
                    "%s: MySQL error, will attempt reconnection.", self.TABLE_NAME)
                conn, cur = self._get_sql_connection()

            # pylint: disable=broad-except
            except Exception:
                logger.exception("%s: Unexpected error.", self.TABLE_NAME)
            # Sleep between feed fetches (adjust to feed update interval)
            time.sleep(self.fetch_delay_seconds)


class VehicleUpdatesDataIngestion(DataIngestion):
    """
    Class to handle data ingestion from GTFS-Realtime feed to MySQL database.

    Parameters
    ----------
    connection_params : dict
        Dictionary containing MySQL connection parameters.
        Should include keys: host, user, password, database, port.

    data_ingestion_params : dict
        Dictionary containing data ingestion parameters.
        Should include keys: ingestion_url, fetch_delay_seconds, connection_retry_delay_seconds.

    Methods
    -------
    run_ingestion_loop()
        Starts the continuous data ingestion loop.
    """
    CREATE_TABLE_QUERY = VEHICLE_UPDATES_CREATE_TABLE_QUERY
    INSERT_QUERY = VEHICLE_UPDATES_INSERT_QUERY
    TABLE_NAME = VEHICLE_UPDATE_TABLE

    @staticmethod
    def _get_dicts_from_feed(header, entity) -> List[Dict]:
        """
        Convert a FeedEntity to a dictionary, handling missing fields gracefully.

        Parameters:
        -----------
        header: gtfs_realtime_pb2.FeedHeader
            The FeedHeader containing metadata about the feed.

        entity: gtfs_realtime_pb2.FeedEntity
            The FeedEntity to convert.

        Returns
        -------
        List[Dict]
            A list of dictionary representations of the FeedEntity.
        """
        vehicle = get_field(entity, "vehicle")
        trip = get_field(vehicle, "trip")
        position = get_field(vehicle, "position")
        vehicle_info = get_field(vehicle, "vehicle")

        return [{
            "feed_timestamp": get_field(header, "timestamp"),
            "entity_id": get_field(entity, "id"),
            "trip_id": get_field(trip, "trip_id"),
            "trip_start_time": get_field(trip, "start_time"),
            "trip_start_date": get_field(trip, "start_date"),
            "trip_schedule_relationship": get_field(trip, "schedule_relationship"),
            "trip_route_id": get_field(trip, "route_id"),
            "trip_direction_id": get_field(trip, "direction_id"),
            "position_latitude": get_field(position, "latitude"),
            "position_longitude": get_field(position, "longitude"),
            "position_bearing": get_field(position, "bearing"),
            "position_odometer": get_field(position, "odometer"),
            "position_speed": get_field(position, "speed"),
            "current_stop_sequence": get_field(vehicle, "current_stop_sequence"),
            "current_status": get_field(vehicle, "current_status"),
            "timestamp": get_field(vehicle, "timestamp"),
            "congestion_level": get_field(vehicle, "congestion_level"),
            "stop_id": get_field(vehicle, "stop_id"),
            "vehicle_id": get_field(vehicle_info, "id"),
            "vehicle_label": get_field(vehicle_info, "label"),
        }]

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
        vehicle_id = entity['vehicle_id']
        cond = vehicle_id not in latest_update
        cond |= (vehicle_id in latest_update and
                 entity['timestamp'] > latest_update[vehicle_id])

        if cond:
            latest_update[vehicle_id] = entity['timestamp']

        return cond, latest_update

    @staticmethod
    def _format_df_feed(df_feed: pd.DataFrame) -> pd.DataFrame:
        df_feed['trip_start_timestamp'] = pd.to_datetime(
            df_feed['trip_start_date'] + ' ' + df_feed['trip_start_time'],
            format='%Y%m%d %H:%M:%S', errors='coerce'
        )
        df_feed['feed_timestamp'] = pd.to_datetime(
            df_feed['feed_timestamp'], unit='s')
        df_feed['timestamp'] = pd.to_datetime(df_feed['timestamp'], unit='s')

        process_cols = df_feed.dtypes[df_feed.dtypes == object]
        for col in process_cols.index:
            df_feed[col] = df_feed[col].apply(lambda x: x if x != '' else None)
        if 'trip_start_time' in df_feed.columns:
            del df_feed['trip_start_time']
        if 'trip_start_date' in df_feed.columns:
            del df_feed['trip_start_date']
        col_types = {
            'entity_id': int,
            'trip_id': str,
            'trip_schedule_relationship': int,
            'trip_route_id': str,
            'trip_direction_id': int,
            'position_latitude': float,
            'position_longitude': float,
            'position_bearing': float,
            'position_odometer': float,
            'position_speed': float,
            'current_stop_sequence': int,
            'current_status': int,
            'congestion_level': int,
            'stop_id': str,
            'vehicle_id': int,
            'vehicle_label': int
        }
        for col, col_type in col_types.items():
            if col in df_feed.columns:
                df_feed[col] = df_feed[col].astype(col_type)

        # drop cols with No trip start timestamp
        df_feed = df_feed[~df_feed['trip_start_timestamp'].isna()].reset_index(
            drop=True)
        return df_feed


class TripUpdatesDataIngestion(DataIngestion):
    """
    Class to handle data ingestion from GTFS-Realtime feed to MySQL database.

    Parameters
    ----------
    connection_params : dict
        Dictionary containing MySQL connection parameters.
        Should include keys: host, user, password, database, port.

    data_ingestion_params : dict
        Dictionary containing data ingestion parameters.
        Should include keys: ingestion_url, fetch_delay_seconds, connection_retry_delay_seconds.

    Methods
    -------
    run_ingestion_loop()
        Starts the continuous data ingestion loop.
    """
    CREATE_TABLE_QUERY = TRIP_UPDATES_CREATE_TABLE_QUERY
    INSERT_QUERY = TRIP_UPDATES_INSERT_QUERY
    TABLE_NAME = TRIP_UPDATE_TABLE

    @staticmethod
    def _get_dicts_from_feed(header, entity) -> dict:
        """
        Convert a FeedEntity to a dictionary, handling missing fields gracefully.

        Parameters:
        -----------
        header: gtfs_realtime_pb2.FeedHeader
            The FeedHeader containing metadata about the feed.

        entity: gtfs_realtime_pb2.FeedEntity
            The FeedEntity to convert.

        Returns
        -------
        list
            A list of dictionary representations of the FeedEntity.
        """
        trip_update = get_field(entity, "trip_update")
        trip = get_field(trip_update, "trip")
        stop_time_update = get_field(trip_update, "stop_time_update")
        base_dict = {
            "feed_timestamp": get_field(header, "timestamp"),
            "trip_id": get_field(trip, "trip_id"),
            "trip_start_time": get_field(trip, "start_time"),
            "trip_start_date": get_field(trip, "start_date"),
            "trip_schedule_relationship": get_field(trip, "schedule_relationship"),
            "trip_route_id": get_field(trip, "route_id"),
            "trip_direction_id": get_field(trip, "direction_id"),
        }
        list_dicts = []
        for stu in stop_time_update:
            dict_str = base_dict.copy()
            arrival = get_field(stu, "arrival")
            departure = get_field(stu, "departure")
            dict_str.update({
                "stop_sequence": get_field(stu, "stop_sequence"),
                "stop_id": get_field(stu, "stop_id"),
                "schedule_relationship": get_field(stu, "schedule_relationship"),
                "arrival_delay": get_field(arrival, "delay"),
                "arrival_time": get_field(arrival, "time"),
                "arrival_uncertainty": get_field(arrival, "uncertainty"),
                "departure_delay": get_field(departure, "delay"),
                "departure_time": get_field(departure, "time"),
                "departure_uncertainty": get_field(departure, "uncertainty"),
            })
            list_dicts.append(dict_str)

        return list_dicts

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:

        check_cols = [
            'arrival_delay', 'arrival_time', 'arrival_uncertainty',
            'departure_delay', 'departure_time', 'departure_uncertainty'
        ]

        trip_id = entity['trip_id']
        stop_id = entity['stop_id']
        cond = False
        if trip_id not in latest_update:
            cond = True
            latest_update[trip_id] = {}
        elif stop_id not in latest_update[trip_id]:
            cond = True
        else:
            for col in check_cols:
                if entity[col] != latest_update[trip_id][stop_id][col]:
                    cond = True
                    break

        if cond:
            latest_update[trip_id][stop_id] = {
                col: entity[col] for col in check_cols}
        return cond, latest_update

    @staticmethod
    def _format_df_feed(df_feed: pd.DataFrame) -> pd.DataFrame:
        df_feed['trip_start_timestamp'] = pd.to_datetime(
            df_feed['trip_start_date'] + ' ' + df_feed['trip_start_time'],
            format='%Y%m%d %H:%M:%S', errors='coerce'
        )

        process_cols = df_feed.dtypes[df_feed.dtypes == object]
        for col in process_cols.index:
            df_feed[col] = df_feed[col].apply(lambda x: x if x != '' else None)
        if 'trip_start_time' in df_feed.columns:
            del df_feed['trip_start_time']
        if 'trip_start_date' in df_feed.columns:
            del df_feed['trip_start_date']

        time_columns = ["feed_timestamp", "arrival_time", "departure_time"]
        for col in time_columns:
            df_feed[col] = df_feed[col].apply(
                lambda x: pd.to_datetime(x, unit="s", errors='coerce') if x != 0 else None)
            df_feed[col] = df_feed[col].astype(object)
            df_feed[col] = df_feed[col].replace(pd.NaT, None)

        return df_feed
