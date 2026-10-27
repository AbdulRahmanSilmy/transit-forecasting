"""Prepare timestamp-aware congestion snapshots and consistent route maps."""

from __future__ import annotations

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
from shapely.geometry import LineString, MultiLineString

from .road_segment_congestion import CongestionResult, _require_columns


def _equivalent_segments(segments: pd.DataFrame) -> pd.DataFrame:
    """Map equivalent directed stop-pair geometries to a stable display ID.

    Parameters
    ----------
    segments : pandas.DataFrame
        Engine segments with unique IDs.

    Returns
    -------
    pandas.DataFrame
        Segment membership, using the lowest segment ID in each group.
    """
    candidates: dict[tuple[int, str, str], list[tuple[int, LineString]]] = {}
    rows = []
    for row in segments.sort_values("segment_id").itertuples(index=False):
        key = (row.trip_direction_id, row.from_stop_id, row.to_stop_id)
        matches = candidates.setdefault(key, [])
        display_id = next(
            (
                segment_id
                for segment_id, geometry in matches
                if geometry.equals(row.geometry)
            ),
            row.segment_id,
        )
        if display_id == row.segment_id:
            matches.append((display_id, row.geometry))
        rows.append(
            dict(
                segment_id=row.segment_id,
                display_id=display_id,
                shape_id=row.shape_id,
            )
        )
    membership = pd.DataFrame(rows)
    members = membership.groupby("display_id", as_index=False).agg(
        member_segment_ids=("segment_id", tuple),
        member_shape_ids=("shape_id", lambda values: tuple(sorted(set(values)))),
    )
    return membership.drop(columns="shape_id").merge(
        members, on="display_id", validate="many_to_one"
    )


def prepare_congestion_snapshots(
    result: CongestionResult,
    times: pd.DatetimeIndex,
    *,
    max_age: str | pd.Timedelta = "15min",
    service_activity: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Select fresh, already-observed estimates at each requested timestamp.

    An aggregate becomes available at its last contributing probe timestamp,
    not at its floored time-bin label. Historical observations may seed the
    first frame, but are never carried past ``max_age``. The engine's full-
    history speed baselines are unchanged; this is a display replay, not a
    point-in-time baseline backtest. Equivalent paths with the same direction
    and directed stop pair share one display row. The freshest observation
    wins, with the lowest source segment ID breaking timestamp ties; values
    and baselines are never pooled across shapes. Inputs are not modified.

    Parameters
    ----------
    result : CongestionResult
        Engine result including matched, timestamped vehicle probes.
    times : pandas.DatetimeIndex
        Nonempty, unique, increasing display timestamps. Naive times are UTC.
    max_age : str or pandas.Timedelta, optional
        Positive maximum observation age, inclusive. The provisional default
        is 15 minutes; calibrate it against actual probe availability.
    service_activity : pandas.DataFrame or None, optional
        Output of ``get_congestion_service_activity`` for the shared selected
        period. When omitted, schedule activity is explicitly unevaluated.

    Returns
    -------
    pandas.DataFrame
        One row per timestamp and equivalent road section. ``segment_id`` is
        the group's lowest engine segment ID; ``source_segment_id`` and
        ``source_shape_id`` identify the selected observation. Membership is
        retained in ``member_segment_ids`` and ``member_shape_ids``.
        Other columns include segment metadata, ``as_of``,
        ``observed_at``, ``age``, ``is_fresh``, ``last_congestion_index``, and
        freshness-masked ``congestion_index``. ``status`` distinguishes fresh,
        stale, unobserved, inactive, and unavailable-baseline records.
        ``inactive_shape_ids`` identifies inactive members even when another
        shape provides a fresh observation.

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
    probe_times["observed_at"] = pd.to_datetime(probe_times["observed_at"], utc=True)
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
        selected = pd.merge_asof(
            selected,
            probe_times.loc[probe_times["segment_id"].eq(segment_id), ["observed_at"]]
            .rename(columns={"observed_at": "last_probe_at"})
            .sort_values("last_probe_at"),
            left_on="as_of",
            right_on="last_probe_at",
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
    membership = _equivalent_segments(result.segments)
    snapshots = snapshots.merge(
        result.segments, on="segment_id", how="left", validate="many_to_one"
    ).merge(
        membership,
        on="segment_id",
        validate="many_to_one",
    )
    snapshots["last_probe_at"] = snapshots.groupby(["as_of", "display_id"])[
        "last_probe_at"
    ].transform("max")
    if service_activity is None:
        snapshots["scheduled_trips"] = pd.Series(
            pd.NA, index=snapshots.index, dtype="Int64"
        )
        snapshots["inactive_shape_ids"] = [()] * len(snapshots)
    else:
        _require_columns(
            service_activity, ("segment_id", "scheduled_trips"), "service_activity"
        )
        service_activity = service_activity[["segment_id", "scheduled_trips"]].copy()
        service_activity["scheduled_trips"] = pd.to_numeric(
            service_activity["scheduled_trips"], errors="raise"
        )
        if (
            service_activity["segment_id"].duplicated().any()
            or set(service_activity["segment_id"]) != set(result.segments["segment_id"])
            or service_activity["scheduled_trips"].isna().any()
            or service_activity["scheduled_trips"].lt(0).any()
            or service_activity["scheduled_trips"].mod(1).ne(0).any()
        ):
            raise ValueError("Service activity requires one valid count per segment.")
        members = (
            result.segments[["segment_id", "shape_id"]]
            .merge(membership[["segment_id", "display_id"]], on="segment_id")
            .merge(service_activity[["segment_id", "scheduled_trips"]], on="segment_id")
        )
        counts = members.groupby("display_id", as_index=False)["scheduled_trips"].sum()
        inactive = (
            members.loc[members["scheduled_trips"].eq(0)]
            .groupby("display_id")["shape_id"]
            .agg(lambda values: tuple(sorted(set(values))))
        )
        counts["inactive_shape_ids"] = counts["display_id"].map(
            lambda identifier: inactive.get(identifier, ())
        )
        snapshots = snapshots.merge(counts, on="display_id", validate="many_to_one")
    snapshots = snapshots.sort_values(
        ["as_of", "display_id", "observed_at", "segment_id"],
        ascending=[True, True, False, True],
        kind="stable",
        na_position="last",
    ).drop_duplicates(["as_of", "display_id"])
    snapshots["source_segment_id"] = (
        snapshots["segment_id"].where(snapshots["observed_at"].notna()).astype("Int64")
    )
    snapshots["source_shape_id"] = snapshots["shape_id"].where(
        snapshots["observed_at"].notna()
    )
    snapshots["segment_id"] = snapshots.pop("display_id")
    snapshots["status"] = "unobserved"
    snapshots.loc[snapshots["scheduled_trips"].eq(0), "status"] = "inactive"
    snapshots.loc[snapshots["last_probe_at"].notna(), "status"] = "unavailable"
    snapshots.loc[snapshots["observed_at"].notna(), "status"] = "stale"
    snapshots.loc[snapshots["is_fresh"], "status"] = "fresh"
    return snapshots.sort_values(["as_of", "segment_id"], kind="stable").reset_index(
        drop=True
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
            reading = {
                "fresh": f"{value_label}: {value:.2f}",
                "stale": "Stale estimate: maximum observation age exceeded",
                "unobserved": "No matching vehicle observations by this timestamp",
                "inactive": "Inactive service variant: no scheduled trips in selected period",
                "unavailable": "Matched vehicle observations, but no eligible congestion baseline",
            }[row.status]
            provenance = (
                f"Observed (UTC): {row.observed_at}<br>Age: {row.age}"
                if pd.notna(row.observed_at)
                else "No estimate observed by this timestamp"
            )
            service = (
                "Service activity: not evaluated"
                if pd.isna(row.scheduled_trips)
                else (
                    f"Scheduled trip traversals in selected period: {row.scheduled_trips}"
                    f"<br>Inactive shapes in selected period: {row.inactive_shape_ids}"
                )
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
                        f"<br>Equivalent segments: {row.member_segment_ids}"
                        f"<br>Shapes: {row.member_shape_ids}"
                        f"<br>Source segment: {row.source_segment_id}"
                        f"<br>Source shape: {row.source_shape_id}"
                        f"<br>As of (UTC): {as_of}"
                        f"<br>{reading}<br>{provenance}<br>{service}"
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
