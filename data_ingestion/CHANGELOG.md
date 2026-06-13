# Changelog

All notable changes to this project are documented in this file.

The format is based on Keep a Changelog and this project uses Semantic Versioning.

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
