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
import requests
from google.transit import gtfs_realtime_pb2
from requests.exceptions import Timeout

from . import constants as cons
from . import queries as sql_queries

logger = logging.getLogger(__name__)

# Protobuf generated modules expose attributes that static linters may not
# understand. We apply a single-line disable at the use site instead of a
# module-level suppression.


def fetch_gtfs_data(url: str) -> bytes | None:
    """
    Fetch GTFS real-time data from the given URL.

    Parameters
    ----------
    url : str
        The GTFS-Realtime feed URL to request.

    Returns
    -------
    bytes or None
        The raw feed bytes if the request is successful; otherwise ``None``.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.content
    except (Timeout, requests.exceptions.ConnectionError):
        logger.exception("Network error or timeout occurred while fetching GTFS data.")
    except requests.exceptions.HTTPError as e:
        logger.exception("HTTP error while fetching GTFS data: %s", e)
    except requests.exceptions.RequestException as e:
        logger.exception("Unexpected error while fetching GTFS data: %s", e)

    return None


def parse_gtfs_data(data: bytes):
    """
    Parse GTFS real-time data from bytes into a protobuf FeedMessage.

    Parameters
    ----------
    data : bytes
        Raw GTFS-Realtime protobuf bytes as returned by the feed endpoint.

    Returns
    -------
    google.transit.gtfs_realtime_pb2.FeedMessage
        The parsed protobuf ``FeedMessage`` instance.
    """
    feed = gtfs_realtime_pb2.FeedMessage()  # pylint: disable=no-member
    feed.ParseFromString(data)
    return feed


def get_field(feed, key, default=None):
    """
    Safely retrieve an attribute from an object using optional dotted paths.

    Parameters
    ----------
    feed : object
        The object to access.

    key : str
        The attribute name or dotted attribute path to retrieve.

    default : Any, optional
        The value to return if any attribute in the path is not found.
        Defaults to ``None``.

    Returns
    -------
    Any
        The resolved attribute value if present; otherwise ``default``.

    Notes
    -----
    If ``key`` contains ``.`` separators, each segment is resolved in order
    using ``getattr``. If any segment is missing, the function returns
    ``default`` immediately.
    """
    keys = key.split(".")
    if len(keys) == 1:
        return getattr(feed, key, default)
    else:
        value = feed
        for k in keys:
            value = getattr(value, k, default)
            if value is None:
                return default
        return value


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

    time_columns : list[str]
        List of column names in the feed that should be normalized as datetimes.

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
        """Require concrete subclasses to define SQL and table metadata."""
        super().__init_subclass__()
        if cls.CREATE_TABLE_QUERY is None:
            raise TypeError(f"{cls.__name__} must define 'CREATE_TABLE_QUERY'")
        if cls.INSERT_QUERY is None:
            raise TypeError(f"{cls.__name__} must define 'INSERT_QUERY'")
        if cls.TABLE_NAME is None:
            raise TypeError(f"{cls.__name__} must define 'TABLE_NAME'")

    def __init__(
        self,
        connection_params: dict,
        data_ingestion_params: dict,
        time_columns: List[str],
    ):
        """Store configuration and validate the required ingestion parameters."""
        self.connection_params = connection_params
        self.data_ingestion_params = data_ingestion_params
        self._time_columns = time_columns
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
        """
        Convert a feed entity into one or more dictionary rows.

        Parameters
        ----------
        header : google.transit.gtfs_realtime_pb2.FeedHeader
            The feed header object containing metadata for the feed.

        entity : google.transit.gtfs_realtime_pb2.FeedEntity
            The feed entity to convert into one or more row dictionaries.

        Returns
        -------
        list[dict]
            One or more dictionaries representing flattened rows extracted from the entity.
        """

    @staticmethod
    @abstractmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
        """
        Determine whether an entity is a duplicate and update tracking state.

        Parameters
        ----------
        entity : dict
            The candidate entity dictionary extracted from the feed.

        latest_update : dict
            The current duplicate-tracking state for previously seen entities.

        Returns
        -------
        (bool, dict)
            Tuple where the first element is True if the entity should be ingested
            (i.e., is not a duplicate), and the second element is the updated
            `latest_update` tracking structure.
        """

    def _format_df_feed(self, df_feed: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize the raw feed DataFrame into database-ready types.

        Parameters
        ----------
        df_feed : pandas.DataFrame
            Raw DataFrame created from flattened feed dictionaries.

        Returns
        -------
        pandas.DataFrame
            A transformed DataFrame with properly-typed columns ready for DB insert.
        """
        trip_fields = cons.TripTableIngestionFields

        if (
            trip_fields.TRIP_START_TIME_KEY in df_feed.columns
            and trip_fields.TRIP_START_DATE_KEY in df_feed.columns
        ):
            df_feed[trip_fields.TRIP_START_TIMESTAMP_KEY] = pd.to_datetime(
                df_feed[trip_fields.TRIP_START_DATE_KEY]
                + " "
                + df_feed[trip_fields.TRIP_START_TIME_KEY],
                format=cons.TRIP_START_TIMESTAMP_FORMAT,
                errors="coerce",
            )

        process_cols = df_feed.dtypes[df_feed.dtypes == "object"]
        for col in process_cols.index:
            df_feed[col] = df_feed[col].apply(lambda x: x if x != "" else None)

        if trip_fields.TRIP_START_TIME_KEY in df_feed.columns:
            del df_feed[trip_fields.TRIP_START_TIME_KEY]
        if trip_fields.TRIP_START_DATE_KEY in df_feed.columns:
            del df_feed[trip_fields.TRIP_START_DATE_KEY]

        for col in self._time_columns:
            if col in df_feed.columns:
                df_feed[col] = DataIngestion._normalize_datetime_series(
                    df_feed[col], unit=cons.UNIX_TIMESTAMP_UNIT
                )

        return df_feed

    @staticmethod
    def _to_python_datetime(value, unit=None):
        """Convert a pandas-compatible datetime value into a MySQL-safe Python datetime."""
        if value is None or pd.isna(value) or value == 0:
            return None

        parsed_value = pd.to_datetime(value, unit=unit, errors="coerce")
        if pd.isna(parsed_value):
            return None

        # Reject datetimes outside MySQL's supported range (year >= 1000)
        # Pandas may parse strings like '0001-01-01' which Python returns
        # as year 1 — MySQL will reject those, so treat them as missing.
        year = getattr(parsed_value, "year", None)
        if year is None:
            return None
        if year < 1000:
            return None

        return parsed_value.to_pydatetime()

    @staticmethod
    def _normalize_datetime_series(series: pd.Series, unit=None) -> pd.Series:
        """Normalize a Series of datetimes to object dtype while preserving None values."""
        return pd.Series(
            [DataIngestion._to_python_datetime(value, unit=unit) for value in series],
            index=series.index,
            dtype=object,
        )

    def _extract_feed_info(self, feed) -> List[Dict]:
        """
        Convert a FeedEntity to a dictionary, handling missing fields gracefully.

        Parameters:
        -----------
        feed: gtfs_realtime_pb2.Feed
            The Feed to convert.

        Returns
        -------
        list[dict]
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
    ) -> Tuple[pd.DataFrame, dict]:
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

        Parameters
        ----------
        stop_event : threading.Event
            Event used to signal shutdown; connection attempts stop if set.

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
        Starts the continuous data ingestion loop.

        The loop repeatedly fetches the GTFS-Realtime feed, processes the data,
        and inserts new rows into the configured MySQL table until
        `stop_event` is set.

        Parameters
        ----------
        stop_event : threading.Event
            Event used to signal shutdown of the ingestion loop.

        Returns
        -------
        None
            Runs until `stop_event` is set; performs work via side-effects.
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

                except Exception:  # pylint: disable=broad-except
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

    def __init__(self, connection_params: dict, data_ingestion_params: dict):
        super().__init__(
            connection_params=connection_params,
            data_ingestion_params=data_ingestion_params,
            time_columns=[
                cons.VehicleTableIngestionFields.FEED_TIMESTAMP,
                cons.VehicleTableIngestionFields.TIMESTAMP,
                cons.VehicleTableIngestionFields.TRIP_START_TIMESTAMP,
            ],
        )

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
        list[dict]
            A list of dictionary representations of the FeedEntity.
        """
        fields = cons.VehicleTableIngestionFields
        raw_fields = cons.VehicleTableRawFields

        def _entity_value(raw_path: str):
            return get_field(entity, raw_path.removeprefix("entity."))

        def _header_value(raw_path: str):
            return get_field(header, raw_path.removeprefix("header."))

        mapped_entity = {}
        for name, raw_path in vars(raw_fields).items():
            if not name.isupper():
                continue

            column = getattr(fields, name, None)
            if column is None:
                continue

            if raw_path.startswith("header."):
                mapped_entity[column] = _header_value(raw_path)
            elif raw_path.startswith("entity."):
                mapped_entity[column] = _entity_value(raw_path)
            else:
                mapped_entity[column] = get_field(entity, raw_path)

        return [mapped_entity]

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
        """
        Return whether the vehicle update is newer than the last seen record.

        Parameters
        ----------
        entity : dict
            Flattened vehicle entity dictionary.

        latest_update : dict
            Current tracking state for latest vehicle timestamps.

        Returns
        -------
        cond : bool
            True if the entity is not a duplicate and should be ingested; False otherwise.
        updated_latest_update : dict
            Updated tracking state for latest vehicle timestamps, with the current entity's
            timestamp included if it is newer than the existing record for that vehicle.
        """
        fields = cons.VehicleTableIngestionFields
        vehicle_id = entity.get(fields.VEHICLE_ID)

        if vehicle_id is None:
            return False, latest_update

        cond = vehicle_id not in latest_update
        cond |= (
            vehicle_id in latest_update
            and entity[fields.TIMESTAMP] > latest_update[vehicle_id]
        )

        if cond:
            latest_update[vehicle_id] = entity[fields.TIMESTAMP]

        return cond, latest_update


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

    def __init__(self, connection_params: dict, data_ingestion_params: dict):
        super().__init__(
            connection_params=connection_params,
            data_ingestion_params=data_ingestion_params,
            time_columns=[
                cons.TripTableIngestionFields.FEED_TIMESTAMP,
                cons.TripTableIngestionFields.ARRIVAL_TIME,
                cons.TripTableIngestionFields.DEPARTURE_TIME,
                cons.TripTableIngestionFields.TRIP_START_TIMESTAMP,
            ],
        )

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
        list[dict]
            A list of dictionary representations of the FeedEntity.
        """
        fields = cons.TripTableIngestionFields
        raw_fields = cons.TripTableRawFields

        trip_update = get_field(entity, cons.TRIP_UPDATE_KEY)
        stop_time_update = get_field(trip_update, cons.STOP_TIME_UPDATE_KEY)

        list_dicts = []
        if not stop_time_update:
            return list_dicts

        for stu in stop_time_update:

            def _resolve(raw_path: str):
                if raw_path.startswith("header."):
                    return get_field(header, raw_path.removeprefix("header."))
                if raw_path.startswith("entity.trip_update.trip."):
                    return get_field(
                        trip_update, raw_path.removeprefix("entity.trip_update.")
                    )
                if raw_path.startswith("stop_time_update."):
                    return get_field(stu, raw_path.removeprefix("stop_time_update."))
                return None

            mapped = {}
            for name, raw_path in vars(raw_fields).items():
                if not name.isupper():
                    continue
                column = getattr(fields, name, None)
                if column is None:
                    continue
                mapped[column] = _resolve(raw_path)

            list_dicts.append(mapped)

        return list_dicts

    @staticmethod
    def _check_duplicate_entity(entity, latest_update) -> Tuple[bool, Dict]:
        """
        Return whether the trip update contains a changed stop-time snapshot.

        Parameters
        ----------
        entity : dict
            Flattened trip update dictionary.

        latest_update : dict
            Tracking structure for previous trip stop-time snapshots.

        Returns
        -------
        cond : bool
            True if the entity is not a duplicate and should be ingested; False otherwise.
        updated_latest_update : dict
            Updated tracking state for latest trip stop-time snapshots, with the current entity's
            snapshot included if it is newer than the existing record for that trip/stop.
        """

        fields = cons.TripTableIngestionFields
        check_cols = [
            fields.ARRIVAL_DELAY,
            fields.ARRIVAL_TIME,
            fields.ARRIVAL_UNCERTAINTY,
            fields.DEPARTURE_DELAY,
            fields.DEPARTURE_TIME,
            fields.DEPARTURE_UNCERTAINTY,
        ]

        trip_id = entity.get(fields.TRIP_ID)
        stop_id = entity.get(fields.STOP_ID)
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
