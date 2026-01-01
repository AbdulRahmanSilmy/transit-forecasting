"""
Utility functions for GTFS real-time data ingestion and processing.

TODO:
-------
- Improve format data for db function 
- Replace print statements with logging
"""

from google.transit import gtfs_realtime_pb2
import requests
from requests.exceptions import Timeout, ConnectionError, HTTPError
import pandas as pd
from typing import List


def fetch_gtfs_data(url: str) -> bytes | None:
    """
    Fetch GTFS real-time data from the given URL.
    Returns None if the request fails or times out.
    """
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()  # Raise exception for 4xx/5xx responses
        return response.content
    except (Timeout, ConnectionError):
        print("Network error or timeout occurred while fetching GTFS data.")
    except HTTPError as e:
        print(f"HTTP error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")
    
    return None

def parse_gtfs_data(data: bytes) -> gtfs_realtime_pb2.FeedMessage | None:
    """
    Parse GTFS real-time data from bytes.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
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
    }
def extract_feed_info(feed: gtfs_realtime_pb2.FeedEntity) -> List[dict]:
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

def get_df_feed(list_dict_entities: List[dict], vehicle_current_timestamp: dict) -> pd.DataFrame:
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
        vehicle_id = dict_entity['vehicle_id']
        cond = vehicle_id not in vehicle_current_timestamp
        cond |= (vehicle_id in vehicle_current_timestamp and\
                 dict_entity['timestamp'] > vehicle_current_timestamp[vehicle_id])
        if cond:
            list_non_duplicates.append(dict_entity)
            vehicle_current_timestamp[vehicle_id] = dict_entity['timestamp']

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
    df_feed['trip_start_timestamp'] = pd.to_datetime(
        df_feed['trip_start_date'] + ' ' + df_feed['trip_start_time'], 
        format='%Y%m%d %H:%M:%S', errors='coerce'
    )
    df_feed['feed_timestamp'] = pd.to_datetime(df_feed['feed_timestamp'], unit='s')
    df_feed['timestamp'] = pd.to_datetime(df_feed['timestamp'], unit='s')
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

    process_cols = df_feed.dtypes[df_feed.dtypes==object]
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

    #drop cols with No trip start timestamp
    df_feed = df_feed[~df_feed['trip_start_timestamp'].isna()].reset_index(drop=True)
    return df_feed