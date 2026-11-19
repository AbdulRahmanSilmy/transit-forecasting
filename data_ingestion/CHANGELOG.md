# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project uses Semantic Versioning.

## [0.4.0] - 2026-11-19

### Added
- Added `python -m data_ingestion.nightly` and the `archive` Compose service, which export each complete UTC day to R2 shortly after midnight UTC, catch up missed days, and retry failed runs.
- Added `python -m data_ingestion.retention` with `check`, `prune`, `compact`, and `drop-backup`. Days are removed from MySQL only when their R2 file exists and its row count matches; `compact` rebuilds the tables with only the retention window via `RENAME TABLE` and keeps a backup until `drop-backup --yes`.
- Added `ARCHIVE_RETAIN_DAYS` to enable nightly pruning and `ARCHIVE_RUN_AT_UTC` to set the run time.
- Added an `EXTRAS` build argument to the Dockerfile for optional dependency groups.

### Changed
- `data_ingestion.archive.run_export` now holds the multi-table export loop used by the CLI and the nightly service.
- The Compose `integration_tests` service runs every integration test.

## [0.3.0] - 2026-11-18

### Added
- Added `python -m data_ingestion.archive`, which copies complete UTC days of the realtime tables to Cloudflare R2 as zstd Parquet files (`archive/<table>/date=YYYY-MM-DD/data.parquet`), sorted by route and direction, and verifies each upload's row count.
- Added the optional `archive` dependency group (`duckdb`).

## [0.2.1] - 2026-11-17

### Fixed
- Capped the ingestion log file with a rotating handler (4 x 10 MB) so the long-running service no longer grows it without bound.
- Capped Docker `json-file` logs for every Compose service at 3 x 10 MB.
- Disabled MySQL binary logging, which this single-node setup does not use and which retained several GB of insert history on the Raspberry Pi.

## [0.2.0] - 2026-06-13

### Added
- Added structured dataclass-based field-group mappings for raw GTFS paths and ingestion column names.
- Added ingestion-field coverage validation to ensure raw field definitions stay in sync with ingestion field definitions.
- Added integration coverage for pipeline-style ingestion execution with test-only tables and log verification for both vehicle and trip ingestion.
- Added unit coverage for query formatting and deterministic ingestion-row ordering behavior.

### Changed
- Refactored feed extraction to be driven by dot-delimited paths defined in constants, reducing feed-shape coupling in ingestion subclasses.
- Refactored vehicle and trip ingestion subclasses to rely on shared base parsing behavior and class-level raw/ingestion field mappings.
- Updated constants, ingestion logic, SQL templates, and tests to follow Python naming standards (`snake_case`) for dataclass attributes.
- Updated SQL template formatting to use field-group mapping objects from constants.
- Improved Docker build caching behavior so source-only edits rebuild faster when dependencies are unchanged.
- Updated integration test runner flow to use the existing MySQL test service with isolated test tables.

### Fixed
- Fixed MySQL insert reliability by enforcing deterministic ingestion column ordering before `executemany`, preventing value/column misalignment.
- Improved datetime normalization and handling of invalid/empty timestamp values before database writes.
- Updated unit and integration tests to match refactored constants and ingestion field access patterns.

## [0.1.0] - 2026-06-07

### Added
- Initial GTFS-Realtime ingestion package for transit forecasting.
- Vehicle updates and trip updates ingestion pipelines with MySQL persistence.
- Config-driven ingestion parameters and connection settings.
- SQL templates for table creation and inserts for vehicle/trip tables.
- Docker and Docker Compose setup for MySQL, ingestion service, and integration test execution.
- Baseline unit and integration test suites for config, parsing, formatting, queries, and MySQL round-trip ingestion.
