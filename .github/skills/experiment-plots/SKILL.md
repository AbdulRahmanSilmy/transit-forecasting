---
name: experiment-plots
description: "Use before modelling in a research-loop iteration, or for any study diagnostic: build understanding by making one exploratory plot at a time, looking at it, and choosing the next plot from what it shows. Logs every plot and the reasoning trail to MLflow, and selects a few key plots for the reviewer (GitHub issue and LOG.md)."
---

# Experiment plots: look before you model

Exploration is a loop, not a batch. Each plot answers **one** question, and what it shows decides the next plot. The harness's standard figures come later and stay unchanged. These plots are extra.

## When

- **Research loop, step 2 (Explore)**: after picking the ticket and before writing the prediction. The prediction and the design of the change should follow from what the plots showed.
- **Any diagnostic** (truth checks, data defects, surprising results), even without a scored run.

## Data rules

- Only development data: the dev window's raw data, the harness's prepared frames, and earlier runs' artifacts (`predictions.csv.gz`, `traversals.csv.gz` in `data/mlflow/artifacts/<study>/<run_id>/artifacts/`). **Never the locked-test period.**
- Prefer **training-side** data and earlier runs' **residuals** over the current candidate's dev scores. Exploration must not become tuning on dev folds: choose a design from the plots, then score it once.
- Loading the raw window takes minutes. Cache what you need once (Parquet in the session folder), then iterate on the cache.

## The loop

1. **Start a session** (once per experiment). Use a session folder in the scratchpad, not the repo:

   ```python
   from forecasting.exploration_tracking import ExplorationSession
   session = ExplorationSession.start(
       WORKDIR, tracking_dir=REPO / "data/mlflow", experiment="hwy97_delay",
       log_id="E011", hypothesis="H8",
       purpose="One sentence: what the plots must establish before modelling.",
   )
   ```

2. **Ask one question, make one plot**, then log it in a short script that reopens the session:

   ```python
   session = ExplorationSession.load(WORKDIR)
   png = session.log_figure(fig, "bias_by_stop", question="Where along the route does bias build up?")
   ```

3. **Look at the PNG** (Read it) before going on. Then record what it shows and what to do next:

   ```python
   session.note("bias_by_stop", observation="...", next_step="...", key=False)
   ```

   The observation states what the plot shows, with numbers. The next step names the next question, or the design decision the plot settles.

4. **Repeat** until you can write the prediction and the design. Typically 4–8 plots; stop earlier if the picture is clear. If a plot is unreadable or misleading, fix it and log it under a new name (`*_v2`), then note why.

5. **Finish**: `session.finish()`. Cite the exploration run in the LOG entry next to the scored run.

Everything goes to one MLflow run (tag `run_type=exploration`, same experiment as the scored runs): `figures/NN_<name>.png` and `.html`, `journal.md` (question → observation → next for every plot) and `key_figures.json`.

## Plot craft

- Follow the `dataviz` skill for form and colour. Use Plotly (the PNG goes through kaleido) with the palette's categorical slots in fixed order, a recessive grid, a title that states the finding where possible, and a subtitle with n, the window and what's excluded.
- One message per plot. Prefer distributions, per-segment or per-stop breakdowns, and time-of-day profiles over grand averages. Show units (s, min) and say what's clipped.

## Selecting plots for the reviewer

Mark **2–4** figures `key=True`: the ones that explain the decision. Typically one plot that shows the problem, one that shows the mechanism, and one that shows the result or the remaining gap after the scored run.

At hand-off:

1. `session.export_key_figures(REPO / "experiments/<study>/figures/<log_id>")`, commit them with the experiment's docs, and merge as usual.
2. Embed them in the ticket hand-off comment with commit-pinned raw URLs, which keep working after the branch is deleted:
   `![caption](https://raw.githubusercontent.com/AbdulRahmanSilmy/transit-forecasting/<commit>/experiments/<study>/figures/<log_id>/<file>.png)`
   Give each a one-line caption saying what to see. Link the full exploration run in MLflow for the rest.
3. In the LOG entry, add the exploration run ID (linked) and embed the same key figures with relative paths (`figures/<log_id>/<file>.png`).
