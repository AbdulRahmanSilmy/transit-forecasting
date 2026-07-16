# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install third-party dependencies first so source-only edits do not
# invalidate this cached layer.
COPY pyproject.toml ./
RUN python - <<'PY'
import subprocess
import tomllib
from pathlib import Path

data = tomllib.loads(Path('pyproject.toml').read_text())
deps = data.get('project', {}).get('dependencies', [])
if deps:
	subprocess.check_call(['pip', 'install', '--no-cache-dir', *deps])
PY

# Copy application source and install package metadata without reinstalling deps.
COPY data_ingestion/ data_ingestion/
RUN pip install --no-cache-dir --no-deps -e .

CMD ["python", "-m", "data_ingestion"]
