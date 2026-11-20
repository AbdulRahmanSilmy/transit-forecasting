---
name: research-loop
description: "Use when running one iteration of a research experiment under experiments/<study>/ (e.g. hwy97_delay): pick a hypothesis, implement a candidate, run the frozen evaluation harness on development folds, log the result and recommend the next step."
---

# Research Loop: one iteration

Each study lives in `experiments/<study>/`:

- `RESEARCH.md`: the question, frozen definitions, success criterion, locked test, backlog
- `LOG.md`: append-only experiment ledger
- `HARNESS.md`: the evaluation contract

Read all three before starting.

## Procedure

1. **Pick** the highest-priority `open` backlog item, or the one the owner named. Restate it as a falsifiable claim.
2. **Predict** the expected direction and rough size of the effect, and write it in the new LOG entry *before* running.
3. **Implement** the smallest change that tests it, in the package (`forecasting/forecasting/`). Add NumPy docstrings and focused unit tests. Follow `forecasting-development`.
4. **Run** `conda run -n transit_env pytest forecasting/tests/unit -q`, then the harness on `--split dev`. Commit before the scored run so the log's git hash reproduces it.
5. **Check** the sanity outputs: leakage assertions, identical row counts, shuffled control. If any fail, stop and report. Do not interpret the scores.
6. **Record** the LOG entry: results for every baseline, Δ vs B0 with CI, Δ vs best baseline, per-fold spread and verdict. Update the backlog status, and add new hypotheses the result suggests.
7. **Decide**: keep / discard / refine / inconclusive. A Δ whose CI spans zero, or that is smaller than the fold-to-fold spread, is **inconclusive**, not a win.

## Hard rules

- Never edit the harness files, frozen definitions, success criterion or locked-test dates without asking.
- Never load, score or plot locked-test data. `--split test` is owner-only.
- Never drop rows by outcome to improve a score. Report exclusions.
- Use at most 10 scored runs per session. Stop after 3 consecutive runs with no improvement over the best baseline, and summarise instead.
- Negative results are logged like positive ones.
- Commit messages follow AGENTS.md prefixes, e.g. `Add: pooled harmonic traversal candidate (H4)`.
