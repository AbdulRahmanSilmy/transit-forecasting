"""
Main script for ingesting GTFS-Realtime vehicle updates into MySQL database.

This script fetches GTFS-Realtime data from a specified URL at regular intervals, processes the data,
and inserts it into a MySQL database table. It includes error handling and logging for monitoring the ingestion process.

This script uses configuration parameters defined in a YAML file for database connection and data ingestion settings.
"""

import argparse
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


class DataIngestion():
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
    def __init__(self, connection_params: dict, data_ingestion_params: dict):
        self.connection_params = connection_params
        self.data_ingestion_params = data_ingestion_params
        self._vehicle_current_timestamp = {}

        self._check_parameters()
        self.fetch_delay_seconds = self.data_ingestion_params[DIP_FETCH_DELAY_SECONDS_KEY]
        self.connection_retry_delay_seconds = self.data_ingestion_params[DIP_CONNECTION_RETRY_DELAY_SECONDS_KEY]

    
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
                logging.info("Connected to MySQL database.")
                connection_state = True
            except mysql.connector.Error:
                logging.exception("Failed to connect to MySQL")
                time.sleep(self.connection_retry_delay_seconds)
        
        return conn, cur

    def _create_table_if_not_exists(self, conn, cur):
        """
        Creates the TRANSIT_DATA table if it does not already exist.

        Parameters
        ----------
        conn : mysql.connector.connection.MySQLConnection
            Active MySQL connection object.

        cur : mysql.connector.cursor.MySQLCursor
            Cursor object for executing queries.
        """
        cur.execute(CREATE_TABLE_QUERY)
        conn.commit()
        logging.info("Ensured TRANSIT_DATA table exists.")

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
            entities = extract_feed_info(feed)
            logging.info("Feed retrieved with %d entities.", len(feed.entity))

            df_feed, self._vehicle_current_timestamp = get_df_feed(
                entities, self._vehicle_current_timestamp)

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

    def run_ingestion_loop(self):
        """
        Starts the continuous data ingestion loop. Continually fetches data from the GTFS-Realtime feed,
        processes it, and inserts it into the MySQL database.
        """
        conn, cur = self._get_sql_connection()
        self._create_table_if_not_exists(conn, cur)

        while True:
            try:
                logging.info("Fetching GTFS feed...")
                data = fetch_gtfs_data(self.data_ingestion_params[DIP_INGESTION_URL_KEY])
                self._process_and_insert_data(data, conn, cur)

            except mysql.connector.Error:
                logging.exception("MySQL error, will attempt reconnection.")
                conn, cur = self._get_sql_connection()

            #pylint: disable=broad-except
            except Exception:
                logging.exception("Unexpected error.")
            # Sleep between feed fetches (adjust to feed update interval)
            time.sleep(self.fetch_delay_seconds)

def main(config_path: str = CONFIG_PATH):
    """
    Main function to run the data ingestion process.

    Parameters
    ----------
    config_path : str
        Path to the configuration YAML file.
    """

    # Load configuration
    with open(config_path, 'r', encoding='utf-8') as file:
        config = yaml.safe_load(file)

    connection_params = dict(**config[CONNECTION_PARAMS_KEY])
    data_ingestion_params = dict(**config[DATA_INGESTION_PARAMS_KEY])
    logging_file = config[LOGGING_FILE_KEY]


    # Setup logging
    logging.basicConfig(
        level=logging.INFO,  # Change to DEBUG for more detailed logs
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(logging_file),  # log to file
            logging.StreamHandler()  # also print to console
        ]
    )

    # Database connection
    data_ingestion = DataIngestion(
        connection_params=connection_params,
        data_ingestion_params=data_ingestion_params
    )

    data_ingestion.run_ingestion_loop()


if __name__ == "__main__":
    # get config path from command line args if provided
    parser = argparse.ArgumentParser(description="Run the data ingestion script.")
    parser.add_argument(
        "--config",
        type=str,
        default=CONFIG_PATH,
        help="Path to the configuration YAML file"
    )
    args = parser.parse_args()

    main(config_path=args.config)
   