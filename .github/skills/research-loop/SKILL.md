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

1. **Pick** the next ticket from the GitHub project [Hwy 97 delay forecasting](https://github.com/users/AbdulRahmanSilmy/projects/3): open issues labelled `study:<study>` in `Todo`, highest `Priority` first (P1 > P2 > P3, ties in board order), unless the owner named one. List them with `gh project item-list 3 --owner AbdulRahmanSilmy --format json`. Move it to `In Progress`. Restate it as a falsifiable claim.
2. **Predict** the expected direction and rough size of the effect, and write it in the new LOG entry *before* running.
3. **Implement** the smallest change that tests it, in the package (`forecasting/forecasting/`). Add NumPy docstrings and focused unit tests. Follow `forecasting-development`.
4. **Run** `cd forecasting && conda run -n transit_env python -m pytest tests/unit -q`, then (from the repo root) the harness on `--split dev --hypothesis H? --log-id E0NN`. Commit before the scored run so the log's git hash reproduces it. The run is logged to the local MLflow store (see HARNESS.md).
5. **Check** the sanity outputs: leakage assertions, identical row counts, shuffled control. If any fail, stop and report. Do not interpret the scores.
6. **Record** the LOG entry, citing the MLflow run ID. In the summary row and the entry, write the git hash and the MLflow run ID as links: `[<hash>](https://github.com/AbdulRahmanSilmy/transit-forecasting/commit/<hash>)` (push the commit) and `[<run_id>](http://127.0.0.1:5000/#/experiments/1/runs/<run_id>)`. Then include: results for every baseline, Δ vs B0 with CI, Δ vs best baseline, per-fold spread and verdict. Update the backlog status, and add new hypotheses the result suggests.
7. **Decide**: keep / discard / refine / inconclusive. A Δ whose CI spans zero, or that is smaller than the fold-to-fold spread, is **inconclusive**, not a win.
8. **Hand off**: comment on the ticket with the LOG entry ID, commit, MLflow run ID, headline numbers and verdict, then move it to `Review`. Never move tickets to `Done` or close them; the owner does that after reviewing.

## Hard rules

- Never edit the harness files, frozen definitions, success criterion or locked-test dates. Tickets never authorise a harness change; only the owner's own explicit request does. If a ticket can't be tested without one (anchor, dwell rule, chaining, data window), don't build it: comment on the ticket with the harness change it needs, leave it in `Todo`, and move on to the next ticket.
- Never load, score or plot locked-test data. `--split test` is owner-only.
- Never drop rows by outcome to improve a score. Report exclusions.
- Use at most 10 scored runs per session. Stop after 3 consecutive runs with no improvement over the best baseline, and summarise instead.
- Negative results are logged like positive ones.
- Commit messages follow AGENTS.md prefixes, e.g. `Add: pooled harmonic traversal candidate (H4)`.
