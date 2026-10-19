---
name: forecasting-development
description: "Use when implementing, debugging, testing, or documenting the forecasting Python package, including transit prediction utilities, feature preparation, model code, and forecasting tests."
---

# Forecasting Development

Use this skill for production-code changes in `forecasting/`.

## Package map

- Python package: `forecasting/forecasting/`
- Package metadata and runtime dependencies: `forecasting/pyproject.toml`
- Notebook and analysis dependencies: `forecasting/requirements.txt`
- Unit tests: `forecasting/tests/unit/`

## Procedure

1. Check for an existing package utility before introducing equivalent notebook logic.
2. Keep reusable forecasting behavior in the package and expose intended public functions from `forecasting/forecasting/__init__.py`.
3. Declare direct runtime dependencies in `pyproject.toml`; keep notebook-only dependencies in `requirements.txt`.
4. Document Python functions with NumPy-style docstrings and preserve input frames unless mutation is an intentional, tested behavior.
5. Add focused pytest coverage for normal, boundary, and missing-data cases.
6. Run tests with the `transit_env` Conda environment from the forecasting project directory, for example:
   `cd forecasting && conda run -n transit_env python -m pytest tests/unit -q`
