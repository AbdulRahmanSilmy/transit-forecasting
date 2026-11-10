"""Plot delay-propagation responses and route time-space diagrams."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.colors import sample_colorscale

from .delay_propagation import select_delay_events
from .road_segment_congestion import _require_columns

# Diverging blue (less delay than usual) -> neutral grey -> red (more delay).
_DIVERGING_SCALE = [[0.0, "#2a78d6"], [0.5, "#f0efec"], [1.0, "#e34948"]]
_TRIP_LINE_COLOUR = "#b4b2ad"


def make_delay_event_plot(
    anomalies: pd.DataFrame,
    *,
    segment_id: int,
    title: str,
    event_quantile: float = 0.9,
    before_minutes: float = 60.0,
    after_minutes: float = 90.0,
    max_gap_minutes: float = 30.0,
    service_date: str | pd.Timestamp | None = None,
) -> go.Figure:
    """Overlay one segment's delay trajectories around individual events.

    Each positive event selected by ``select_delay_events`` anchors a curve
    at elapsed time zero. Points are unsmoothed traversals of the same
    segment and service day, including the event bus. These are successive
    buses experiencing a segment, not one bus's delay along its route.
    Missing anomalies and gaps longer than ``max_gap_minutes`` break lines.

    Parameters
    ----------
    anomalies : pandas.DataFrame
        Output of ``compute_delay_anomalies``.
    segment_id : int
        Segment whose events and surrounding traversals are plotted.
    title : str
        Figure title.
    event_quantile : float, optional
        Segment-specific event quantile, default 0.9. Only positive
        anomalies at or above this threshold anchor curves.
    before_minutes, after_minutes : float, optional
        Window before and after each event, default 60 and 90 minutes.
        The before window may be zero; the after window must be positive.
    max_gap_minutes : float, optional
        Largest gap joined by a line, default 30 minutes.
    service_date : str or pandas.Timestamp, optional
        Restrict event anchors to one local service day. The event threshold
        still uses the segment's full history. None includes all days.

    Returns
    -------
    plotly.graph_objects.Figure
        One coloured, independently toggleable line per event. The x axis
        is elapsed minutes and y is anomalous segment delay in seconds.
        Diamonds mark event traversals. Hover includes actual local time,
        trip identity, raw delay change, and the other-day baseline.

    Raises
    ------
    ValueError
        If controls are invalid, the segment has missing times or infinite
        anomalies, or no positive events are available.

    Notes
    -----
    The curve's slope describes growth or decay in seconds per minute;
    no decay model is fitted. Consecutive qualifying traversals can describe
    the same episode, so overlapping curves are not independent incidents.
    Windows are clipped to observed data, never padded with zero delay.
    """
    _require_columns(
        anomalies,
        (
            "segment_id",
            "service_date",
            "local_time",
            "trip_instance_key",
            "delta_delay_seconds",
            "baseline_delta_delay_seconds",
            "anomaly_seconds",
        ),
        "anomalies",
    )
    for name, value in (
        ("before_minutes", before_minutes),
        ("after_minutes", after_minutes),
        ("max_gap_minutes", max_gap_minutes),
    ):
        minimum_ok = value >= 0 if name == "before_minutes" else value > 0
        if (
            isinstance(value, (bool, np.bool_))
            or not np.isfinite(value)
            or not minimum_ok
        ):
            minimum = "nonnegative" if name == "before_minutes" else "positive"
            raise ValueError(f"{name} must be finite and {minimum}.")
    segment = (
        anomalies.loc[anomalies["segment_id"].eq(segment_id)]
        .sort_values("local_time", kind="stable")
        .reset_index(drop=True)
    )
    if segment.empty:
        raise ValueError(f"No traversals for segment {segment_id}.")
    if segment[["local_time", "service_date"]].isna().any().any():
        raise ValueError("Segment traversals must have local_time and service_date.")
    if np.isinf(
        segment["anomaly_seconds"].to_numpy(dtype="float64", na_value=np.nan)
    ).any():
        raise ValueError("Segment anomaly_seconds must be finite or missing.")
    events = select_delay_events(segment, event_quantile=event_quantile)
    events = events.loc[events["anomaly_seconds"].gt(0)]
    if service_date is not None:
        day = pd.Timestamp(service_date)
        if pd.isna(day):
            raise ValueError("service_date must be a valid local service date.")
        events = events.loc[events["service_date"].eq(day.normalize())]
    if events.empty:
        raise ValueError(
            f"No positive delay events for segment {segment_id} "
            "on the selected service days."
        )
    colours = sample_colorscale(
        "Turbo",
        np.linspace(0.05, 0.95, len(events)).tolist() if len(events) > 1 else [0.5],
    )
    before = pd.Timedelta(minutes=before_minutes)
    after = pd.Timedelta(minutes=after_minutes)
    max_gap = pd.Timedelta(minutes=max_gap_minutes)
    figure = go.Figure()
    for number, (event, colour) in enumerate(
        zip(events.itertuples(index=False), colours), 1
    ):
        window = segment.loc[
            segment["service_date"].eq(event.service_date)
            & segment["local_time"].between(
                event.local_time - before, event.local_time + after
            )
        ]
        x: list[float | None] = []
        y: list[float | None] = []
        customdata: list[list[object]] = []
        previous_time: pd.Timestamp | None = None
        for sample in window.itertuples(index=False):
            if (
                previous_time is not None
                and sample.local_time - previous_time > max_gap
            ):
                x.append(None)
                y.append(None)
                customdata.append([None] * 4)
            x.append((sample.local_time - event.local_time).total_seconds() / 60)
            y.append(
                None if pd.isna(sample.anomaly_seconds) else sample.anomaly_seconds
            )
            customdata.append(
                [
                    sample.local_time.strftime("%Y-%m-%d %H:%M:%S"),
                    sample.trip_instance_key,
                    sample.delta_delay_seconds,
                    sample.baseline_delta_delay_seconds,
                ]
            )
            previous_time = sample.local_time
        figure.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                connectgaps=False,
                line=dict(color=colour, width=1.5),
                marker=dict(
                    symbol=["diamond" if value == 0 else "circle" for value in x],
                    size=[10 if value == 0 else 5 for value in x],
                ),
                name=f"{number}: {event.local_time:%Y-%m-%d %H:%M}",
                legendgroup=f"event-{number}",
                customdata=customdata,
                hovertemplate=(
                    "Time from event: %{x:+.1f} min"
                    "<br>Anomalous delay: %{y:+.1f} s"
                    "<br>Local time: %{customdata[0]}"
                    "<br>Trip: %{customdata[1]}"
                    "<br>Delay change: %{customdata[2]:+.1f} s"
                    "<br>Usual (other-day median): %{customdata[3]:+.1f} s"
                    "<extra>%{fullData.name}</extra>"
                ),
            )
        )
    figure.add_vline(x=0, line_dash="dot", line_color="#777777")
    figure.add_hline(y=0, line_dash="dash", line_color="#777777")
    figure.update_layout(
        title=f"{title}<br><sup>{len(events)} events; diamonds mark event traversals</sup>",
        xaxis=dict(
            title="Time from event traversal (minutes)",
            range=[-before_minutes, after_minutes],
        ),
        yaxis_title="Anomalous segment delay (s above usual)",
        legend=dict(
            title="Event start (local time)",
            itemclick="toggle",
            itemdoubleclick="toggleothers",
        ),
        height=600,
        plot_bgcolor="#fcfcfb",
    )
    return figure


def make_propagation_heatmap(
    response: pd.DataFrame,
    *,
    group: str | None = None,
    title: str,
    color_limit_seconds: float = 60.0,
    alpha: float = 0.05,
) -> go.Figure:
    """Plot excess anomaly by segment offset and lag after delay events.

    Parameters
    ----------
    response : pandas.DataFrame
        Output of ``event_space_time_response``.
    group : str, optional
        Group to plot; required when ``response`` has several groups.
    title : str
        Figure title.
    color_limit_seconds : float, optional
        Positive symmetric colour saturation, default 60 seconds. Hover and
        cell labels keep full values.
    alpha : float, optional
        Cells with a permutation p-value below this are starred, default 0.05.

    Returns
    -------
    plotly.graph_objects.Figure
        Heatmap with offsets on x (negative upstream) and lag bins on y
        (earliest at the bottom). Empty cells stay blank.

    Raises
    ------
    ValueError
        If the group is ambiguous or absent, or controls are invalid.
    """
    _require_columns(
        response,
        (
            "group",
            "offset",
            "lag_bin",
            "lag_start_minutes",
            "event_count",
            "response_count",
            "mean_anomaly_seconds",
            "null_mean_seconds",
            "excess_seconds",
            "excess_ci_low_seconds",
            "excess_ci_high_seconds",
            "p_value",
        ),
        "response",
    )
    if not np.isfinite(color_limit_seconds) or color_limit_seconds <= 0:
        raise ValueError("color_limit_seconds must be positive and finite.")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1.")
    groups = response["group"].unique()
    if group is None:
        if len(groups) != 1:
            raise ValueError("Choose a group; the response has several.")
        group = groups[0]
    cells = response.loc[response["group"].eq(group)]
    if cells.empty:
        raise ValueError(f"Group {group!r} is not in the response.")
    offsets = sorted(cells["offset"].unique())
    lag_bins = (
        cells.sort_values("lag_start_minutes")["lag_bin"].drop_duplicates().tolist()
    )

    def grid(column: str) -> np.ndarray:
        return (
            cells.pivot(index="lag_bin", columns="offset", values=column)
            .reindex(index=lag_bins, columns=offsets)
            .to_numpy(dtype="float64")
        )

    excess = grid("excess_seconds")
    p_value = grid("p_value")
    labels = np.where(
        np.isfinite(excess),
        np.char.add(
            np.char.mod("%+.0f", np.nan_to_num(excess)),
            np.where(p_value < alpha, "*", ""),
        ),
        "",
    )
    customdata = np.stack(
        [
            grid(column)
            for column in (
                "mean_anomaly_seconds",
                "null_mean_seconds",
                "excess_ci_low_seconds",
                "excess_ci_high_seconds",
                "p_value",
                "response_count",
            )
        ],
        axis=-1,
    )
    event_count = int(cells["event_count"].iloc[0])
    figure = go.Figure(
        go.Heatmap(
            z=excess,
            x=[f"{offset:+d}" for offset in offsets],
            y=lag_bins,
            text=labels,
            texttemplate="%{text}",
            customdata=customdata,
            zmin=-color_limit_seconds,
            zmax=color_limit_seconds,
            zmid=0,
            colorscale=_DIVERGING_SCALE,
            xgap=2,
            ygap=2,
            colorbar=dict(title="Excess anomaly (s)<br>vs other-day null"),
            hovertemplate=(
                "Offset %{x} segments, lag %{y} min"
                "<br>Excess: %{z:+.1f} s (95% CI %{customdata[2]:+.1f} to "
                "%{customdata[3]:+.1f})"
                "<br>Observed mean: %{customdata[0]:+.1f} s"
                "<br>Other-day null mean: %{customdata[1]:+.1f} s"
                "<br>Permutation p: %{customdata[4]:.3f}"
                "<br>Responses: %{customdata[5]:.0f}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title=f"{title}<br><sup>{group}: {event_count} events; * p < {alpha:g}</sup>",
        xaxis_title="Segment offset from delayed segment (- upstream, + downstream)",
        yaxis_title="Minutes after the event (other buses)",
        height=520,
        plot_bgcolor="#fcfcfb",
    )
    return figure


def make_time_space_diagram(
    anomalies: pd.DataFrame,
    ordered_segments: pd.DataFrame,
    *,
    service_date: str | pd.Timestamp,
    title: str,
    color_limit_seconds: float = 120.0,
    shape_dist_per_km: float = 1000.0,
) -> go.Figure:
    """Plot each trip's path through time and route distance for one day.

    Grey lines join each trip's proxy arrivals; markers at segment midpoints
    show the segment anomaly. Diagonal bands of colour travelling across
    trips indicate spreading delay; a horizontal stripe indicates delay that
    stays on one segment.

    Parameters
    ----------
    anomalies : pandas.DataFrame
        Output of ``compute_delay_anomalies``.
    ordered_segments : pandas.DataFrame
        Output of ``order_route_segments`` for stop labels.
    service_date : str or pandas.Timestamp
        Local service date to plot.
    title : str
        Figure title.
    color_limit_seconds : float, optional
        Positive symmetric colour saturation, default 120 seconds.
    shape_dist_per_km : float, optional
        GTFS ``shape_dist_traveled`` units per kilometre, default 1000
        (metres, as in BC Transit feeds).

    Returns
    -------
    plotly.graph_objects.Figure
        Time-space diagram in local time with stop names on the y axis.

    Raises
    ------
    ValueError
        If no traversals fall on the date or controls are invalid.
    """
    _require_columns(
        anomalies,
        (
            "trip_instance_key",
            "service_date",
            "position",
            "from_local_time",
            "local_time",
            "from_shape_dist",
            "to_shape_dist",
            "delta_delay_seconds",
            "baseline_delta_delay_seconds",
            "anomaly_seconds",
        ),
        "anomalies",
    )
    _require_columns(
        ordered_segments,
        ("from_stop_name", "to_stop_name", "from_shape_dist", "to_shape_dist"),
        "ordered_segments",
    )
    for name, value in (
        ("color_limit_seconds", color_limit_seconds),
        ("shape_dist_per_km", shape_dist_per_km),
    ):
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite.")
    day = anomalies.loc[
        anomalies["service_date"].eq(pd.Timestamp(service_date).normalize())
    ].sort_values(["trip_instance_key", "position"])
    if day.empty:
        raise ValueError(f"No traversals on service date {service_date}.")
    line_x, line_y = [], []
    for _, trip in day.groupby("trip_instance_key", sort=False):
        for row in trip.itertuples(index=False):
            line_x += [row.from_local_time, row.local_time, None]
            line_y += [
                row.from_shape_dist / shape_dist_per_km,
                row.to_shape_dist / shape_dist_per_km,
                None,
            ]
    middle = day["from_local_time"] + (day["local_time"] - day["from_local_time"]) / 2
    figure = go.Figure(
        [
            go.Scatter(
                x=line_x,
                y=line_y,
                mode="lines",
                line=dict(color=_TRIP_LINE_COLOUR, width=1),
                hoverinfo="skip",
                name="Trip path (proxy arrivals)",
            ),
            go.Scatter(
                x=middle,
                y=(day["from_shape_dist"] + day["to_shape_dist"])
                / 2
                / shape_dist_per_km,
                mode="markers",
                marker=dict(
                    size=9,
                    color=day["anomaly_seconds"],
                    cmin=-color_limit_seconds,
                    cmax=color_limit_seconds,
                    colorscale=_DIVERGING_SCALE,
                    line=dict(color="#fcfcfb", width=1),
                    colorbar=dict(title="Anomaly (s)<br>vs usual for<br>segment/hour"),
                ),
                customdata=day[
                    [
                        "delta_delay_seconds",
                        "baseline_delta_delay_seconds",
                        "trip_instance_key",
                    ]
                ],
                hovertemplate=(
                    "%{x|%H:%M}<br>Anomaly: %{marker.color:+.0f} s"
                    "<br>Delay change: %{customdata[0]:+.0f} s"
                    "<br>Usual (other-day median): %{customdata[1]:+.0f} s"
                    "<br>Trip: %{customdata[2]}<extra></extra>"
                ),
                name="Segment anomaly",
            ),
        ]
    )
    stops = pd.concat(
        [
            ordered_segments[["from_stop_name", "from_shape_dist"]].set_axis(
                ["name", "dist"], axis=1
            ),
            ordered_segments[["to_stop_name", "to_shape_dist"]].set_axis(
                ["name", "dist"], axis=1
            ),
        ]
    ).drop_duplicates("dist")
    figure.update_layout(
        title=title,
        xaxis_title="Local time",
        yaxis=dict(
            title="Distance along route (km)",
            tickvals=stops["dist"] / shape_dist_per_km,
            ticktext=[
                f"{name} ({dist / shape_dist_per_km:.1f})"
                for name, dist in zip(stops["name"], stops["dist"])
            ],
            gridcolor="#e6e5e1",
        ),
        legend=dict(orientation="h", y=-0.15),
        height=700,
        plot_bgcolor="#fcfcfb",
    )
    return figure
