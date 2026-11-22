## Summary
<!-- What changes and why. Link the issue: "Closes #N" only for issues the owner has agreed to close; otherwise "Part of #N". -->

## Checks
- [ ] Commit messages use a type prefix (`Add:`, `Fix:`, `Refactor:`, `Test:`, `Docs:`, `Chore:`, `Revert:`)
- [ ] Unit tests pass for each package touched (`cd <package> && conda run -n transit_env python -m pytest tests/unit -q`)
- [ ] Integration tests pass (`data_ingestion/run_integration_tests.sh`), if MySQL behaviour changed
- [ ] Docs updated: package README, `docs/`, and `docs/gtfs-rt-data.md` for any new data quirk

### Research studies (delete if not applicable)
- [ ] Every scored run has a `LOG.md` entry citing its MLflow run ID and commit, including negative results
- [ ] The issue's **Experiment runs** section links each new entry
- [ ] No change to the harness, frozen definitions, success criterion or locked-test dates, unless the owner asked for it (say where)
- [ ] The locked-test period was not loaded, scored or plotted

### Data ingestion (delete if not applicable)
- [ ] Deployment needs: rebuild of `data_ingestion` / `archive`, new `.env` settings, schema change (describe)
