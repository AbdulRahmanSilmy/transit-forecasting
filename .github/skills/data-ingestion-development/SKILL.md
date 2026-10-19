---
name: data-ingestion-development
description: "Use when implementing, debugging, testing, or documenting the GTFS-Realtime data_ingestion package, including feed parsing, database persistence, configuration, SQL, and ingestion tests."
---

# Data Ingestion Development

Use this skill for changes in `data_ingestion/`.

## Package map

- Python package and CLI: `data_ingestion/data_ingestion/`
- SQL templates: `data_ingestion/data_ingestion/sql/`
- Unit tests: `data_ingestion/tests/unit/`
- MySQL integration tests: `data_ingestion/tests/integration/`
- Package metadata and dependencies: `data_ingestion/pyproject.toml`

## Procedure

1. Trace the relevant path from feed retrieval and parsing through persistence before changing behavior.
2. Reuse the existing configuration, constants, SQL-template, and logging patterns.
3. Keep SQL changes in the package's `sql/` templates and update focused unit tests for related query behavior.
4. Update both the package implementation and its tests; avoid editing notebook copies as the canonical implementation.
5. Run focused tests with the `transit_env` Conda environment, for example:
   `conda run -n transit_env pytest data_ingestion/tests/unit -q`
6. Run MySQL integration tests only when the change needs database-level verification and the configured test database is available.
