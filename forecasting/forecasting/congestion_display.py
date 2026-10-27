"""Prepare timestamp-aware congestion snapshots and consistent route maps."""

from __future__ import annotations

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
from shapely.geometry import LineString, MultiLineString

from .road_segment_congestion import CongestionResult


def prepare_congestion_snapshots(
    result: CongestionResult,
    times: pd.DatetimeIndex,
    *,
    max_age: str | pd.Timedelta = "15min",
) -> pd.DataFrame:
    """Select fresh, already-observed estimates at each requested timestamp.

    An aggregate becomes available at its last contributing probe timestamp,
    not at its floored time-bin label. Historical observations may seed the
    first frame, but are never carried past ``max_age``. The engine's full-
    history speed baselines are unchanged; this is a display replay, not a
    point-in-time baseline backtest. Inputs are not modified.

    Parameters
    ----------
    result : CongestionResult
        Engine result including matched, timestamped vehicle probes.
    times : pandas.DatetimeIndex
        Nonempty, unique, increasing display timestamps. Naive times are UTC.
    max_age : str or pandas.Timedelta, optional
        Positive maximum observation age, inclusive. The provisional default
        is 15 minutes; calibrate it against actual probe availability.

    Returns
    -------
    pandas.DataFrame
        One row per timestamp and segment, with segment metadata, ``as_of``,
        ``observed_at``, ``age``, ``is_fresh``, ``last_congestion_index``, and
        freshness-masked ``congestion_index``.

    Raises
    ------
    ValueError
        If timestamps, maximum age, or observation provenance are invalid.
    """
    times = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    if (
        times.empty
        or times.hasnans
        or not times.is_unique
        or not times.is_monotonic_increasing
    ):
        raise ValueError("times must be nonempty, valid, unique, and increasing.")
    max_age = pd.Timedelta(max_age)
    if pd.isna(max_age) or max_age <= pd.Timedelta(0):
        raise ValueError("max_age must be a positive duration.")
    if result.segments.empty or result.segments["segment_id"].duplicated().any():
        raise ValueError("Engine segments must be nonempty with unique segment IDs.")

    observations = result.congestion[
        ["segment_id", "time_bin", "congestion_index"]
    ].copy()
    probe_times = (
        result.vehicle_probes.groupby(["segment_id", "time_bin"], as_index=False)[
            "feed_timestamp"
        ]
        .max()
        .rename(columns={"feed_timestamp": "observed_at"})
    )
    observations = observations.merge(
        probe_times, on=["segment_id", "time_bin"], how="left", validate="one_to_one"
    )
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    if observations["observed_at"].isna().any():
        raise ValueError("Every congestion estimate must have contributing probes.")

    frames = []
    for segment_id in result.segments["segment_id"]:
        history = observations.loc[
            observations["segment_id"].eq(segment_id),
            ["observed_at", "congestion_index"],
        ].sort_values("observed_at")
        selected = pd.merge_asof(
            pd.DataFrame({"as_of": times}),
            history,
            left_on="as_of",
            right_on="observed_at",
            direction="backward",
        )
        selected["segment_id"] = segment_id
        frames.append(selected)
    snapshots = pd.concat(frames, ignore_index=True)
    snapshots["age"] = snapshots["as_of"] - snapshots["observed_at"]
    snapshots["is_fresh"] = snapshots["age"].le(max_age)
    snapshots["last_congestion_index"] = snapshots["congestion_index"]
    snapshots["congestion_index"] = snapshots["congestion_index"].where(
        snapshots["is_fresh"]
    )
    return (
        snapshots.merge(
            result.segments, on="segment_id", how="left", validate="many_to_one"
        )
        .sort_values(["as_of", "segment_id"], kind="stable")
        .reset_index(drop=True)
    )


def make_congestion_map(
    snapshots: pd.DataFrame,
    route_geometry: LineString | MultiLineString,
    *,
    title: str,
    value_label: str = "Congestion index",
) -> go.Figure:
    """Plot a snapshot or animation with the same trace and hover semantics.

    Parameters
    ----------
    snapshots : pandas.DataFrame
        Output of ``prepare_congestion_snapshots``. Every timestamp must
        contain the same segment IDs.
    route_geometry : shapely.geometry.LineString or shapely.geometry.MultiLineString
        Geometry used to center the map.
    title : str
        Figure title.
    value_label : str, optional
        Label for the displayed congestion value.

    Returns
    -------
    plotly.graph_objects.Figure
        Map with direction controls and, for multiple timestamps, playback.

    Raises
    ------
    ValueError
        If snapshots are empty, duplicated, or have inconsistent segments.
    """
    if snapshots.empty or snapshots.duplicated(["as_of", "segment_id"]).any():
        raise ValueError(
            "Map snapshots must be nonempty with unique time/segment rows."
        )
    segments = snapshots.drop_duplicates("segment_id").sort_values("segment_id")
    segment_ids = segments["segment_id"].tolist()
    colorscale = pc.get_colorscale("RdYlGn_r")
    frames = []
    for as_of, snapshot in snapshots.groupby("as_of", sort=True):
        snapshot = snapshot.sort_values("segment_id")
        if snapshot["segment_id"].tolist() != segment_ids:
            raise ValueError("Every map timestamp must contain the same segments.")
        traces = []
        for row in snapshot.itertuples(index=False):
            value = row.congestion_index
            longitude, latitude = row.geometry.xy
            reading = (
                f"{value_label}: {value:.2f}"
                if pd.notna(value)
                else "No recent estimate"
            )
            provenance = (
                f"Observed (UTC): {row.observed_at}<br>Age: {row.age}"
                if pd.notna(row.observed_at)
                else "No estimate observed by this timestamp"
            )
            traces.append(
                go.Scattermap(
                    lon=list(longitude),
                    lat=list(latitude),
                    mode="lines",
                    line=dict(
                        width=5,
                        color=(
                            pc.sample_colorscale(colorscale, [float(value)])[0]
                            if pd.notna(value)
                            else "gray"
                        ),
                    ),
                    name=f"Direction {row.trip_direction_id}: {row.from_stop_name} -> {row.to_stop_name}",
                    hovertext=(
                        f"Direction {row.trip_direction_id} | segment {row.segment_id}: "
                        f"{row.from_stop_name} -> {row.to_stop_name}"
                        f"<br>Shape {row.shape_id}<br>As of (UTC): {as_of}"
                        f"<br>{reading}<br>{provenance}"
                    ),
                    hoverinfo="text",
                )
            )
        frames.append(go.Frame(name=str(as_of), data=traces))

    direction_buttons = [
        dict(
            label="All selected directions",
            method="restyle",
            args=[{"visible": [True] * len(segments)}],
        )
    ]
    for direction in sorted(segments["trip_direction_id"].unique()):
        direction_buttons.append(
            dict(
                label=f"Direction {direction}",
                method="restyle",
                args=[
                    {"visible": segments["trip_direction_id"].eq(direction).tolist()}
                ],
            )
        )
    menus = [dict(type="dropdown", buttons=direction_buttons, x=0.4, y=1.05)]
    figure = go.Figure(data=frames[0].data)
    if len(frames) > 1:
        figure.frames = frames
        menus.insert(
            0,
            dict(
                type="buttons",
                showactive=False,
                x=0,
                y=1.05,
                buttons=[
                    dict(
                        label="Play",
                        method="animate",
                        args=[
                            None,
                            dict(
                                frame=dict(duration=250, redraw=True),
                                fromcurrent=True,
                                transition=dict(duration=0),
                            ),
                        ],
                    ),
                    dict(
                        label="Pause",
                        method="animate",
                        args=[
                            [None],
                            dict(
                                frame=dict(duration=0, redraw=True),
                                mode="immediate",
                                transition=dict(duration=0),
                            ),
                        ],
                    ),
                ],
            ),
        )
        figure.update_layout(
            sliders=[
                dict(
                    x=0.08,
                    len=0.9,
                    currentvalue=dict(prefix="Time (UTC): "),
                    steps=[
                        dict(
                            label=pd.Timestamp(frame.name).strftime("%m-%d %H:%M"),
                            method="animate",
                            args=[
                                [frame.name],
                                dict(
                                    frame=dict(duration=0, redraw=True),
                                    mode="immediate",
                                    transition=dict(duration=0),
                                ),
                            ],
                        )
                        for frame in frames
                    ],
                )
            ]
        )
    figure.update_layout(
        title=title,
        map_style="open-street-map",
        map=dict(
            center=dict(lat=route_geometry.centroid.y, lon=route_geometry.centroid.x),
            zoom=11,
        ),
        showlegend=False,
        width=1500,
        height=700,
        updatemenus=menus,
    )
    return figure
