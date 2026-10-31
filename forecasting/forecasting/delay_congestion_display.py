"""Timestamp-aware maps of signed segment delay accumulation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
from shapely.geometry import LineString, MultiLineString

from .congestion_display import _equivalent_segments
from .road_segment_delay_congestion import DelayCongestionResult


def prepare_delay_congestion_snapshots(
    result: DelayCongestionResult,
    times: pd.DatetimeIndex,
    *,
    max_age: str | pd.Timedelta = "15min",
    smoothing_window: str | pd.Timedelta | None = None,
) -> pd.DataFrame:
    """Select the newest crossed bin whose final proxy evidence is available.

    Late publication of an older bin cannot replace a newer traffic reading.
    Age is measured from the latest contributing downstream proxy arrival,
    not from publication; delayed updates and smoothing never extend freshness.
    Identical directed paths share a display row using the freshest eligible
    shape-specific source, with lowest segment ID breaking ties. Values are
    never pooled across shapes. Inputs are not modified.

    Parameters
    ----------
    result : DelayCongestionResult
        Delay engine outputs with observed and available timestamps.
    times : pandas.DatetimeIndex
        Nonempty unique increasing timestamps; naive values are UTC.
    max_age : str or pandas.Timedelta, optional
        Positive inclusive freshness limit, default 15 minutes.
    smoothing_window : str, pandas.Timedelta, or None, optional
        Trailing window over available actual segment/bin medians. Each bin
        contributes once. The window ends at the selected proxy observation,
        is open on the left, and uses no future evidence. Default is no smoothing.

    Returns
    -------
    pandas.DataFrame
        Segment metadata and raw/displayed signed seconds at each ``as_of``,
        with source provenance, count, age, and fresh/stale/unobserved status.
        Unobserved and expired values remain missing, not zero.

    Raises
    ------
    ValueError
        If display times, durations, segments, or evidence timestamps are invalid.
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
    if smoothing_window is not None:
        smoothing_window = pd.Timedelta(smoothing_window)
        if pd.isna(smoothing_window) or smoothing_window <= pd.Timedelta(0):
            raise ValueError("smoothing_window must be a positive duration.")
    if result.segments.empty or result.segments["segment_id"].duplicated().any():
        raise ValueError("Engine segments must be nonempty with unique segment IDs.")
    observations = result.congestion.copy()
    for column in ("time_bin", "observed_at", "available_at"):
        observations[column] = pd.to_datetime(
            observations[column], utc=True, errors="raise"
        )
    if (
        observations[["observed_at", "available_at"]].isna().any().any()
        or observations["available_at"].lt(observations["observed_at"]).any()
        or observations.duplicated(["segment_id", "time_bin"]).any()
    ):
        raise ValueError(
            "Every segment/bin must have unique, valid proxy evidence times."
        )
    observations = observations.sort_values(["segment_id", "time_bin"])
    frames = []
    for as_of in times:
        available = observations.loc[observations["available_at"].le(as_of)]
        selected = available.drop_duplicates("segment_id", keep="last").copy()
        selected["delta_delay_seconds"] = selected["median_delta_delay_seconds"]
        if smoothing_window is not None and not selected.empty:
            history = available.merge(
                selected[["segment_id", "observed_at"]].rename(
                    columns={"observed_at": "window_end"}
                ),
                on="segment_id",
                validate="many_to_one",
            )
            age = history["window_end"] - history["observed_at"]
            means = (
                history.loc[age.ge(pd.Timedelta(0)) & age.lt(smoothing_window)]
                .groupby("segment_id")["median_delta_delay_seconds"]
                .mean()
            )
            selected["delta_delay_seconds"] = selected["segment_id"].map(means)
        selected = selected.drop(columns=["trip_direction_id", "shape_id"])
        frame = result.segments.merge(
            selected, on="segment_id", how="left", validate="one_to_one"
        )
        frame["as_of"] = as_of
        frames.append(frame)
    snapshots = pd.concat(frames, ignore_index=True)
    snapshots["age"] = snapshots["as_of"] - snapshots["observed_at"]
    snapshots["is_fresh"] = snapshots["age"].le(max_age)
    snapshots["last_delta_delay_seconds"] = snapshots["delta_delay_seconds"]
    snapshots["raw_delta_delay_seconds"] = snapshots[
        "median_delta_delay_seconds"
    ].where(snapshots["is_fresh"])
    snapshots["delta_delay_seconds"] = snapshots["delta_delay_seconds"].where(
        snapshots["is_fresh"]
    )
    membership = _equivalent_segments(result.segments)
    snapshots = snapshots.merge(membership, on="segment_id", validate="many_to_one")
    snapshots = snapshots.sort_values(
        ["as_of", "display_id", "observed_at", "segment_id"],
        ascending=[True, True, False, True],
        na_position="last",
        kind="stable",
    ).drop_duplicates(["as_of", "display_id"])
    snapshots["source_segment_id"] = (
        snapshots["segment_id"].where(snapshots["observed_at"].notna()).astype("Int64")
    )
    snapshots["source_shape_id"] = snapshots["shape_id"].where(
        snapshots["observed_at"].notna()
    )
    snapshots["segment_id"] = snapshots.pop("display_id")
    snapshots["status"] = "unobserved"
    snapshots.loc[snapshots["observed_at"].notna(), "status"] = "stale"
    snapshots.loc[snapshots["is_fresh"], "status"] = "fresh"
    return snapshots.sort_values(["as_of", "segment_id"], kind="stable").reset_index(
        drop=True
    )


def make_delay_congestion_map(
    snapshots: pd.DataFrame,
    route_geometry: LineString | MultiLineString,
    *,
    title: str,
    color_limit_seconds: float = 120.0,
    frame_duration_ms: int = 250,
) -> go.Figure:
    """Plot signed delay seconds with one scale for static and animated maps.

    Parameters
    ----------
    snapshots : pandas.DataFrame
        Output of ``prepare_delay_congestion_snapshots``.
    route_geometry : LineString or MultiLineString
        Selected route geometry for centering.
    title : str
        Figure title.
    color_limit_seconds : float, optional
        Positive symmetric visual saturation limit, default 120 seconds.
        Only colours are clipped; hover values retain the full signed delta.
    frame_duration_ms : int, optional
        Nonnegative animation frame duration, default 250 milliseconds.

    Returns
    -------
    plotly.graph_objects.Figure
        Map using green for recovery, neutral for zero, red for added delay,
        and grey for stale/unobserved values. Hover labels identify proxies.

    Raises
    ------
    ValueError
        If values, colour limits, frame duration, or frame memberships are invalid.
    """
    if (
        isinstance(color_limit_seconds, (bool, np.bool_))
        or not np.isfinite(color_limit_seconds)
        or color_limit_seconds <= 0
    ):
        raise ValueError("color_limit_seconds must be positive and finite.")
    if (
        isinstance(frame_duration_ms, bool)
        or not isinstance(frame_duration_ms, int)
        or frame_duration_ms < 0
    ):
        raise ValueError("frame_duration_ms must be a nonnegative integer.")
    if snapshots.empty or snapshots.duplicated(["as_of", "segment_id"]).any():
        raise ValueError(
            "Map snapshots must be nonempty with unique time/segment rows."
        )
    if np.isinf(snapshots["delta_delay_seconds"].dropna()).any():
        raise ValueError("Map delay values must be finite.")
    segment_ids = sorted(snapshots["segment_id"].unique())
    colorscale = pc.get_colorscale("RdYlGn_r")
    frames = []
    for as_of, snapshot in snapshots.groupby("as_of", sort=True):
        snapshot = snapshot.sort_values("segment_id")
        if snapshot["segment_id"].tolist() != segment_ids:
            raise ValueError("Every map timestamp must contain the same segments.")
        traces = []
        for row in snapshot.itertuples(index=False):
            value = row.delta_delay_seconds
            longitude, latitude = row.geometry.xy
            if row.status == "fresh":
                reading = f"Delay change: {value:+.1f} s ({value / 60:+.2f} min)"
            elif row.status == "stale":
                reading = "Stale: proxy crossing exceeds maximum age"
            elif row.status == "unobserved":
                reading = "Unknown: no completed proxy pair available"
            else:
                raise ValueError(f"Unknown delay snapshot status: {row.status!r}.")
            colour = "gray"
            if pd.notna(value):
                scaled = (np.clip(value / color_limit_seconds, -1, 1) + 1) / 2
                colour = pc.sample_colorscale(colorscale, [float(scaled)])[0]
            traces.append(
                go.Scattermap(
                    lon=list(longitude),
                    lat=list(latitude),
                    mode="lines",
                    line=dict(width=5, color=colour),
                    name=f"{row.from_stop_name} -> {row.to_stop_name}",
                    hovertext=(
                        f"Segment {row.segment_id} | direction {row.trip_direction_id}: "
                        f"{row.from_stop_name} -> {row.to_stop_name}"
                        f"<br>Latest reported arrival-delay proxies<br>{reading}"
                        f"<br>Raw median: {row.raw_delta_delay_seconds:+.1f} s"
                        f"<br>Latest-bin traversals: {row.traversal_count}"
                        f"<br>Added-delay share: {row.added_delay_share:.0%}"
                        f"<br>Proxy crossing (UTC): {row.observed_at}"
                        f"<br>Evidence available (UTC): {row.available_at}"
                        f"<br>As of (UTC): {as_of}<br>Age: {row.age}"
                        f"<br>Source segment/shape: {row.source_segment_id} / {row.source_shape_id}"
                        f"<br>Equivalent segments: {row.member_segment_ids}"
                    ),
                    hoverinfo="text",
                    showlegend=False,
                )
            )
        frames.append(
            go.Frame(name=str(as_of), data=traces, traces=list(range(len(segment_ids))))
        )
    figure = go.Figure(data=frames[0].data)
    if len(frames) > 1:
        figure.frames = frames
        figure.update_layout(
            updatemenus=[
                dict(
                    type="buttons",
                    showactive=False,
                    buttons=[
                        dict(
                            label="Play",
                            method="animate",
                            args=[
                                None,
                                dict(
                                    frame=dict(duration=frame_duration_ms, redraw=True),
                                    transition=dict(duration=0),
                                    fromcurrent=True,
                                ),
                            ],
                        ),
                        dict(
                            label="Pause",
                            method="animate",
                            args=[
                                [None],
                                dict(
                                    frame=dict(duration=0, redraw=False),
                                    mode="immediate",
                                ),
                            ],
                        ),
                    ],
                )
            ],
            sliders=[
                dict(
                    steps=[
                        dict(
                            label=frame.name,
                            method="animate",
                            args=[
                                [frame.name],
                                dict(
                                    mode="immediate",
                                    frame=dict(duration=0, redraw=True),
                                    transition=dict(duration=0),
                                ),
                            ],
                        )
                        for frame in frames
                    ]
                )
            ],
        )
    min_lon, min_lat, max_lon, max_lat = route_geometry.bounds
    figure.add_trace(
        go.Scattermap(
            lon=[None, None],
            lat=[None, None],
            mode="markers",
            showlegend=False,
            marker=dict(
                color=[-color_limit_seconds, color_limit_seconds],
                cmin=-color_limit_seconds,
                cmax=color_limit_seconds,
                colorscale=colorscale,
                showscale=True,
                colorbar=dict(title="Delay change (s)<br>- recovery / + added"),
            ),
            hoverinfo="skip",
        )
    )
    figure.update_layout(
        title=title,
        map=dict(
            style="carto-positron",
            zoom=10,
            center=dict(lon=(min_lon + max_lon) / 2, lat=(min_lat + max_lat) / 2),
        ),
        height=650,
        margin=dict(l=0, r=0, t=70, b=0),
    )
    return figure
