# Congestion maps

## Congestion via arrival-delay changes

The [delay-based notebook](../notebooks/route_97_delay_congestion_and_propagation.ipynb)
reuses the original notebook's cached-backtest workflow for Route `97-KEL`
and direction `0`, without changing the speed-based analysis. Select the
saved snapshot explicitly with `backtest_dataset_id` in the notebook.
`run_delay_congestion_engine` subtracts the latest reported upstream
`arrival_delay` from the downstream value within the same trip instance.
Positive seconds mean added delay, zero means no additional delay, and negative
seconds mean recovery relative to schedule. Existing lateness is not itself a
segment congestion signal. These are final reported **proxies**, not independently
observed arrivals or pure road traffic: dwell, holding, schedule slack, and
prediction revisions can influence the difference.

The engine reuses directed, shape-specific GTFS segment geometry. It keeps one
latest row per trip instance/stop/sequence (publication time, then greatest
database ID; first input row otherwise), preserving missing latest estimates.
`get_latest_stop_delays` also supports omitting `stop_name` to select all route
stops; unknown stop IDs are retained for auditing. Missing trip starts are
explicitly excluded from the delay engine to avoid ambiguous daily/midnight
pairing. Missing, unmatched, unfinished, and temporally reversed endpoints are
audited, not substituted or bridged across missing stops.

`DelayCongestionResult` exposes `stop_visits`, candidate `traversals`,
`congestion`, `audit`, and segment geometry. Each usable traversal contributes
once to its downstream proxy arrival's five-minute UTC bin. Bins contain median
signed delay change, distinct traversal count, spread, and sign shares. Single
traversals are low-support observations; their standard deviation is undefined.
Unobserved segments remain unknown rather than receiving invented zero values.

`prepare_delay_congestion_snapshots` waits for every contributing endpoint's
publication/receipt and proxy crossing before exposing a final bin. Missing
trip-update receipt timestamps use publication time with an explicit warning;
the legacy snapshot therefore supports publication-time, not exact receipt-time,
replay. Late backfilled bins cannot replace newer crossed bins. The provisional
15-minute expiry measures age from the last contributing proxy arrival, not
publication. Optional trailing smoothing uses available actual bins once and
never extends freshness or pools shapes. Equivalent directed geometries use
the freshest shape-specific source with deterministic segment-ID tie breaking.

`make_delay_congestion_map` shows signed seconds, with green for recovery,
yellow for zero, red for accumulation, and grey for stale/unknown readings.
The symmetric colour saturation limit is visual only; hover values are not
clipped. The notebook includes a trip decomposition, raw/smoothed animations,
and an exact-shape segment time series with retrospective crossing-bin counts.
The latest map stays anchored at the recording end even when coverage has
expired; it never silently substitutes an earlier good reading.

## Speed-based congestion replay

The notebook keeps two separate speed-comparison plots. The original shows
time-binned mean probe speeds against the engine's median individual probe-speed
baseline. Select its segment with `probe_speed_segment_id`; a marker represents
a time bin, not an individual bus.

The per-bus plot uses `get_segment_traversal_speeds` to show one marker
per completed bus/trip-instance traversal, at its final reported downstream
arrival. Select its segment independently with `speed_segment_id`.
Speed is the segment's shape-path distance divided by the time from
upstream departure to downstream arrival, excluding upstream dwell. Hover labels
identify the bus, trip instance, path distance, and travel time. Missing final
times, ambiguous bus identities, and unfinished traversals are excluded with
warnings, never replaced with older predictions or instantaneous speed averages.
The comparison baseline is the median of these full-traversal speeds for the
selected directed segment and shape; it does not change the congestion engine's
probe-speed baseline. Both stop-time estimates and shape-derived distances are
proxies, and the full-period traversal median is not a past-only forecast.

`prepare_congestion_snapshots` selects the latest already-observed congestion
estimate at each requested UTC timestamp. An aggregate becomes available at its
last contributing vehicle probe, not at the start of its five-minute bin.
History preceding the display window can initialize a frame, but estimates expire
after `max_age` (inclusive). The notebook's provisional default is 15 minutes;
calibrate this against actual observation gaps. Full-history engine speed
baselines are unchanged, so this visualization is not a causal baseline backtest.

`make_congestion_map` uses the same rendering for static and animated snapshots.
The latest map and final raw animation frame share their timestamp and freshness
settings. Hover labels show the actual observation time and age; an old estimate
is never described as current. Neither function modifies engine results.

For display only, identical geometries sharing the same direction and directed
stop pair are represented once. The freshest eligible shape-specific estimate
wins; equal observation times are resolved by the lowest engine segment ID.
Hover labels retain group membership and the selected source segment/shape.
Missing or inactive duplicates cannot cover an observed road section. Engine
values and baselines remain separate: different directions, stop pairs, or paths
are never combined, even when some of their geometry overlaps.

Hover states distinguish fresh estimates, expired estimates, no matched probes
yet, matched probes without an eligible congestion baseline, and inactive service
variants. Schedule activity comes from `get_congestion_service_activity`, using
the selected snapshot's agency timezone, weekly calendar, dated additions and
removals, and GTFS times beyond 24:00. Counts indicate trips whose scheduled
interval overlaps the selected period, not the exact time a bus crosses each
segment. Missing or invalid schedule metadata raises an error rather than
silently labeling service inactive. Without schedule classification, hover text
explicitly says activity was not evaluated.

The notebook displays the original per-segment activity table for inspection,
including inactive duplicate shapes hidden by an observed equivalent road.
Static GTFS segments are retained even when inactive in the selected period;
actual observations always take precedence over schedule-based status.

Set `smoothing_window` on `prepare_congestion_snapshots` to smooth actual
segment/bin estimates with a trailing time window before selecting display
frames. Each observation contributes once; repeated carried-forward frames do
not add weight. The window is open on the left and closed at the observation
timestamp. The notebook uses 50 minutes rather than a centered 10-frame mean.
Smoothing is performed separately for each engine segment, before equivalent
paths choose their freshest source, and never advances observation timestamps,
extends expiry, or consumes future observations. Historical observations before
the visible window still contribute when eligible. `raw_congestion_index`
retains the selected raw value while `congestion_index` contains the displayed,
freshness-masked value. The latest raw map remains identical to the final raw
animation frame; smoothed values are intentionally different.
