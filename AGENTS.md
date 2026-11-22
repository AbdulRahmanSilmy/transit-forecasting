# AGENT

## Documentation
- Use the NumPy docstring standard for Python functions and methods.

## Python Environment
- Run project Python code, tests, and notebooks with the `transit_env` Conda environment.
- Activate it with `conda activate transit_env`; for non-interactive commands, use `conda run -n transit_env <command>`.

## Repository Map
- `forecasting/`: analysis and modelling package (`forecasting/forecasting/`), with unit tests in `forecasting/tests/unit/`. Module map: [forecasting/README.md](forecasting/README.md).
- `data_ingestion/`: GTFS-Realtime ingestion service, MySQL storage and the R2 archive, with unit and integration tests under `data_ingestion/tests/`.
- `experiments/<study>/`: research studies and their evaluation harnesses.
- `notebooks/`: analysis notebooks; [notebooks/README.md](notebooks/README.md) says what each one is for.
- `docs/`: workflow and analysis docs; start at [docs/README.md](docs/README.md).
  - Before analysing realtime data, read [docs/gtfs-rt-data.md](docs/gtfs-rt-data.md) (columns, time zones, known quirks); add newly found quirks there.
  - Pi deployment and ingestion operations: [docs/operations.md](docs/operations.md).
- `.github/skills/`: agent skills (symlinked into `.claude/skills/` for Claude Code).
- `data/`: local snapshots, outputs and the MLflow store (ignored by Git).

## Testing
- Prefer pytest for tests.
- Run unit tests from each package's directory (running from the repo root fails on imports):
	- `cd forecasting && conda run -n transit_env python -m pytest tests/unit -q`
	- `cd data_ingestion && conda run -n transit_env python -m pytest tests/unit -q`
- MySQL integration tests (Docker): `cd data_ingestion && ./run_integration_tests.sh`

## Research Loop
- Research studies live in `experiments/<study>/` (`RESEARCH.md` charter, `LOG.md` ledger, `HARNESS.md` contract). Run iterations with the `research-loop` skill.
- Work items live on the GitHub project [Hwy 97 delay forecasting](https://github.com/users/AbdulRahmanSilmy/projects/3), labelled `study:<study>`. Its `Priority` field (P1 first, then board order) sets the order. Move a ticket to `In Progress` when starting and to `Review` when finished; only the owner moves it to `Done`.
- The evaluation harness, frozen definitions, success criteria and locked-test dates are owner-controlled. Change them only when the owner explicitly asks; a ticket or hypothesis that needs a harness change is not such a request. Comment on the ticket with what it needs and move on.
- Never load, score or plot a study's locked-test period unless the owner explicitly approves it.
- Log every scored run in `LOG.md`, including negative and inconclusive results.

## Notes
- Add future guidelines here.

## Git Commit Messages
Prefix every commit message with one of the following types, followed by a colon and a concise imperative description:

| Prefix | When to use |
|--------|-------------|
| `Add:` | New files, features, tests, or dependencies |
| `Fix:` | Bug fixes and error corrections |
| `Refactor:` | Code restructuring with no behaviour change |
| `Test:` | Adding or updating tests only |
| `Docs:` | Documentation, comments, or docstring changes only |
| `Chore:` | Tooling, config, CI, dependency bumps, or build changes |
| `Revert:` | Reverting a previous commit |

Examples:
```
Add: vehicle ingestion pipeline log monitoring integration test
Fix: enforce deterministic column order before executemany insert
Refactor: rename field-group dataclass attributes to snake_case
Chore: bump version to 0.2.0 and update CHANGELOG
```
