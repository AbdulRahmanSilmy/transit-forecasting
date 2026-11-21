# Transit Forecasting

## Overview
Tools and data pipelines for transit forecasting and GTFS-realtime ingestion.

| Directory | Contents |
|-----------|----------|
| [data_ingestion/](data_ingestion) | GTFS-realtime ingestion service, MySQL storage and the nightly R2 Parquet archive |
| [forecasting/](forecasting) | `forecasting` package: backtest data loading, congestion engines, delay analyses and models |
| [notebooks/](notebooks) | Analysis notebooks; see the [notebook guide](notebooks/README.md) |
| [experiments/](experiments) | Research studies (charter, log and evaluation harness) |
| [docs/](docs) | Workflow and analysis documentation; see the [docs index](docs/README.md) |
| `data/` | Local snapshots and generated outputs (ignored by Git) |

## Documentation

- [Notebook guide](notebooks/README.md): each analysis's purpose, recommended
  reading order, inputs, outputs and interpretation limits.
- [Historical backtest data](docs/backtest-data.md): offline snapshots, the R2
  archive and MySQL downloads.
- [Database download scaling benchmark](docs/download-benchmark.md)
- Analyses: [congestion maps](docs/congestion-maps.md),
  [stop-delay feasibility](docs/stop-delay-feasibility.md),
  [recurring segment delay prediction](docs/segment-delay-prediction.md),
  [stop dynamics EDA](docs/stop-dynamics-eda.md)
- [Agent research loop](docs/agent-research-loop.md)

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

The root requirements file includes the data ingestion requirements. The
analysis notebooks also need the forecasting package's model dependencies:

```bash
conda run -n transit_env python -m pip install -e ./forecasting
```

### Tests

Use `pytest` for tests. Each package has its own unit tests; run them from the
package directory:

```bash
cd forecasting      # or data_ingestion
conda run -n transit_env python -m pytest tests/unit -q
```

See [Integration Tests In Docker](#integration-tests-in-docker) for the MySQL
ingestion tests.

### Pre-commit Hooks
Install the git hook:

```
pre-commit install
```

## Viewing Experiment Results (MLflow)

Research harness runs (for example
[experiments/hwy97_delay/evaluate.py](experiments/hwy97_delay/evaluate.py)) log
metrics, parameters and figures to a local MLflow store in `data/mlflow/`
(SQLite `mlflow.db` plus `artifacts/`, ignored by Git). Start the MLflow UI
from the repository root:

```bash
conda run --no-capture-output -n transit_env mlflow ui \
  --backend-store-uri sqlite:///data/mlflow/mlflow.db
```

Then open http://127.0.0.1:5000 and pick the study's experiment (such as
`hwy97_delay`). Add `--port <port>` if 5000 is already in use. Stop the server
with `Ctrl+C`. The store only exists after at least one harness run; each
study's `LOG.md` cites the MLflow run ID for every logged result, and its
`HARNESS.md` lists what each run records.

## Data Ingestion
The ingestion scripts live under [data_ingestion/](data_ingestion), and [data_ingestion/config.yaml](data_ingestion/config.yaml) defines the configuration. It pulls environmental variables from a `.env` file. To run the code, use [.env.example](data_ingestion/.env.example) as a reference for what to include in your `.env` file.

### Docker
The Docker Compose file is in [data_ingestion/docker-compose.yml](data_ingestion/docker-compose.yml). Run it from the `data_ingestion` directory:

```bash
cd data_ingestion
docker compose up --build
```

This starts MySQL, phpMyAdmin, and the data ingestion service.

### Integration Tests In Docker
Run integration tests inside Docker so MySQL host resolution matches the Compose network:

```bash
cd data_ingestion
./run_integration_tests.sh
```

This runs `tests/integration/test_mysql_ingestion.py` with `RUN_INTEGRATION_TESTS=1` inside the container and restores MySQL to its original state (stops it afterward only if the script had to start it).
