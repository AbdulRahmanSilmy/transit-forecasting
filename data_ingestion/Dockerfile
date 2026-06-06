# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
COPY data_ingestion/ data_ingestion/
RUN pip install .

CMD ["python", "-m", "data_ingestion"]
