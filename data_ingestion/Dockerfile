# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
COPY config.yaml .
COPY data_ingestion/ data_ingestion/
RUN pip install --no-cache-dir .

CMD ["python", "-m", "data_ingestion"]
