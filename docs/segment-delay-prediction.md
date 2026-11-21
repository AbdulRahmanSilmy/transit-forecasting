# Recurring segment delay prediction

The separate [segment delay modelling notebook](../notebooks/route_97_recurring_segment_delay_prediction.ipynb)
tests whether calendar patterns predict signed downstream-minus-upstream arrival
delay changes on Route `97-KEL`, direction `0`. It explicitly replays the
`2026-11-10_2_months` snapshot offline, using each traversal's own **scheduled**
upstream arrival time, local weekday and categorical directed segment identity.
Final-reported arrival delays are proxies that include dwell, holding and
schedule slack, not independently measured road traffic.

Run with the `transit_env` kernel. Install declared model dependencies from the
repository root if needed:

```bash
conda run -n transit_env python -m pip install -e ./forecasting
```

`prepare_segment_delay_data` reuses the audited delay engine, joins exact trip
stop sequences, handles extended GTFS times and DST, and excludes recording-edge
service days. `make_segment_delay_splits` builds **two seven-day expanding
development blocks and a final seven-day holdout**, with a one-service-day
training gap and label-availability/trip-boundary guards. The initially planned
four development weeks were reduced after auditing sparse early static-GTFS
matches; the first retained training block has 22 observed days. Insufficient
coverage raises an error rather than silently changing the design.

`run_segment_delay_experiment` compares L1-loss XGBoost with ordinary
segment/weekday/30-minute arithmetic averages, training-winsorised averages,
robust segment-only averages and zero change. Sparse calendar groups require
five observations across three service days, with explicit segment/time,
segment and global fallbacks. All feature fitting and model selection use
training/development history only; final-test labels are never used for fitting.

MAE, median absolute error, RMSE, R-squared, macro-segment MAE, support and
paired daily/segment improvements describe the result. Unchanged held-out
targets are scored for **all observations**, and separately within
training-only conditional residual bands and their tails (95% and 99%
sensitivity). There are no verified incident labels: these masks are
retrospective diagnostics, not an anomaly detector. Unexpected-delay magnitudes
are downweighted through robust training, not hidden by clipping test outcomes.
Calendar-model improvement beyond fixed segment corrections and consistency
across held-out weeks are more relevant than feature importance alone.

Running the notebook produces tables and figures. `save_segment_delay_experiment`
saves a non-overwriting run under ignored `data/segment_delay_models/`, including
the pre-test model bundle, snapshot manifest/checksums, versions/configuration,
segment mapping, split/audit tables, tuning results, out-of-time predictions
and metrics. Load pickle bundles only from trusted sources. This experiment
does not modify the live delay estimator or the reference notebook.
