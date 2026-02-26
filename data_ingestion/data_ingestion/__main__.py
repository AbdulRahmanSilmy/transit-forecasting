"""
Main script for ingesting GTFS-Realtime vehicle updates into MySQL database.

This script fetches GTFS-Realtime data from a specified URL at regular 
intervals, processes the data, and inserts it into a MySQL database table. 
It includes error handling and logging for monitoring the ingestion process.

This script uses configuration parameters defined in a YAML file for database 
connection and data ingestion settings.
"""
import argparse
import logging
import threading
import yaml

from .data_ingestion import (
    CONFIG_PATH,
    CONNECTION_PARAMS_KEY,
    DATA_INGESTION_PARAMS_KEY,
    LOGGING_FILE_KEY,
    VehicleUpdatesDataIngestion,
    TripUpdatesDataIngestion
)

logger = logging.getLogger(__name__)


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

    vu_config = config.get("vehicle_updates")
    vu_connection_params = dict(**vu_config[CONNECTION_PARAMS_KEY])
    vu_data_ingestion_params = dict(**vu_config[DATA_INGESTION_PARAMS_KEY])

    tu_config = config.get("trip_updates")
    tu_connection_params = dict(**tu_config[CONNECTION_PARAMS_KEY])
    tu_data_ingestion_params = dict(**tu_config[DATA_INGESTION_PARAMS_KEY])

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
    logger.info("Starting Data Ingestion")

    # Database connection
    vu_data_ingestion = VehicleUpdatesDataIngestion(
        connection_params=vu_connection_params,
        data_ingestion_params=vu_data_ingestion_params
    )
    tu_data_ingestion = TripUpdatesDataIngestion(
        connection_params=tu_connection_params,
        data_ingestion_params=tu_data_ingestion_params
    )

    # run ingestion loops concurrently
    threading.Thread(target=vu_data_ingestion.run_ingestion_loop).start()
    threading.Thread(target=tu_data_ingestion.run_ingestion_loop).start()


if __name__ == "__main__":
    # get config path from command line args if provided
    parser = argparse.ArgumentParser(
        description="Run the data ingestion script.")
    parser.add_argument(
        "--config",
        type=str,
        default=CONFIG_PATH,
        help="Path to the configuration YAML file"
    )
    args = parser.parse_args()

    main(config_path=args.config)
