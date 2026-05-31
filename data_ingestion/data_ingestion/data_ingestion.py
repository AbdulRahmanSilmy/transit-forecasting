"""
INTRO
-----
Contains the data ingestion scripts for populating each table in the database
is encapsulated within its respective data ingestion classes

The following are currently populated tables and their respective classes:
- Vehicle Updates -> VehicleUpdatesDataIngestion
- Trip Updates -> TripUpdatesDataIngestion

"""

import logging
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

import mysql.connector
import pandas as pd

from . import constants as cons
from . import queries as sql_queries
from .utils import fetch_gtfs_data, get_field, parse_gtfs_data

logger = logging.getLogger(__name__)


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

    Notes
    -----
    Each ingestion instance owns its own ``_latest_update`` state. The current
    runner creates one instance per thread, so duplicate tracking is thread-safe
    as long as instances are not shared across threads.

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
            raise TypeError(f"{cls.__name__} must define 'CREATE_TABLE_QUERY'")
        if cls.INSERT_QUERY is None:
            raise TypeError(f"{cls.__name__} must define 'INSERT_QUERY'")
        if cls.TABLE_NAME is None:
            raise TypeError(f"{cls.__name__} must define 'TABLE_NAME'")

    def __init__(self, connection_params: dict, data_ingestion_params: dict):
        self.connection_params = connection_params
        self.data_ingestion_params = data_ingestion_params
        self._latest_update = {}

        self._check_parameters()
        self.fetch_delay_seconds = self.data_ingestion_params[
            cons.DIP_FETCH_DELAY_SECONDS_KEY
        ]
        self.connection_retry_delay_seconds = self.data_ingestion_params[
            cons.DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY
        ]

    @staticmethod
    @abstractmethod
    def _get_dicts_from_feed(header, entity) -> List[dict]:
        pass

    @staticmethod
    @abstractmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
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
        header = get_field(feed, cons.HEADER_KEY)
        list_entity = get_field(feed, cons.ENTITY_KEY)
        list_dict_entities = []
        for entity in list_entity:
            list_dict_entities.extend(self._get_dicts_from_feed(header, entity))

        return list_dict_entities

    def _get_df_feed(
        self, list_dict_entities: List[dict], latest_updates: dict
    ) -> pd.DataFrame:
        """
        Convert a list of dictionary representations of FeedEntities to a DataFrame.

        Parameters:
        -----------
        list_dict_entities: List[dict]
            A list of dictionary representations of the FeedEntities.

        latest_updates: dict
            A tracking structure to identify and filter out duplicate entities based on their
            unique identifiers.
        Returns
        -------
        df_feed: pd.DataFrame
            A DataFrame representation of the FeedEntities.

        latest_updates: dict
            An updated latest-updates tracking structure, in the same format as the input
            ``latest_updates``.
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
        for key in cons.REQUIRED_CONNECTION_KEYS:
            if key not in self.connection_params:
                raise ValueError(f"Missing connection parameter: {key}")

        for key in cons.REQUIRED_INGESTION_KEYS:
            if key not in self.data_ingestion_params:
                raise ValueError(f"Missing data ingestion parameter: {key}")

    def _get_sql_connection(self, stop_event: threading.Event) -> tuple:
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
        while connection_state is False and not stop_event.is_set():
            try:
                conn = mysql.connector.connect(**self.connection_params)
                cur = conn.cursor()
                logger.info("%s: Connected to MySQL database.", self.TABLE_NAME)
                connection_state = True
            except mysql.connector.Error:
                logger.exception("%s: Failed to connect to MySQL", self.TABLE_NAME)
                stop_event.wait(self.connection_retry_delay_seconds)

        if stop_event.is_set():
            return None, None

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
            logger.info(
                "%s: Feed retrieved with %d entities.",
                self.TABLE_NAME,
                len(feed.entity),
            )

            df_feed, self._latest_update = self._get_df_feed(
                entities, self._latest_update
            )

            if not df_feed.empty:
                df_feed = self._format_df_feed(df_feed)

                # Convert DataFrame to list of tuples
                data_to_insert = [tuple(row)[1:] for row in df_feed.itertuples()]

                logger.info(
                    "%s: Inserting %d rows into the database...",
                    self.TABLE_NAME,
                    len(data_to_insert),
                )
                cur.executemany(self.INSERT_QUERY, data_to_insert)
                conn.commit()
                logger.info("%s: Insert committed successfully.", self.TABLE_NAME)
            else:
                logger.info("%s: No new data to insert.", self.TABLE_NAME)

        else:
            logger.warning("%s: No data received from feed.", self.TABLE_NAME)

    def run_ingestion_loop(self, stop_event: threading.Event):
        """
        Starts the continuous data ingestion loop. Continually fetches data
        from the GTFS-Realtime feed, processes it, and inserts it into the
        MySQL database.
        """
        conn = None
        cur = None
        try:
            conn, cur = self._get_sql_connection(stop_event)
            if conn is None or cur is None:
                return

            self._create_table_if_not_exists(conn, cur)

            while not stop_event.is_set():
                try:
                    logger.info("%s: Fetching GTFS feed...", self.TABLE_NAME)
                    data = fetch_gtfs_data(
                        self.data_ingestion_params[cons.DIP_INGESTION_URL_KEY]
                    )
                    self._process_and_insert_data(data, conn, cur)

                except mysql.connector.Error:
                    logger.exception(
                        "%s: MySQL error, will attempt reconnection.", self.TABLE_NAME
                    )
                    conn, cur = self._get_sql_connection(stop_event)
                    if conn is None or cur is None:
                        return

                # pylint: disable=broad-except
                except Exception:
                    logger.exception("%s: Unexpected error.", self.TABLE_NAME)

                # Sleep between feed fetches (adjust to feed update interval)
                stop_event.wait(self.fetch_delay_seconds)
        finally:
            if cur is not None:
                cur.close()
            if conn is not None:
                conn.close()


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

    CREATE_TABLE_QUERY = sql_queries.VEHICLE_UPDATES_CREATE_TABLE_QUERY
    INSERT_QUERY = sql_queries.VEHICLE_UPDATES_INSERT_QUERY
    TABLE_NAME = cons.VEHICLE_UPDATE_TABLE

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
        vehicle = get_field(entity, cons.VEHICLE_KEY)
        trip = get_field(vehicle, cons.TRIP_KEY)
        position = get_field(vehicle, cons.POSITION_KEY)
        vehicle_info = get_field(vehicle, cons.VEHICLE_KEY)

        return [
            {
                cons.FEED_TIMESTAMP_KEY: get_field(header, cons.TIMESTAMP_KEY),
                cons.ENTITY_ID_KEY: get_field(entity, cons.ID_KEY),
                cons.TRIP_ID_KEY: get_field(trip, cons.TRIP_ID_KEY),
                cons.TRIP_START_TIME_KEY: get_field(trip, cons.TRIP_START_TIME_KEY),
                cons.TRIP_START_DATE_KEY: get_field(trip, cons.TRIP_START_DATE_KEY),
                cons.TRIP_SCHEDULE_RELATIONSHIP_KEY: get_field(
                    trip, cons.SCHEDULE_RELATIONSHIP_KEY
                ),
                cons.TRIP_ROUTE_ID_KEY: get_field(trip, cons.TRIP_ROUTE_ID_KEY),
                cons.TRIP_DIRECTION_ID_KEY: get_field(trip, cons.DIRECTION_ID_KEY),
                cons.POSITION_LATITUDE_KEY: get_field(position, cons.LATITUDE_KEY),
                cons.POSITION_LONGITUDE_KEY: get_field(position, cons.LONGITUDE_KEY),
                cons.POSITION_BEARING_KEY: get_field(position, cons.BEARING_KEY),
                cons.POSITION_ODOMETER_KEY: get_field(position, cons.ODOMETER_KEY),
                cons.POSITION_SPEED_KEY: get_field(position, cons.SPEED_KEY),
                cons.CURRENT_STOP_SEQUENCE_KEY: get_field(
                    vehicle, cons.CURRENT_STOP_SEQUENCE_KEY
                ),
                cons.CURRENT_STATUS_KEY: get_field(vehicle, cons.CURRENT_STATUS_KEY),
                cons.TIMESTAMP_KEY: get_field(vehicle, cons.TIMESTAMP_KEY),
                cons.CONGESTION_LEVEL_KEY: get_field(
                    vehicle, cons.CONGESTION_LEVEL_KEY
                ),
                cons.STOP_ID_KEY: get_field(vehicle, cons.STOP_ID_KEY),
                cons.VEHICLE_ID_KEY: get_field(vehicle_info, cons.ID_KEY),
                cons.VEHICLE_LABEL_KEY: get_field(vehicle_info, cons.LABEL_KEY),
            }
        ]

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
        vehicle_id = entity.get(cons.VEHICLE_ID_KEY)

        if vehicle_id is None:
            return False, latest_update

        cond = vehicle_id not in latest_update
        cond |= (
            vehicle_id in latest_update
            and entity[cons.TIMESTAMP_KEY] > latest_update[vehicle_id]
        )

        if cond:
            latest_update[vehicle_id] = entity[cons.TIMESTAMP_KEY]

        return cond, latest_update

    @staticmethod
    def _format_df_feed(df_feed: pd.DataFrame) -> pd.DataFrame:
        df_feed[cons.TRIP_START_TIMESTAMP_KEY] = pd.to_datetime(
            df_feed[cons.TRIP_START_DATE_KEY] + " " + df_feed[cons.TRIP_START_TIME_KEY],
            format=cons.TRIP_START_TIMESTAMP_FORMAT,
            errors="coerce",
        )
        df_feed[cons.FEED_TIMESTAMP_KEY] = pd.to_datetime(
            df_feed[cons.FEED_TIMESTAMP_KEY], unit=cons.UNIX_TIMESTAMP_UNIT
        )
        df_feed[cons.TIMESTAMP_KEY] = pd.to_datetime(
            df_feed[cons.TIMESTAMP_KEY], unit=cons.UNIX_TIMESTAMP_UNIT
        )

        process_cols = df_feed.dtypes[df_feed.dtypes == "object"]
        for col in process_cols.index:
            df_feed[col] = df_feed[col].apply(lambda x: x if x != "" else None)
        if cons.TRIP_START_TIME_KEY in df_feed.columns:
            del df_feed[cons.TRIP_START_TIME_KEY]
        if cons.TRIP_START_DATE_KEY in df_feed.columns:
            del df_feed[cons.TRIP_START_DATE_KEY]
        col_types = {
            cons.ENTITY_ID_KEY: int,
            cons.TRIP_ID_KEY: str,
            cons.TRIP_SCHEDULE_RELATIONSHIP_KEY: int,
            cons.TRIP_ROUTE_ID_KEY: str,
            cons.TRIP_DIRECTION_ID_KEY: int,
            cons.POSITION_LATITUDE_KEY: float,
            cons.POSITION_LONGITUDE_KEY: float,
            cons.POSITION_BEARING_KEY: float,
            cons.POSITION_ODOMETER_KEY: float,
            cons.POSITION_SPEED_KEY: float,
            cons.CURRENT_STOP_SEQUENCE_KEY: int,
            cons.CURRENT_STATUS_KEY: int,
            cons.CONGESTION_LEVEL_KEY: int,
            cons.STOP_ID_KEY: str,
            cons.VEHICLE_ID_KEY: int,
            cons.VEHICLE_LABEL_KEY: int,
        }
        for col, col_type in col_types.items():
            if col in df_feed.columns:
                df_feed[col] = df_feed[col].astype(col_type)

        # drop cols with No trip start timestamp
        df_feed = df_feed[~df_feed[cons.TRIP_START_TIMESTAMP_KEY].isna()].reset_index(
            drop=True
        )
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

    CREATE_TABLE_QUERY = sql_queries.TRIP_UPDATES_CREATE_TABLE_QUERY
    INSERT_QUERY = sql_queries.TRIP_UPDATES_INSERT_QUERY
    TABLE_NAME = cons.TRIP_UPDATE_TABLE

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
        list[Dict]
            A list of dictionary representations of the FeedEntity.
        """
        trip_update = get_field(entity, cons.TRIP_UPDATE_KEY)
        trip = get_field(trip_update, cons.TRIP_KEY)
        stop_time_update = get_field(trip_update, cons.STOP_TIME_UPDATE_KEY)
        base_dict = {
            cons.FEED_TIMESTAMP_KEY: get_field(header, cons.TIMESTAMP_KEY),
            cons.TRIP_ID_KEY: get_field(trip, cons.TRIP_ID_KEY),
            cons.TRIP_START_TIME_KEY: get_field(trip, cons.TRIP_START_TIME_KEY),
            cons.TRIP_START_DATE_KEY: get_field(trip, cons.TRIP_START_DATE_KEY),
            cons.TRIP_SCHEDULE_RELATIONSHIP_KEY: get_field(
                trip, cons.SCHEDULE_RELATIONSHIP_KEY
            ),
            cons.TRIP_ROUTE_ID_KEY: get_field(trip, cons.TRIP_ROUTE_ID_KEY),
            cons.TRIP_DIRECTION_ID_KEY: get_field(trip, cons.DIRECTION_ID_KEY),
        }
        list_dicts = []
        if not stop_time_update:
            return list_dicts
        for stu in stop_time_update:
            dict_str = base_dict.copy()
            arrival = get_field(stu, cons.ARRIVAL_KEY)
            departure = get_field(stu, cons.DEPARTURE_KEY)
            dict_str.update(
                {
                    cons.STOP_SEQUENCE_KEY: get_field(stu, cons.STOP_SEQUENCE_KEY),
                    cons.STOP_ID_KEY: get_field(stu, cons.STOP_ID_KEY),
                    cons.SCHEDULE_RELATIONSHIP_KEY: get_field(
                        stu, cons.SCHEDULE_RELATIONSHIP_KEY
                    ),
                    cons.ARRIVAL_DELAY_KEY: get_field(arrival, cons.DELAY_KEY),
                    cons.ARRIVAL_TIME_KEY: get_field(arrival, cons.TIME_KEY),
                    cons.ARRIVAL_UNCERTAINTY_KEY: get_field(
                        arrival, cons.UNCERTAINTY_KEY
                    ),
                    cons.DEPARTURE_DELAY_KEY: get_field(departure, cons.DELAY_KEY),
                    cons.DEPARTURE_TIME_KEY: get_field(departure, cons.TIME_KEY),
                    cons.DEPARTURE_UNCERTAINTY_KEY: get_field(
                        departure, cons.UNCERTAINTY_KEY
                    ),
                }
            )
            list_dicts.append(dict_str)

        return list_dicts

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:

        check_cols = [
            cons.ARRIVAL_DELAY_KEY,
            cons.ARRIVAL_TIME_KEY,
            cons.ARRIVAL_UNCERTAINTY_KEY,
            cons.DEPARTURE_DELAY_KEY,
            cons.DEPARTURE_TIME_KEY,
            cons.DEPARTURE_UNCERTAINTY_KEY,
        ]

        trip_id = entity.get(cons.TRIP_ID_KEY)
        stop_id = entity.get(cons.STOP_ID_KEY)
        cond = False

        # If we don't have valid identifiers, we cannot reliably deduplicate.
        if trip_id is None or stop_id is None:
            return cond, latest_update

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
            latest_update[trip_id][stop_id] = {col: entity[col] for col in check_cols}
        return cond, latest_update

    @staticmethod
    def _format_df_feed(df_feed: pd.DataFrame) -> pd.DataFrame:
        df_feed[cons.TRIP_START_TIMESTAMP_KEY] = pd.to_datetime(
            df_feed[cons.TRIP_START_DATE_KEY] + " " + df_feed[cons.TRIP_START_TIME_KEY],
            format=cons.TRIP_START_TIMESTAMP_FORMAT,
            errors="coerce",
        )

        process_cols = df_feed.dtypes[df_feed.dtypes == "object"]
        for col in process_cols.index:
            df_feed[col] = df_feed[col].apply(lambda x: x if x != "" else None)
        if cons.TRIP_START_TIME_KEY in df_feed.columns:
            del df_feed[cons.TRIP_START_TIME_KEY]
        if cons.TRIP_START_DATE_KEY in df_feed.columns:
            del df_feed[cons.TRIP_START_DATE_KEY]

        time_columns = [
            cons.FEED_TIMESTAMP_KEY,
            cons.ARRIVAL_TIME_KEY,
            cons.DEPARTURE_TIME_KEY,
        ]
        for col in time_columns:
            df_feed[col] = df_feed[col].apply(
                lambda x: (
                    pd.to_datetime(x, unit=cons.UNIX_TIMESTAMP_UNIT, errors="coerce")
                    if x != 0
                    else None
                )
            )
            df_feed[col] = df_feed[col].astype(object)
            df_feed[col] = df_feed[col].replace(pd.NaT, None)

        return df_feed
