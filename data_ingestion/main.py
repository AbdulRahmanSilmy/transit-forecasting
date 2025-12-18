"""
Main script for ingesting GTFS-Realtime vehicle updates into MySQL database.
"""
import time
import logging
import yaml
import mysql.connector
from utils import (
    fetch_gtfs_data,
    parse_gtfs_data,
    extract_feed_info,
    get_df_feed,
    format_data,
    format_data_for_db
)
from queries import CREATE_TABLE_QUERY, INSERT_QUERY

CONFIG_PATH = "config.yaml"
with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
    config = yaml.safe_load(file)

REALTIME_VEHICLE_UPDATES_URL = config["ingestion_url"]
CONNECT_PARAMS = config["connection_params"]
LOGGING_FILE = config["logging_file"]

# -----------------------------
# Setup logging
# -----------------------------
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detailed logs
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGGING_FILE),  # log to file
        logging.StreamHandler()  # also print to console
    ]
)

# -----------------------------
# Database connection
# -----------------------------
vehicle_current_timestamp = {}
connect_params = dict(
    **CONNECT_PARAMS
)


try:
    conn = mysql.connector.connect(**connect_params)
    cur = conn.cursor()
    logging.info("Connected to MySQL database.")
    cur.execute(CREATE_TABLE_QUERY)
    conn.commit()
    logging.info("Ensured TRANSIT_DATA table exists.")
except mysql.connector.Error as e:
    logging.error("Failed to connect to MySQL: %e", e)
    raise e


# -----------------------------
# Ingestion loop
# -----------------------------
while True:
    try:
        logging.info("Fetching GTFS feed...")
        data = fetch_gtfs_data(REALTIME_VEHICLE_UPDATES_URL)

        if data:
            feed = parse_gtfs_data(data)
            entities = extract_feed_info(feed)
            logging.info("Feed retrieved with %d entities.", len(feed.entity))

            df_feed, vehicle_current_timestamp = get_df_feed(
                entities, vehicle_current_timestamp)

            if not df_feed.empty:
                df_feed = format_data(df_feed)
                df_feed_db = format_data_for_db(df_feed)

                # Convert DataFrame to list of tuples
                data_to_insert = [tuple(row)[1:]
                                  for row in df_feed_db.itertuples()]

                logging.info(
                    "Inserting %d rows into the database...", len(data_to_insert))
                cur.executemany(INSERT_QUERY, data_to_insert)
                conn.commit()
                logging.info("Insert committed successfully.")
            else:
                logging.info("No new data to insert.")

        else:
            logging.warning("No data received from feed.")

    except mysql.connector.Error as e:
        logging.error("MySQL error: %e. Reconnecting...", e)
        try:
            conn = mysql.connector.connect(**connect_params)
            cur = conn.cursor()
            logging.info("Reconnected to MySQL successfully.")
        except mysql.connector.Error as e2:
            logging.critical("Failed to reconnect: %e", e2)
            time.sleep(10)  # wait before retrying

    except Exception as e:
        logging.error("Unexpected error: %e", e, exc_info=True)
    # Sleep between feed fetches (adjust to feed update interval)
    time.sleep(5)
