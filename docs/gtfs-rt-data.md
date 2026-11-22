# GTFS-Realtime data reference

What the stored realtime data contains, how to read it, and the known quirks
to handle before trusting a number. For loading data, see
[backtest-data.md](backtest-data.md). For the system around it, see
[architecture.md](architecture.md).

## Source

BC Transit publishes GTFS-Realtime for Kelowna under operator ID 47 (URLs in
[data_ingestion/config.yaml](../data_ingestion/config.yaml)):

| Feed | Stored as (MySQL / R2) | Poll interval |
|---|---|---|
| Trip updates: predicted arrival and departure per stop | `TRANSIT_TRIP_TABLE` / `trip_updates` | 5 s |
| Vehicle positions | `TRANSIT_VEHICLE_TABLE` / `vehicle_updates` | 5 s |

The matching static GTFS (timetable, stops, shapes) is downloaded by
`forecasting.download_static_gtfs` and saved with each backtest snapshot. MySQL
and R2 hold the same columns. `load_archive_updates` returns the same dtypes
as a MySQL download.

## What a row is

The ingestion service writes a row only when something has changed, so the
tables are **change logs, not snapshots of every poll**.

- **Trip updates:** one row per `(trip_id, stop_id)` each time any of its
  arrival or departure delay, time or uncertainty changes. A stop whose
  prediction stays the same for an hour has one row for that hour.
- **Vehicle positions:** one row per vehicle each time its `timestamp`
  advances, about every 15 s while in service.

To rebuild what the feed said at time *t*, take each stop's latest row with
`feed_timestamp <= t`. `forecasting.get_trip_updates_for_trip` does this for
vehicle rows (see the `trip-vehicle-update-merging` skill).

The change-tracking state lives in memory, so **every restart of the
ingestion container writes the full current feed again**. This creates
duplicate rows at each restart ([#6](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/6)).
Deduplicate on all value columns if counts matter.

## Columns

Times are naive `TIMESTAMP` values. **Every time column is UTC except
`trip_start_timestamp`, which is local wall time.**

### Trip updates

| Column | Meaning |
|---|---|
| `id` | Auto-increment row ID, in insert order |
| `feed_timestamp` | Feed header time of the publication that carried this row (UTC). The archive is partitioned by this column's UTC day |
| `trip_id` | Static GTFS trip ID. It repeats every service day, so it does not identify a journey on its own |
| `trip_start_timestamp` | Trip start descriptor: the feed's `start_date` + `start_time`, **local** wall time, matching the static timetable. Null for trips starting at or after midnight (see [quirks](#known-quirks)) |
| `trip_route_id`, `trip_direction_id` | Route (for example `97-KEL`) and GTFS direction (0 or 1) |
| `trip_schedule_relationship` | Trip-level status: 0 scheduled, 1 added, 3 cancelled, 7 deleted, 8 new. The hwy97 harness drops 3 and 7 |
| `stop_sequence`, `stop_id` | Position of the stop in the trip, and the static stop ID |
| `schedule_relationship` | Stop-level status: 0 scheduled, 1 skipped, 2 no data. The hwy97 harness drops 1 and 2 |
| `arrival_delay`, `departure_delay` | Predicted (or, after the event, reported) delay in seconds; positive is late |
| `arrival_time`, `departure_time` | Predicted or reported event time (UTC). Scheduled time = event time − delay |
| `arrival_uncertainty`, `departure_uncertainty` | Almost always 0; not informative |

### Vehicle positions

| Column | Meaning |
|---|---|
| `id`, `feed_timestamp` | As above |
| `entity_id` | Feed entity ID |
| `vehicle_id`, `vehicle_label` | Bus identifiers (strings) |
| `trip_id`, `trip_start_timestamp`, `trip_route_id`, `trip_direction_id`, `trip_schedule_relationship` | The trip the bus is assigned to; same meaning as in trip updates |
| `timestamp` | Time the position was measured (UTC); usually 3 to 8 s before `feed_timestamp` |
| `position_latitude`, `position_longitude` | WGS84 position |
| `position_bearing` | Degrees clockwise from north |
| `position_speed` | Metres per second |
| `position_odometer` | Metres; a per-vehicle counter, not reset per trip |
| `current_stop_sequence`, `stop_id` | The stop the bus is at or heading to |
| `current_status` | 0 incoming at, 1 stopped at, 2 in transit to `current_stop_sequence` |
| `congestion_level` | Almost always 1 (running smoothly); not informative |
| `read_timestamp` | When the Pi inserted the row. Normally about 3 s after `feed_timestamp`, but it depends on the Pi's clock; use `feed_timestamp` for analysis |

## Identifying journeys and visits

- **Journey (dated trip):** `trip_id` + `trip_start_timestamp`. The study
  harness writes this as `trip_instance_key = "<trip_id>|<start descriptor>"`.
- **Stop visit:** a journey plus `stop_sequence`.
- **Final report:** a visit's last published row. After the bus has passed,
  this is the feed's record of what happened and is used as truth (with the
  caveats below).
- **Origin publication:** a journey's first published row, about 150 minutes
  before arrival. This is Transit BC's earliest estimate.
- **Trip updates ↔ vehicle positions:** match on `trip_id` +
  `trip_start_timestamp`. 99.3% of route-97 journeys have pings
  ([#12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12)).

## Time zones and clocks

- **Event times are physical.** The header clock agrees with real time to
  within seconds, and at a reported arrival time the bus's GPS position is at
  the stop (median 52 m).
- **British Columbia has kept UTC−7 all year since 2026-03-08.** There was no
  fall-back on 2026-11-01. A timezone database older than tzdata 2026a still
  switches to UTC−8, which makes every local time from Nov 1 onwards look one
  hour early. That bug looked like a feed defect for weeks
  ([#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13)).
  Keep tzdata current in every environment that converts to
  `America/Vancouver`. A unit test in `forecasting` fails if it is stale.
- **Feed clock vs timetable clock.** `forecasting.feed_clock` measures each
  journey's whole-hour offset between its feed schedule (converted to local
  time) and its start descriptor. With a current timezone database the offset
  is 0 for almost every journey. Calendar features keyed to the timetable
  clock stay correct even if a conversion is wrong.
- **Service day:** studies use the local date with a 03:00 cutover, so a bus
  running at 00:30 belongs to the previous day's service.

## Known quirks

Check this list before trusting any result built on the raw tables.

| Quirk | Effect | How it is handled | Reference |
|---|---|---|---|
| **Stale final reports.** The feed sometimes stops updating a stop long before the bus reaches it, so the final report is an old prediction, often a 0 s schedule-only estimate | About 3% of route-97 direction-0 visits have a final report published more than 10 min before the arrival it reports. Scored as truth, they make Transit BC look perfect | The hwy97 harness excludes them as `stale_final_report`; at stops 2–14 none of the checked cases matched vehicle positions | [#12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12) |
| **On-time first-stop departures look stale.** The feed leaves an on-time first-stop departure at its 0 s origin estimate | Looks like the stale case above, but 91% are within 60 s of vehicle positions | The harness keeps them for first-link training (E010) | [#12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12) |
| **Untracked journeys.** Every final arrival and departure delay is exactly 0 s | Schedule-only data, not observations; 93 of 3,336 journeys from Sep 11 to Nov 10, 2026 | Excluded as `untracked_journey` | [#21](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/21) |
| **Feed outage on 2026-11-01.** About one capture every 30 min, all schedule-only predictions, which also applied the old fall-back | The whole day is unusable | Excluded as untracked | [#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13) |
| **Missing start descriptor after midnight.** Trips whose GTFS `start_time` is `24:00:00` or later fail to parse, so `trip_start_timestamp` is null | About 3% of route-97 trip-update rows (trips scheduled 00:00–03:00 local) cannot be matched to a dated journey or to vehicle positions | Not handled yet; these trips drop out of journey-based analyses | `_format_df_feed` in [data_ingestion.py](../data_ingestion/data_ingestion/data_ingestion.py) |
| **Duplicate rows after ingestion restarts** | Repeated rows at each deploy or reboot | Not handled at ingestion | [#6](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/6) |
| **Archive coverage gaps.** Trip updates start on 2026-01-11, February to May 2026 is empty, and route-tagged data starts around June/July 2026 | Long training histories are shorter than the calendar suggests | Check `list_archive_days` before choosing a window | LOG E003 in [experiments/hwy97_delay/LOG.md](../experiments/hwy97_delay/LOG.md) |
| **Stale tzdata** | Local times one hour early from 2026-11-01 | Update tzdata; guard test | [#13](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/13) |

## How good is the final report as truth?

Compared with arrival and departure times measured from vehicle positions over
the hwy97 development window ([#12](https://github.com/AbdulRahmanSilmy/transit-forecasting/issues/12)):

| Final reports that are fresh (published near the event) | Within 30 s | Within 60 s |
|---|---|---|
| Arrivals (n = 36,252) | 97% | 99.9% |
| Departures (n = 38,136) | 88% | 98% |

Fresh final reports are a good proxy for what happened. Stale ones are not.

## Glossary

| Term | Meaning |
|---|---|
| Publication | One feed header (`feed_timestamp`) and the rows it carried |
| Segment, link | The directed path between two consecutive stops of a trip |
| Traversal `T_k` | Time from departing the upstream stop to arriving at the downstream stop; excludes dwell |
| Dwell `W_k` | Time spent at a stop, from arrival to departure |
| Scheduled allowance `S_k` | Scheduled time for the segment, from the timetable |
| Delay dynamics identity | $d_k = d_{k-1} + T_k + W_k - S_k$: delay changes by how much a segment and its dwell run over or under schedule |
| Lead | Time from the forecast's origin publication to the event |
| Timepoint | A stop where buses running early wait for their scheduled time; on route 97 direction 0, stops 6 and 10 show this |
