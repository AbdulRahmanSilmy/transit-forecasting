"""Compute route-level road segment congestion from vehicle updates."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import substring

_VEHICLE_UPDATE_COLUMNS = (
    "trip_route_id",
    "trip_id",
    "trip_direction_id",
    "stop_id",
    "feed_timestamp",
    "position_speed",
    "current_stop_sequence",
    "vehicle_id",
)
_STOP_COLUMNS = ("stop_id", "stop_name", "stop_lat", "stop_lon")
_STOP_TIME_COLUMNS = ("trip_id", "stop_id", "stop_sequence")
_TRIP_COLUMNS = ("route_id", "trip_id", "direction_id", "shape_id")
_SHAPE_COLUMNS = ("shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon")
_SEGMENT_KEY_COLUMNS = ("segment_id", "trip_direction_id", "shape_id")
_PROBE_MATCH_COLUMNS = (
    "trip_id",
    "trip_direction_id",
    "stop_id",
    "current_stop_sequence",
)
_CONGESTION_COLUMNS = (
    *_SEGMENT_KEY_COLUMNS,
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
        Trip with the most stop-time records among the selected directions.
        Retained as a reference, not as the sole source of route segments.
    route_geometry : shapely.geometry.LineString or shapely.geometry.MultiLineString
        All selected route shapes in longitude/latitude coordinates. A single
        shape is returned as a LineString.
    segments : pandas.DataFrame
        Directed stop pairs, including ``trip_direction_id``, ``shape_id``,
        stop metadata, and sliced geometries. Segment IDs are unique within
        the result. ``to_stop_sequence`` describes the first trip defining
        the segment; probe matching uses each trip's own sequence instead.
    vehicle_probes : pandas.DataFrame
        Valid vehicle updates matched to a segment, including the UTC time
        bin used for aggregation. Matching requires the same trip, direction,
        stop ID, and stop sequence. Origin-stop updates have no incoming
        segment and are excluded.
    baseline_speeds : pandas.DataFrame
        Median historical probe speed for each directed segment and shape.
    congestion : pandas.DataFrame
        Per-segment, per-time-bin mean speed, baseline speed, and congestion
        index. The index is clipped to the inclusive range from 0 to 1.
    """

    route_id: str
    representative_trip_id: str
    route_geometry: LineString | MultiLineString
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


def _normalize_direction_ids(values: pd.Series, frame_name: str) -> pd.Series:
    """Parse directions without treating absent values as direction zero.

    Parameters
    ----------
    values : pandas.Series
        Direction identifiers, allowing missing values.
    frame_name : str
        Input name used in validation errors.

    Returns
    -------
    pandas.Series
        Nullable integer direction identifiers.

    Raises
    ------
    ValueError
        If a nonmissing direction is not zero or one.
    """
    directions = pd.to_numeric(values, errors="raise")
    if not directions.dropna().isin([0, 1]).all():
        raise ValueError(f"{frame_name} direction IDs must be 0 or 1.")
    return directions.astype("Int64")


def _build_shape_geometry(
    df_shapes: pd.DataFrame,
    shape_id: str,
) -> LineString:
    """Construct a travel-ordered geometry for one GTFS shape.

    Parameters
    ----------
    df_shapes : pandas.DataFrame
        GTFS shapes with normalized string shape IDs.
    shape_id : str
        Shape identifier to construct.

    Returns
    -------
    shapely.geometry.LineString
        Shape ordered by ``shape_pt_sequence``.

    Raises
    ------
    ValueError
        If the shape has missing or invalid coordinates or no usable length.
    """
    shape_points = df_shapes.loc[df_shapes["shape_id"].eq(shape_id)].copy()
    if shape_points.empty:
        raise ValueError(f"No shape points were found for shape {shape_id!r}.")
    for column in ("shape_pt_sequence", "shape_pt_lon", "shape_pt_lat"):
        shape_points[column] = pd.to_numeric(shape_points[column], errors="raise")
    if (
        shape_points[["shape_pt_sequence", "shape_pt_lon", "shape_pt_lat"]]
        .isna()
        .any()
        .any()
    ):
        raise ValueError(f"Shape {shape_id!r} contains missing coordinates.")
    if (
        shape_points[["shape_pt_lon", "shape_pt_lat"]]
        .isin([float("inf"), float("-inf")])
        .any()
        .any()
    ):
        raise ValueError(f"Shape {shape_id!r} contains infinite coordinates.")
    shape_points = shape_points.sort_values("shape_pt_sequence")
    if len(shape_points) < 2:
        raise ValueError(f"Shape {shape_id!r} must contain at least two points.")

    route_geometry = LineString(
        shape_points[["shape_pt_lon", "shape_pt_lat"]].to_numpy()
    )
    if route_geometry.is_empty or route_geometry.length == 0:
        raise ValueError(f"Shape {shape_id!r} has no length.")
    return route_geometry


def _build_route_segments(
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    df_shapes: pd.DataFrame,
    route_id: str,
    trip_direction_id: int | None,
) -> tuple[str, LineString | MultiLineString, pd.DataFrame, pd.DataFrame]:
    """Build directed segments and an exact trip-to-stop lookup.

    Parameters
    ----------
    df_stops : pandas.DataFrame
        GTFS stop metadata.
    df_stop_times : pandas.DataFrame
        GTFS trip stop sequences.
    df_trips : pandas.DataFrame
        GTFS trips, including ``direction_id`` and ``shape_id``.
    df_shapes : pandas.DataFrame
        GTFS shape coordinates.
    route_id : str
        Route identifier to process.
    trip_direction_id : int or None
        Direction to select, or both directions when ``None``.

    Returns
    -------
    tuple
        Reference trip ID, combined route geometry, directed segments, and
        a lookup keyed by trip, direction, stop ID, and stop sequence.

    Raises
    ------
    ValueError
        If selected GTFS metadata is missing, ambiguous, or inconsistent
        with the travel order of its shapes.
    """
    _require_columns(df_stops, _STOP_COLUMNS, "df_stops")
    _require_columns(df_stop_times, _STOP_TIME_COLUMNS, "df_stop_times")
    _require_columns(df_trips, _TRIP_COLUMNS, "df_trips")
    _require_columns(df_shapes, _SHAPE_COLUMNS, "df_shapes")
    route_trips = df_trips.loc[
        df_trips["route_id"].astype("string").eq(route_id)
    ].copy()
    route_trips["trip_id"] = route_trips["trip_id"].astype("string")
    route_trips["shape_id"] = route_trips["shape_id"].astype("string")
    route_trips["trip_direction_id"] = _normalize_direction_ids(
        route_trips["direction_id"], "df_trips"
    )
    if route_trips["trip_direction_id"].isna().any():
        raise ValueError("df_trips contains missing direction IDs.")
    if trip_direction_id is not None:
        route_trips = route_trips.loc[
            route_trips["trip_direction_id"].eq(trip_direction_id)
        ].copy()
    if route_trips.empty:
        raise ValueError(
            f"No GTFS trips were found for route {route_id!r} "
            f"and direction {trip_direction_id!r}."
        )
    if route_trips[["trip_id", "shape_id"]].isna().any().any():
        raise ValueError("Selected GTFS trips contain missing trip or shape IDs.")
    if route_trips["trip_id"].duplicated().any():
        raise ValueError("Selected GTFS trips contain duplicate trip IDs.")

    stop_times = df_stop_times.loc[
        df_stop_times["trip_id"].astype("string").isin(route_trips["trip_id"]),
        list(_STOP_TIME_COLUMNS),
    ].copy()
    stop_times["trip_id"] = stop_times["trip_id"].astype("string")
    stop_times["stop_id"] = stop_times["stop_id"].astype("string")
    stop_times["stop_sequence"] = pd.to_numeric(
        stop_times["stop_sequence"], errors="raise"
    )
    sequences = stop_times["stop_sequence"]
    if not (sequences.notna() & sequences.ge(0) & sequences.mod(1).eq(0)).all():
        raise ValueError("GTFS stop sequences must be nonnegative integers.")
    if stop_times.duplicated(["trip_id", "stop_sequence"]).any():
        raise ValueError("GTFS stop times contain duplicate trip/stop sequences.")
    stop_times["stop_sequence"] = sequences.astype("int64")
    trip_stop_counts = stop_times.groupby("trip_id").size()
    if not route_trips["trip_id"].isin(trip_stop_counts.index).all():
        raise ValueError("Selected GTFS trips are missing stop times.")
    representative_trip_id = str(trip_stop_counts.idxmax())

    stops = df_stops.loc[:, list(_STOP_COLUMNS)].copy()
    stops["stop_id"] = stops["stop_id"].astype("string")
    stops = stops.loc[stops["stop_id"].isin(stop_times["stop_id"])].copy()
    route_stops = stop_times.merge(
        stops, on="stop_id", how="left", validate="many_to_one"
    )
    for column in ("stop_lat", "stop_lon"):
        route_stops[column] = pd.to_numeric(route_stops[column], errors="raise")
    if route_stops[list(_STOP_COLUMNS)].isna().any().any():
        raise ValueError("Selected GTFS stop times contain missing stop metadata.")
    if (
        route_stops[["stop_lat", "stop_lon"]]
        .isin([float("inf"), float("-inf")])
        .any()
        .any()
    ):
        raise ValueError("Selected GTFS stops contain infinite coordinates.")

    shapes = df_shapes.copy()
    shapes["shape_id"] = shapes["shape_id"].astype("string")
    geometries: dict[str, LineString] = {}
    segment_ids: dict[tuple[int, str, str, str], int] = {}
    segment_rows = []
    lookup_rows = []
    for trip_id in trip_stop_counts.sort_values(ascending=False, kind="stable").index:
        trip = route_trips.loc[route_trips["trip_id"].eq(trip_id)].iloc[0]
        direction_id = int(trip["trip_direction_id"])
        shape_id = str(trip["shape_id"])
        if shape_id not in geometries:
            geometries[shape_id] = _build_shape_geometry(shapes, shape_id)
        geometry = geometries[shape_id]
        visits = (
            route_stops.loc[route_stops["trip_id"].eq(trip_id)]
            .sort_values("stop_sequence")
            .reset_index(drop=True)
        )
        distances = [
            geometry.project(Point(lon, lat))
            for lon, lat in zip(visits["stop_lon"], visits["stop_lat"])
        ]
        for index in range(len(visits)):
            stop = visits.iloc[index]
            segment_id = None
            if index > 0:
                start = visits.iloc[index - 1]
                start_distance, end_distance = distances[index - 1 : index + 1]
                if end_distance <= start_distance:
                    raise ValueError(
                        f"Stops in trip {trip_id!r} do not follow shape "
                        f"{shape_id!r} in stop_sequence order."
                    )
                key = (
                    direction_id,
                    shape_id,
                    str(start["stop_id"]),
                    str(stop["stop_id"]),
                )
                if key not in segment_ids:
                    segment_geometry = substring(geometry, start_distance, end_distance)
                    if not isinstance(segment_geometry, LineString):
                        raise ValueError(
                            f"Could not build a line segment for trip {trip_id!r}."
                        )
                    segment_ids[key] = len(segment_rows)
                    segment_rows.append(
                        {
                            "segment_id": segment_ids[key],
                            "trip_direction_id": direction_id,
                            "shape_id": shape_id,
                            "from_stop_id": start["stop_id"],
                            "from_stop_name": start["stop_name"],
                            "to_stop_id": stop["stop_id"],
                            "to_stop_name": stop["stop_name"],
                            "to_stop_sequence": stop["stop_sequence"],
                            "geometry": segment_geometry,
                        }
                    )
                segment_id = segment_ids[key]
            lookup_rows.append(
                {
                    "trip_id": trip_id,
                    "trip_direction_id": direction_id,
                    "stop_id": stop["stop_id"],
                    "current_stop_sequence": stop["stop_sequence"],
                    "segment_id": segment_id,
                }
            )
    if not segment_rows:
        raise ValueError(
            f"No usable directed segments were found for route {route_id!r}."
        )
    route_geometry = (
        next(iter(geometries.values()))
        if len(geometries) == 1
        else MultiLineString(list(geometries.values()))
    )
    lookup = pd.DataFrame(lookup_rows)
    lookup["segment_id"] = lookup["segment_id"].astype("Int64")
    return representative_trip_id, route_geometry, pd.DataFrame(segment_rows), lookup


def run_congestion_engine(
    df_vehicle_updates: pd.DataFrame,
    df_stops: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    df_shapes: pd.DataFrame,
    route_id: str = "97-KEL",
    time_bin_size: str = "5min",
    *,
    trip_direction_id: int | None = None,
) -> CongestionResult:
    """Compute Route 97-style segment congestion from vehicle updates.

    Build directed stop pairs for every selected trip and shape. An update
    matches only its own trip's ``trip_direction_id``, ``stop_id``, and
    ``current_stop_sequence``. Shared directed stop pairs on the same shape
    reuse a segment; opposite directions and different shapes never share
    baselines or congestion values.

    Unknown trips, conflicting directions/stops, and unknown sequences are
    skipped with a warning. Supply static GTFS matching the realtime period
    to avoid losing unmatched observations. Origin-stop updates have no
    incoming segment and are excluded without a warning.

    Naive vehicle-update timestamps are interpreted as UTC. The input frames
    are not modified.

    Parameters
    ----------
    df_vehicle_updates : pandas.DataFrame
        Vehicle updates. Must include ``trip_route_id``, ``trip_id``,
        ``trip_direction_id``, ``stop_id``, ``feed_timestamp``,
        ``position_speed``, ``current_stop_sequence``, and ``vehicle_id``.
    df_stops : pandas.DataFrame
        GTFS stops table with stop IDs, names, and coordinates.
    df_stop_times : pandas.DataFrame
        GTFS stop times table with trip IDs, stop IDs, and stop sequences.
    df_trips : pandas.DataFrame
        GTFS trips table with route, trip, direction, and shape IDs.
    df_shapes : pandas.DataFrame
        GTFS shapes table with shape IDs, point sequences, and coordinates.
    route_id : str, optional
        GTFS route identifier. Defaults to ``"97-KEL"``.
    time_bin_size : str, optional
        Pandas fixed-frequency interval used to aggregate probe speeds.
        Defaults to ``"5min"``.
    trip_direction_id : int or None, optional
        Select GTFS direction ``0`` or ``1``. The default, ``None``, processes
        both directions separately. Segment IDs are local to each result.

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

    Warns
    -----
    UserWarning
        If realtime updates cannot be matched to selected GTFS metadata.
    """
    if not isinstance(route_id, str) or not route_id:
        raise ValueError("route_id must be a non-empty string.")
    if not isinstance(time_bin_size, str) or not time_bin_size:
        raise ValueError("time_bin_size must be a non-empty frequency string.")
    if trip_direction_id is not None and (
        isinstance(trip_direction_id, bool)
        or not isinstance(trip_direction_id, int)
        or trip_direction_id not in (0, 1)
    ):
        raise ValueError("trip_direction_id must be 0, 1, or None.")
    _require_columns(df_vehicle_updates, _VEHICLE_UPDATE_COLUMNS, "df_vehicle_updates")

    (
        representative_trip_id,
        route_geometry,
        segments,
        stop_lookup,
    ) = _build_route_segments(
        df_stops,
        df_stop_times,
        df_trips,
        df_shapes,
        route_id,
        trip_direction_id,
    )

    route_mask = (
        df_vehicle_updates["trip_route_id"].astype("string").eq(route_id).fillna(False)
    )
    route_updates = df_vehicle_updates.loc[route_mask].copy()
    route_updates["trip_direction_id"] = _normalize_direction_ids(
        route_updates["trip_direction_id"], "df_vehicle_updates"
    )
    if trip_direction_id is not None:
        route_updates = route_updates.loc[
            route_updates["trip_direction_id"].eq(trip_direction_id)
            | route_updates["trip_direction_id"].isna()
        ].copy()
    for column in ("trip_id", "stop_id"):
        route_updates[column] = route_updates[column].astype("string")
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

    vehicle_probes = route_updates.merge(
        stop_lookup,
        on=list(_PROBE_MATCH_COLUMNS),
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unmatched = vehicle_probes["_merge"].eq("left_only")
    if unmatched.any():
        warnings.warn(
            f"Skipping {int(unmatched.sum())} vehicle updates with no GTFS match "
            "for (trip_id, trip_direction_id, stop_id, current_stop_sequence). "
            "Use static GTFS data aligned with the realtime feed.",
            UserWarning,
            stacklevel=2,
        )
    vehicle_probes = vehicle_probes.loc[
        ~unmatched & vehicle_probes["segment_id"].notna()
    ].drop(columns="_merge")
    vehicle_probes["segment_id"] = vehicle_probes["segment_id"].astype("int64")
    vehicle_probes["trip_direction_id"] = vehicle_probes["trip_direction_id"].astype(
        "int64"
    )
    vehicle_probes = vehicle_probes.merge(
        segments[[*_SEGMENT_KEY_COLUMNS, "from_stop_name", "to_stop_name"]],
        on=["segment_id", "trip_direction_id"],
        how="left",
        validate="many_to_one",
    ).sort_values("feed_timestamp", kind="mergesort")
    vehicle_probes = vehicle_probes.reset_index(drop=True)

    if vehicle_probes.empty:
        baseline_speeds = pd.DataFrame(
            columns=[*_SEGMENT_KEY_COLUMNS, "baseline_speed"]
        )
        congestion = pd.DataFrame(columns=list(_CONGESTION_COLUMNS))
    else:
        baseline_speeds = (
            vehicle_probes.groupby(list(_SEGMENT_KEY_COLUMNS), as_index=False)[
                "position_speed"
            ]
            .median()
            .rename(columns={"position_speed": "baseline_speed"})
        )
        observed_speeds = (
            vehicle_probes.groupby([*_SEGMENT_KEY_COLUMNS, "time_bin"], as_index=False)[
                "position_speed"
            ]
            .mean()
            .rename(columns={"position_speed": "observed_speed"})
        )
        congestion = observed_speeds.merge(
            baseline_speeds,
            on=list(_SEGMENT_KEY_COLUMNS),
            how="left",
            validate="many_to_one",
        )
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
