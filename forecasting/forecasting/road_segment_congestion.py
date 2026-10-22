"""Compute route-level road segment congestion from vehicle updates."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import substring

_VEHICLE_UPDATE_COLUMNS = (
    "trip_route_id",
    "feed_timestamp",
    "position_speed",
    "current_stop_sequence",
    "vehicle_id",
)
_STOP_COLUMNS = ("stop_id", "stop_name", "stop_lat", "stop_lon")
_STOP_TIME_COLUMNS = ("trip_id", "stop_id", "stop_sequence")
_TRIP_COLUMNS = ("route_id", "trip_id", "shape_id")
_SHAPE_COLUMNS = ("shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon")
_CONGESTION_COLUMNS = (
    "segment_id",
    "time_bin",
    "observed_speed",
    "baseline_speed",
    "congestion_index",
)


@dataclass(frozen=True)
class CongestionResult:
    """Outputs from the route segment congestion engine.

    Attributes
    ----------
    route_id : str
        GTFS route identifier processed by the engine.
    representative_trip_id : str
        Trip with the most stop-time records for the route, used to build the
        route's representative shape and stop sequence.
    route_geometry : shapely.geometry.LineString
        Representative route shape in longitude/latitude coordinates.
    segments : pandas.DataFrame
        Consecutive stop pairs, their stop metadata, and sliced route
        geometries.
    vehicle_probes : pandas.DataFrame
        Valid vehicle updates matched to a segment, including the UTC time
        bin used for aggregation.
    baseline_speeds : pandas.DataFrame
        Median historical probe speed for each segment.
    congestion : pandas.DataFrame
        Per-segment, per-time-bin mean speed, baseline speed, and congestion
        index. The index is clipped to the inclusive range from 0 to 1.
    """

    route_id: str
    representative_trip_id: str
    route_geometry: LineString
    segments: pd.DataFrame
    vehicle_probes: pd.DataFrame
    baseline_speeds: pd.DataFrame
    congestion: pd.DataFrame


def _require_columns(
    frame: pd.DataFrame, required: tuple[str, ...], frame_name: str
) -> None:
    """Raise a clear error when a required input column is absent.

    Parameters
    ----------
    frame : pandas.DataFrame
        Input frame to validate.
    required : tuple[str, ...]
        Required column names.
    frame_name : str
        Name used to identify the frame in error messages.

    Raises
    ------
    ValueError
        If one or more required columns are missing.
    """
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{frame_name} is missing required columns: {', '.join(missing)}"
        )


def _build_route_segments(
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    df_shapes: pd.DataFrame,
    route_id: str,
) -> tuple[str, LineString, pd.DataFrame]:
    """Build stop-to-stop geometries for a route.

    Parameters
    ----------
    df_stops : pandas.DataFrame
        GTFS stops table.
    df_stop_times : pandas.DataFrame
        GTFS stop times table.
    df_trips : pandas.DataFrame
        GTFS trips table.
    df_shapes : pandas.DataFrame
        GTFS shapes table.
    route_id : str
        Route identifier to process.

    Returns
    -------
    tuple[str, shapely.geometry.LineString, pandas.DataFrame]
        Representative trip ID, route geometry, and a DataFrame containing
        the geometry and stop metadata for each consecutive stop pair.

    Raises
    ------
    ValueError
        If route metadata does not contain enough valid stops or shape
        coordinates to construct segments.
    """
    _require_columns(df_stops, _STOP_COLUMNS, "df_stops")
    _require_columns(df_stop_times, _STOP_TIME_COLUMNS, "df_stop_times")
    _require_columns(df_trips, _TRIP_COLUMNS, "df_trips")
    _require_columns(df_shapes, _SHAPE_COLUMNS, "df_shapes")

    route_trips = df_trips.loc[
        df_trips["route_id"].astype("string").eq(route_id)
    ].copy()
    if route_trips.empty:
        raise ValueError(f"No GTFS trips were found for route {route_id!r}.")

    stop_times = df_stop_times.copy()
    stop_times["trip_id"] = stop_times["trip_id"].astype("string")
    route_trip_ids = route_trips["trip_id"].astype("string")
    trip_stop_counts = (
        stop_times.loc[stop_times["trip_id"].isin(route_trip_ids)]
        .groupby("trip_id")
        .size()
    )
    if trip_stop_counts.empty:
        raise ValueError(f"No stop times were found for route {route_id!r}.")

    representative_trip_id = str(trip_stop_counts.idxmax())
    representative_trip = route_trips.loc[
        route_trips["trip_id"].astype("string").eq(representative_trip_id)
    ]
    shape_ids = representative_trip["shape_id"].dropna().astype("string")
    if shape_ids.empty:
        raise ValueError(
            f"No shape ID was found for representative trip "
            f"{representative_trip_id!r}."
        )
    representative_shape_id = shape_ids.iloc[0]

    shape_points = df_shapes.loc[
        df_shapes["shape_id"].astype("string").eq(representative_shape_id)
    ].copy()
    if shape_points.empty:
        raise ValueError(
            f"No shape points were found for shape {representative_shape_id!r}."
        )
    for column in ("shape_pt_sequence", "shape_pt_lon", "shape_pt_lat"):
        shape_points[column] = pd.to_numeric(shape_points[column], errors="raise")
    if (
        shape_points[["shape_pt_sequence", "shape_pt_lon", "shape_pt_lat"]]
        .isna()
        .any()
        .any()
    ):
        raise ValueError(
            f"Shape {representative_shape_id!r} contains missing coordinates."
        )
    shape_points = shape_points.sort_values("shape_pt_sequence")
    if len(shape_points) < 2:
        raise ValueError(
            f"Shape {representative_shape_id!r} must contain at least two points."
        )

    route_geometry = LineString(
        shape_points[["shape_pt_lon", "shape_pt_lat"]].to_numpy()
    )
    if route_geometry.is_empty or route_geometry.length == 0:
        raise ValueError(f"Shape {representative_shape_id!r} has no length.")

    trip_stop_times = stop_times.loc[
        stop_times["trip_id"].eq(representative_trip_id),
        ["trip_id", "stop_id", "stop_sequence"],
    ].copy()
    trip_stop_times["stop_id"] = trip_stop_times["stop_id"].astype("string")
    trip_stop_times["stop_sequence"] = pd.to_numeric(
        trip_stop_times["stop_sequence"], errors="raise"
    )
    stops = df_stops.loc[:, list(_STOP_COLUMNS)].copy()
    stops["stop_id"] = stops["stop_id"].astype("string")
    route_stops = trip_stop_times.merge(stops, on="stop_id", how="left")
    for column in ("stop_lat", "stop_lon"):
        route_stops[column] = pd.to_numeric(route_stops[column], errors="raise")
    route_stops = route_stops.dropna(subset=["stop_sequence", "stop_lat", "stop_lon"])
    if len(route_stops) < 2:
        raise ValueError(
            f"Fewer than two usable stops were found for route {route_id!r}."
        )

    route_stops["dist_along_line"] = [
        route_geometry.project(Point(lon, lat))
        for lon, lat in zip(route_stops["stop_lon"], route_stops["stop_lat"])
    ]
    route_stops = route_stops.sort_values(
        ["dist_along_line", "stop_sequence"], kind="mergesort"
    ).reset_index(drop=True)

    segment_rows = []
    for index in range(len(route_stops) - 1):
        start_stop = route_stops.iloc[index]
        end_stop = route_stops.iloc[index + 1]
        start_distance = start_stop["dist_along_line"]
        end_distance = end_stop["dist_along_line"]
        if end_distance <= start_distance:
            raise ValueError(
                "Consecutive stops must have distinct positions on the route "
                f"shape: {start_stop['stop_id']!r}, {end_stop['stop_id']!r}."
            )

        segment_geometry = substring(route_geometry, start_distance, end_distance)
        if not isinstance(segment_geometry, LineString):
            raise ValueError(
                "Could not create a line geometry between stops "
                f"{start_stop['stop_id']!r} and {end_stop['stop_id']!r}."
            )
        segment_rows.append(
            {
                "segment_id": index,
                "from_stop_id": start_stop["stop_id"],
                "from_stop_name": start_stop["stop_name"],
                "to_stop_id": end_stop["stop_id"],
                "to_stop_name": end_stop["stop_name"],
                "to_stop_sequence": end_stop["stop_sequence"],
                "geometry": segment_geometry,
            }
        )

    segments = pd.DataFrame(segment_rows)
    return representative_trip_id, route_geometry, segments


def run_congestion_engine(
    df_vehicle_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    df_shapes: pd.DataFrame,
    route_id: str = "97-KEL",
    time_bin_size: str = "5min",
) -> CongestionResult:
    """Compute Route 97-style segment congestion from vehicle updates.

    The route's representative trip is chosen as the trip with the most
    stop-time records. Each update is assigned to the segment ending at its
    ``current_stop_sequence``. Segment baselines are median historical probe
    speeds; observed speeds are averaged in the requested time bins.

    Naive vehicle-update timestamps are interpreted as UTC. The input frames
    are not modified.

    Parameters
    ----------
    df_vehicle_updates : pandas.DataFrame
        Vehicle updates. Must include ``trip_route_id``, ``feed_timestamp``,
        ``position_speed``, ``current_stop_sequence``, and ``vehicle_id``.
    df_stops : pandas.DataFrame
        GTFS stops table with stop IDs, names, and coordinates.
    df_stop_times : pandas.DataFrame
        GTFS stop times table with trip IDs, stop IDs, and stop sequences.
    df_trips : pandas.DataFrame
        GTFS trips table with route, trip, and shape IDs.
    df_shapes : pandas.DataFrame
        GTFS shapes table with shape IDs, point sequences, and coordinates.
    route_id : str, optional
        GTFS route identifier. Defaults to ``"97-KEL"``.
    time_bin_size : str, optional
        Pandas fixed-frequency interval used to aggregate probe speeds.
        Defaults to ``"5min"``.

    Returns
    -------
    CongestionResult
        Route geometry and segments, matched vehicle probes, median baseline
        speeds, and the congestion time series.

    Raises
    ------
    ValueError
        If input columns, route metadata, vehicle values, or time-bin
        frequency are invalid.
    """
    if not isinstance(route_id, str) or not route_id:
        raise ValueError("route_id must be a non-empty string.")
    if not isinstance(time_bin_size, str) or not time_bin_size:
        raise ValueError("time_bin_size must be a non-empty frequency string.")
    _require_columns(df_vehicle_updates, _VEHICLE_UPDATE_COLUMNS, "df_vehicle_updates")

    (
        representative_trip_id,
        route_geometry,
        segments,
    ) = _build_route_segments(
        df_stops,
        df_stop_times,
        df_trips,
        df_shapes,
        route_id,
    )

    route_mask = (
        df_vehicle_updates["trip_route_id"].astype("string").eq(route_id).fillna(False)
    )
    route_updates = df_vehicle_updates.loc[route_mask].copy()
    route_updates["feed_timestamp"] = pd.to_datetime(
        route_updates["feed_timestamp"], utc=True
    )

    raw_speeds = route_updates["position_speed"]
    route_updates["position_speed"] = pd.to_numeric(raw_speeds, errors="coerce")
    invalid_speeds = raw_speeds.notna() & route_updates["position_speed"].isna()
    if invalid_speeds.any():
        raise ValueError("position_speed must contain numeric values.")

    raw_stop_sequences = route_updates["current_stop_sequence"]
    route_updates["current_stop_sequence"] = pd.to_numeric(
        raw_stop_sequences, errors="coerce"
    )
    invalid_sequences = (
        raw_stop_sequences.notna() & route_updates["current_stop_sequence"].isna()
    )
    if invalid_sequences.any():
        raise ValueError("current_stop_sequence must contain numeric values.")

    route_updates = route_updates.loc[
        route_updates["feed_timestamp"].notna()
        & route_updates["position_speed"].notna()
        & route_updates["position_speed"].ge(0)
        & route_updates["current_stop_sequence"].notna()
    ].copy()
    route_updates["current_stop_sequence"] = route_updates[
        "current_stop_sequence"
    ].astype("float64")
    route_updates["time_bin"] = route_updates["feed_timestamp"].dt.floor(time_bin_size)

    segment_lookup = segments[
        [
            "segment_id",
            "to_stop_sequence",
            "from_stop_name",
            "to_stop_name",
        ]
    ].copy()
    segment_lookup["to_stop_sequence"] = pd.to_numeric(
        segment_lookup["to_stop_sequence"], errors="raise"
    ).astype("float64")
    vehicle_probes = route_updates.merge(
        segment_lookup,
        left_on="current_stop_sequence",
        right_on="to_stop_sequence",
        how="inner",
    ).sort_values("feed_timestamp", kind="mergesort")
    vehicle_probes = vehicle_probes.reset_index(drop=True)

    if vehicle_probes.empty:
        baseline_speeds = pd.DataFrame(columns=["segment_id", "baseline_speed"])
        congestion = pd.DataFrame(columns=list(_CONGESTION_COLUMNS))
    else:
        baseline_speeds = (
            vehicle_probes.groupby("segment_id", as_index=False)["position_speed"]
            .median()
            .rename(columns={"position_speed": "baseline_speed"})
        )
        observed_speeds = (
            vehicle_probes.groupby(["segment_id", "time_bin"], as_index=False)[
                "position_speed"
            ]
            .mean()
            .rename(columns={"position_speed": "observed_speed"})
        )
        congestion = observed_speeds.merge(baseline_speeds, on="segment_id", how="left")
        congestion = congestion.loc[congestion["baseline_speed"] > 0.5].copy()
        congestion["congestion_index"] = (
            1 - congestion["observed_speed"] / congestion["baseline_speed"]
        ).clip(lower=0, upper=1)
        congestion = (
            congestion.loc[:, list(_CONGESTION_COLUMNS)]
            .sort_values(["time_bin", "segment_id"], kind="mergesort")
            .reset_index(drop=True)
        )

    return CongestionResult(
        route_id=route_id,
        representative_trip_id=representative_trip_id,
        route_geometry=route_geometry,
        segments=segments,
        vehicle_probes=vehicle_probes,
        baseline_speeds=baseline_speeds,
        congestion=congestion,
    )
