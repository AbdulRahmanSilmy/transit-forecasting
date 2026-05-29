# Transit Forecasting

## Overview
Tools and data pipelines for transit forecasting and GTFS-realtime ingestion.

## Development Setup

### Python Environment
Use your preferred Python environment manager (conda, venv). Example with conda:

```
conda create -n transit_env python=3.11
conda activate transit_env
```

Install Python dependencies:

```
pip install -r requirements.txt
```

Use `pytest` for tests.

### Pre-commit Hooks
Install the git hook:

```
pre-commit install
```

## Data Ingestion
The ingestion scripts live under [data_ingestion/](data_ingestion) and use [data_ingestion/config.yaml.example](data_ingestion/config.yaml.example) for configuration.
