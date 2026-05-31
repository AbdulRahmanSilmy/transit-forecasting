"""
Utility functions for GTFS real-time data ingestion and processing.

TODO:
-------
- Improve format data for db function
- Add documentation for all functions
"""

import logging

import requests
from google.transit import gtfs_realtime_pb2
from requests.exceptions import Timeout

logger = logging.getLogger(__name__)


def fetch_gtfs_data(url: str) -> bytes | None:
    """
    Fetch GTFS real-time data from the given URL.
    Returns None if the request fails or times out.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()  # Raise exception for 4xx/5xx responses
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
    Parse GTFS real-time data from bytes.
    """
    # pylint: disable=no-member
    feed = gtfs_realtime_pb2.FeedMessage()
    # pylint: enable=no-member
    feed.ParseFromString(data)
    return feed


def get_field(obj, field):
    return getattr(obj, field, None)


# def get_dict_from_feed(header, entity) -> dict:
#    """
#    Convert a FeedEntity to a dictionary, handling missing fields gracefully.
#
#    Parameters:
#    -----------
#    header: gtfs_realtime_pb2.FeedHeader
#        The FeedHeader containing metadata about the feed.
#
#    entity: gtfs_realtime_pb2.FeedEntity
#        The FeedEntity to convert.
#
#    Returns
#    -------
#    dict
#        A dictionary representation of the FeedEntity.
#    """
#    vehicle = get_field(entity, cons.VEHICLE_KEY)
#    trip = get_field(vehicle, cons.TRIP_KEY)
#    position = get_field(vehicle, cons.POSITION_KEY)
#    vehicle_info = get_field(vehicle, cons.VEHICLE_KEY)
#
#    return {
#        cons.FEED_TIMESTAMP_KEY: get_field(header, cons.TIMESTAMP_KEY),
#        cons.ENTITY_ID_KEY: get_field(entity, cons.ID_KEY),
#        cons.TRIP_ID_KEY: get_field(trip, cons.TRIP_ID_KEY),
#        cons.TRIP_START_TIME_KEY: get_field(trip, cons.TRIP_START_TIME_KEY),
#        cons.TRIP_START_DATE_KEY: get_field(trip, cons.TRIP_START_DATE_KEY),
#        cons.TRIP_SCHEDULE_RELATIONSHIP_KEY: get_field(
#            trip, cons.SCHEDULE_RELATIONSHIP_KEY
#        ),
#        cons.TRIP_ROUTE_ID_KEY: get_field(trip, cons.TRIP_ROUTE_ID_KEY),
#        cons.TRIP_DIRECTION_ID_KEY: get_field(trip, cons.TRIP_DIRECTION_ID_KEY),
#        cons.POSITION_LATITUDE_KEY: get_field(position, cons.POSITION_LATITUDE_KEY),
#        cons.POSITION_LONGITUDE_KEY: get_field(
#            position, cons.POSITION_LONGITUDE_KEY
#        ),
#        cons.POSITION_BEARING_KEY: get_field(position, cons.POSITION_BEARING_KEY),
#        cons.POSITION_ODOMETER_KEY: get_field(position, cons.POSITION_ODOMETER_KEY),
#        cons.POSITION_SPEED_KEY: get_field(position, cons.POSITION_SPEED_KEY),
#        cons.CURRENT_STOP_SEQUENCE_KEY: get_field(
#            vehicle, cons.CURRENT_STOP_SEQUENCE_KEY
#        ),
#        cons.CURRENT_STATUS_KEY: get_field(vehicle, cons.CURRENT_STATUS_KEY),
#        cons.TIMESTAMP_KEY: get_field(vehicle, cons.TIMESTAMP_KEY),
#        cons.CONGESTION_LEVEL_KEY: get_field(vehicle, cons.CONGESTION_LEVEL_KEY),
#        cons.STOP_ID_KEY: get_field(vehicle, cons.STOP_ID_KEY),
#        cons.VEHICLE_ID_KEY: get_field(vehicle_info, cons.VEHICLE_ID_KEY),
#        cons.VEHICLE_LABEL_KEY: get_field(vehicle_info, cons.VEHICLE_LABEL_KEY),
#    }
#
#
# def extract_feed_info(feed) -> List[dict]:
#    """
#    Convert a FeedEntity to a dictionary, handling missing fields gracefully.
#
#    Parameters:
#    -----------
#    feed: gtfs_realtime_pb2.Feed
#        The Feed to convert.
#
#    Returns
#    -------
#    List[dict]
#        A list of dictionary representations of the FeedEntities.
#    """
#    header = get_field(feed, cons.HEADER_KEY)
#    list_entity = get_field(feed, cons.ENTITY_KEY)
#    list_dict_entities = [get_dict_from_feed(header, entity) for entity in list_entity]
#
#    return list_dict_entities
#
#
# def get_df_feed(
#    list_dict_entities: List[dict], vehicle_current_timestamp: dict
# ) -> pd.DataFrame:
#    """
#    Convert a list of dictionary representations of FeedEntities to a DataFrame.
#
#    Parameters:
#    -----------
#    list_dict_entities: List[dict]
#        A list of dictionary representations of the FeedEntities.
#
#    vehicle_current_timestamp: dict
#        A dictionary mapping vehicle IDs to their latest timestamps.
#
#    Returns
#    -------
#    df_feed: pd.DataFrame
#        A DataFrame representation of the FeedEntities.
#
#    vehicle_current_timestamp: dict
#        A dictionary mapping vehicle IDs to their latest timestamps.
#    """
#    list_non_duplicates = []
#    for dict_entity in list_dict_entities:
#        vehicle_id = dict_entity[cons.VEHICLE_ID_KEY]
#        cond = vehicle_id not in vehicle_current_timestamp
#        cond |= (
#            vehicle_id in vehicle_current_timestamp
#            and dict_entity[cons.TIMESTAMP_KEY] > vehicle_current_timestamp[vehicle_id]
#        )
#        if cond:
#            list_non_duplicates.append(dict_entity)
#            vehicle_current_timestamp[vehicle_id] = dict_entity[cons.TIMESTAMP_KEY]
#
#    df_feed = pd.DataFrame(list_non_duplicates)
#    return df_feed, vehicle_current_timestamp
#
#
# def format_data(df_feed: pd.DataFrame) -> pd.DataFrame:
#    """
#    Format the DataFrame by converting timestamp columns to datetime.
#
#    Parameters
#    ----------
#    df_feed: pd.DataFrame
#        The DataFrame to format.
#
#    Returns
#    -------
#    pd.DataFrame
#        The formatted DataFrame.
#    """
#    df_feed[cons.TRIP_START_TIMESTAMP_KEY] = pd.to_datetime(
#        df_feed[cons.TRIP_START_DATE_KEY] + " " + df_feed[cons.TRIP_START_TIME_KEY],
#        format=cons.TRIP_START_TIMESTAMP_FORMAT,
#        errors="coerce",
#    )
#    df_feed[cons.FEED_TIMESTAMP_KEY] = pd.to_datetime(
#        df_feed[cons.FEED_TIMESTAMP_KEY], unit=cons.UNIX_TIMESTAMP_UNIT
#    )
#    df_feed[cons.TIMESTAMP_KEY] = pd.to_datetime(
#        df_feed[cons.TIMESTAMP_KEY], unit=cons.UNIX_TIMESTAMP_UNIT
#    )
#    return df_feed
#
#
# def format_data_for_db(df_feed: pd.DataFrame) -> pd.DataFrame:
#    """
#    Format the DataFrame for database insertion by replacing NaN with None.
#
#    Parameters
#    ----------
#    df_feed: pd.DataFrame
#        The DataFrame to format.
#
#    Returns
#    -------
#    pd.DataFrame
#        The formatted DataFrame.
#    """
#
#    process_cols = df_feed.dtypes[df_feed.dtypes == "object"]
#    for col in process_cols.index:
#        df_feed[col] = df_feed[col].apply(lambda x: x if x != "" else None)
#    if cons.TRIP_START_TIME_KEY in df_feed.columns:
#        del df_feed[cons.TRIP_START_TIME_KEY]
#    if cons.TRIP_START_DATE_KEY in df_feed.columns:
#        del df_feed[cons.TRIP_START_DATE_KEY]
#    col_types = {
#        cons.ENTITY_ID_KEY: int,
#        cons.TRIP_ID_KEY: str,
#        cons.TRIP_SCHEDULE_RELATIONSHIP_KEY: int,
#        cons.TRIP_ROUTE_ID_KEY: str,
#        cons.TRIP_DIRECTION_ID_KEY: int,
#        cons.POSITION_LATITUDE_KEY: float,
#        cons.POSITION_LONGITUDE_KEY: float,
#        cons.POSITION_BEARING_KEY: float,
#        cons.POSITION_ODOMETER_KEY: float,
#        cons.POSITION_SPEED_KEY: float,
#        cons.CURRENT_STOP_SEQUENCE_KEY: int,
#        cons.CURRENT_STATUS_KEY: int,
#        cons.CONGESTION_LEVEL_KEY: int,
#        cons.STOP_ID_KEY: str,
#        cons.VEHICLE_ID_KEY: int,
#        cons.VEHICLE_LABEL_KEY: int,
#    }
#    for col, col_type in col_types.items():
#        if col in df_feed.columns:
#            df_feed[col] = df_feed[col].astype(col_type)
#
#    # drop cols with No trip start timestamp
#    df_feed = df_feed[~df_feed[cons.TRIP_START_TIMESTAMP_KEY].isna()].reset_index(
#        drop=True
#    )
#    return df_feed
#
