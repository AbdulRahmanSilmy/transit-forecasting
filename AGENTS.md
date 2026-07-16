# AGENT

## Documentation
- Use the NumPy docstring standard for Python functions and methods.

## Testing
- Prefer pytest for tests.
- For local validation, prefer:
	- `pytest tests/unit -q`
	- `./run_integration_tests.sh`

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
