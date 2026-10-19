---
name: trip-vehicle-update-merging
description: "Use when joining or aligning GTFS trip_updates with vehicle_updates, implementing get_trip_updates_for_trip, interpreting realtime-to-vehicle matching, or debugging assigned delay and arrival/departure fields."
---

# Merging Trip Updates with Vehicle Updates

Use this skill for the temporal association logic implemented by
`forecasting.get_trip_updates_for_trip`.

## Matching rules

For every vehicle-update row:

1. Consider trip-update rows whose `feed_timestamp` is no later than the
   vehicle-update row's `feed_timestamp`. If `offset` is provided, use the
   cutoff `vehicle feed_timestamp - offset`.
2. Restrict candidates to the same `trip_id` and `stop_id`.
3. Select the first candidate in the filtered input order. In the established
   notebook workflow, trip updates are ordered newest first, so the first
   candidate is the latest eligible update. Do not silently sort a differently
   ordered input and assume it preserves this contract.
4. Copy `arrival_delay`, `departure_delay`, `arrival_time`, and
   `departure_time` onto the vehicle-update row. If no candidate exists, set
   those values to NaN.
5. Return vehicle updates sorted by `feed_timestamp` ascending without
   modifying the caller's input frame.

## Change and test procedure

- Keep this matching behavior in `forecasting/forecasting/trip_updates.py`;
  notebooks should call the package implementation rather than maintain a
  separate copy.
- Test the newest-first selection, timestamp cutoff (including `offset`),
  trip-and-stop matching, unmatched rows, output ordering, and input
  immutability when changing this behavior.
- Be precise about `offset`: it is subtracted from the vehicle timestamp.
  A positive offset therefore makes the cutoff earlier and excludes more
  recent trip updates.
