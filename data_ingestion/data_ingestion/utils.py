"""
Utility functions for GTFS real-time data ingestion and processing.

TODO:
-------
- Improve format data for db function
- Add documentation for all functions
- Replace print statements with logging
"""

import logging
from typing import List

import pandas as pd
import requests
from google.transit import gtfs_realtime_pb2
from requests.exceptions import Timeout

from .constants import (
    CONGESTION_LEVEL_KEY,
    CURRENT_STATUS_KEY,
    CURRENT_STOP_SEQUENCE_KEY,
    ENTITY_ID_KEY,
    FEED_TIMESTAMP_KEY,
    POSITION_BEARING_KEY,
    POSITION_LATITUDE_KEY,
    POSITION_LONGITUDE_KEY,
    POSITION_ODOMETER_KEY,
    POSITION_SPEED_KEY,
    SCHEDULE_RELATIONSHIP_KEY,
    STOP_ID_KEY,
    TIMESTAMP_KEY,
    TRIP_DIRECTION_ID_KEY,
    TRIP_ID_KEY,
    TRIP_ROUTE_ID_KEY,
    TRIP_SCHEDULE_RELATIONSHIP_KEY,
    TRIP_START_DATE_KEY,
    TRIP_START_TIME_KEY,
    VEHICLE_ID_KEY,
    VEHICLE_LABEL_KEY,
)

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


def get_dict_from_feed(header, entity) -> dict:
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
    dict
        A dictionary representation of the FeedEntity.
    """
    vehicle = get_field(entity, "vehicle")
    trip = get_field(vehicle, "trip")
    position = get_field(vehicle, "position")
    vehicle_info = get_field(vehicle, "vehicle")

    return {
        FEED_TIMESTAMP_KEY: get_field(header, TIMESTAMP_KEY),
        ENTITY_ID_KEY: get_field(entity, "id"),
        TRIP_ID_KEY: get_field(trip, TRIP_ID_KEY),
        TRIP_START_TIME_KEY: get_field(trip, TRIP_START_TIME_KEY),
        TRIP_START_DATE_KEY: get_field(trip, TRIP_START_DATE_KEY),
        TRIP_SCHEDULE_RELATIONSHIP_KEY: get_field(trip, SCHEDULE_RELATIONSHIP_KEY),
        TRIP_ROUTE_ID_KEY: get_field(trip, TRIP_ROUTE_ID_KEY),
        TRIP_DIRECTION_ID_KEY: get_field(trip, TRIP_DIRECTION_ID_KEY),
        POSITION_LATITUDE_KEY: get_field(position, POSITION_LATITUDE_KEY),
        POSITION_LONGITUDE_KEY: get_field(position, POSITION_LONGITUDE_KEY),
        POSITION_BEARING_KEY: get_field(position, POSITION_BEARING_KEY),
        POSITION_ODOMETER_KEY: get_field(position, POSITION_ODOMETER_KEY),
        POSITION_SPEED_KEY: get_field(position, POSITION_SPEED_KEY),
        CURRENT_STOP_SEQUENCE_KEY: get_field(vehicle, CURRENT_STOP_SEQUENCE_KEY),
        CURRENT_STATUS_KEY: get_field(vehicle, CURRENT_STATUS_KEY),
        TIMESTAMP_KEY: get_field(vehicle, TIMESTAMP_KEY),
        CONGESTION_LEVEL_KEY: get_field(vehicle, CONGESTION_LEVEL_KEY),
        STOP_ID_KEY: get_field(vehicle, STOP_ID_KEY),
        VEHICLE_ID_KEY: get_field(vehicle_info, VEHICLE_ID_KEY),
        VEHICLE_LABEL_KEY: get_field(vehicle_info, VEHICLE_LABEL_KEY),
    }


def extract_feed_info(feed) -> List[dict]:
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
    list_dict_entities = [get_dict_from_feed(header, entity) for entity in list_entity]

    return list_dict_entities


def get_df_feed(
    list_dict_entities: List[dict], vehicle_current_timestamp: dict
) -> pd.DataFrame:
    """
    Convert a list of dictionary representations of FeedEntities to a DataFrame.

    Parameters:
    -----------
    list_dict_entities: List[dict]
        A list of dictionary representations of the FeedEntities.

    vehicle_current_timestamp: dict
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
        vehicle_id = dict_entity["vehicle_id"]
        cond = vehicle_id not in vehicle_current_timestamp
        cond |= (
            vehicle_id in vehicle_current_timestamp
            and dict_entity["timestamp"] > vehicle_current_timestamp[vehicle_id]
        )
        if cond:
            list_non_duplicates.append(dict_entity)
            vehicle_current_timestamp[vehicle_id] = dict_entity["timestamp"]

    df_feed = pd.DataFrame(list_non_duplicates)
    return df_feed, vehicle_current_timestamp


def format_data(df_feed: pd.DataFrame) -> pd.DataFrame:
    """
    Format the DataFrame by converting timestamp columns to datetime.

    Parameters
    ----------
    df_feed: pd.DataFrame
        The DataFrame to format.

    Returns
    -------
    pd.DataFrame
        The formatted DataFrame.
    """
    df_feed["trip_start_timestamp"] = pd.to_datetime(
        df_feed["trip_start_date"] + " " + df_feed["trip_start_time"],
        format="%Y%m%d %H:%M:%S",
        errors="coerce",
    )
    df_feed["feed_timestamp"] = pd.to_datetime(df_feed["feed_timestamp"], unit="s")
    df_feed["timestamp"] = pd.to_datetime(df_feed["timestamp"], unit="s")
    return df_feed


def format_data_for_db(df_feed: pd.DataFrame) -> pd.DataFrame:
    """
    Format the DataFrame for database insertion by replacing NaN with None.

    Parameters
    ----------
    df_feed: pd.DataFrame
        The DataFrame to format.

    Returns
    -------
    pd.DataFrame
        The formatted DataFrame.
    """

    process_cols = df_feed.dtypes[df_feed.dtypes == "object"]
    for col in process_cols.index:
        df_feed[col] = df_feed[col].apply(lambda x: x if x != "" else None)
    if "trip_start_time" in df_feed.columns:
        del df_feed["trip_start_time"]
    if "trip_start_date" in df_feed.columns:
        del df_feed["trip_start_date"]
    col_types = {
        "entity_id": int,
        "trip_id": str,
        "trip_schedule_relationship": int,
        "trip_route_id": str,
        "trip_direction_id": int,
        "position_latitude": float,
        "position_longitude": float,
        "position_bearing": float,
        "position_odometer": float,
        "position_speed": float,
        "current_stop_sequence": int,
        "current_status": int,
        "congestion_level": int,
        "stop_id": str,
        "vehicle_id": int,
        "vehicle_label": int,
    }
    for col, col_type in col_types.items():
        if col in df_feed.columns:
            df_feed[col] = df_feed[col].astype(col_type)

    # drop cols with No trip start timestamp
    df_feed = df_feed[~df_feed["trip_start_timestamp"].isna()].reset_index(drop=True)
    return df_feed
