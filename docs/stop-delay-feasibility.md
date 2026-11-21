# Stop-delay congestion feasibility analysis

The [road-segment congestion notebook](../notebooks/route_97_speed_congestion_analysis.ipynb)
includes an offline EDA comparison at the selected stop, direction 0. It contrasts
BC Transit's remaining prediction error with historically available congestion
on each trip's own upstream path, including the incoming segment to the stop.
Example dashboards are saved as local HTML files under `notebooks/visuals/`.
The package also provides cohort residual plots, error distributions, horizon
associations, and coverage/robustness diagnostics. No delay model is fitted.

`prepare_stop_congestion_analysis` preserves visit indices and returns ordered
paths, bin provenance, segment comparisons, scoped summaries, and audit counts.
Only completed engine bins whose contributing probes were published/received
by the cutoff are eligible. Measurement-age limits mask stale data; missing
segments never become zero congestion. Remaining paths require a fresh
same-trip-instance vehicle position and respect its stop sequence/status.
Means describe observed segments only, alongside explicit path coverage.
`summarize_stop_congestion_signal` reports descriptive Spearman associations,
paired counts, and common-visit, complete-coverage, and external-probe subsets.
The display helpers produce the example and cohort Plotly figures.
The analysis also retains `reference_horizons` for every original visit, with
`path_available` and `path_exclusion_reason`. Selecting a known excluded visit
shows its own delay/reference curves and a warning with unavailable congestion
panels, rather than crashing or substituting another visit. Filter the reference
table by `path_available` to find eligible indices for a full dashboard.

**Feasibility limitation:** the engine's full-period median speed baselines
are intentionally unchanged and can use observations later than the evaluation.
This is not a fully leakage-free real-time backtest. A real-time implementation
needs prior-only baselines, trip-update receipt timestamps, and held-out-day
validation. The final reported arrival/delay is a reference proxy, not measured
ground truth; descriptive association does not demonstrate forecast improvement.
Missing static trip/shape matches are warned about and excluded, never mapped
to another trip or shape.
