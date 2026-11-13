"""Shared-time comparisons of segment delay, speed, traversal and dwell."""

from __future__ import annotations

from html import escape

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .stop_dynamics_eda import SegmentTimingEDA, StopDynamicsEDA
from .stop_dynamics_model import StopDynamicsIdentity, StopDynamicsModel


def make_stop_dynamics_model_figure(
    result: StopDynamicsModel,
    *,
    split: str = "holdout",
    display_timezone: str = "America/Vancouver",
) -> go.Figure:
    """Compare reported/fitted delays and residuals for one model split.

    Parameters
    ----------
    result : StopDynamicsModel
        Fitted model with individual training and holdout predictions.
    split : {"train", "holdout"}, default "holdout"
        Visits to display without changing the fitted model.
    display_timezone : str, default "America/Vancouver"
        Timezone for destination-arrival timestamps.

    Returns
    -------
    plotly.graph_objects.Figure
        Two shared-time rows in minutes, with independent vertical zoom.
        No plot is shown or saved; inputs are not modified.

    Raises
    ------
    ValueError
        If the split is invalid or contains no visits.
    """
    if split not in ("train", "holdout"):
        raise ValueError("split must be 'train' or 'holdout'.")
    visits = result.observations.loc[
        result.observations["split"].eq(split)
    ].sort_values("arrival_time")
    if visits.empty:
        raise ValueError(f"No visits available for model split {split!r}.")
    times = visits["arrival_time"].dt.tz_convert(display_timezone).tolist()
    label = "Training" if split == "train" else "Held-out"
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=(
            f"{label} reported and fitted arrival delays",
            "Residual: reported minus fitted",
        ),
    )
    for column, name, color in (
        ("arrival_delay", "Reported delay", "black"),
        ("predicted_delay_seconds", "Fitted equation", "royalblue"),
        ("persistence_delay_seconds", "Upstream persistence", "darkorange"),
    ):
        figure.add_trace(
            go.Scattergl(
                x=times,
                y=visits[column] / 60,
                mode="markers",
                name=name,
                marker=dict(color=color, size=6, opacity=0.65),
                customdata=visits[
                    ["trip_instance_key", "stop_sequence", "baseline_source"]
                ],
                hovertemplate=(
                    "%{x}<br>Journey: %{customdata[0]}"
                    "<br>Stop sequence: %{customdata[1]}<br>Baseline: %{customdata[2]}"
                    "<br>Delay: %{y:.2f} min<extra>%{fullData.name}</extra>"
                ),
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scattergl(
            x=times,
            y=visits["residual_seconds"] / 60,
            mode="markers",
            name="Equation residual",
            marker=dict(color="firebrick", size=6),
            customdata=visits[["trip_instance_key"]],
            hovertemplate=(
                "%{x}<br>Journey: %{customdata[0]}"
                "<br>Residual: %{y:.2f} min<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)
    figure.update_yaxes(title_text="Arrival delay (min)", row=1, col=1)
    figure.update_yaxes(title_text="Residual (min)", row=2, col=1)
    figure.update_yaxes(fixedrange=False)
    figure.update_xaxes(
        title_text=f"Destination arrival ({display_timezone})",
        row=2,
        col=1,
    )
    segment = result.segment
    figure.update_layout(
        title=(
            f"Segment {segment['segment_id']}, direction "
            f"{segment['trip_direction_id']}: retrospective dynamics "
            f"{'training' if split == 'train' else 'holdout'}"
        ),
        height=850,
        hovermode="x unified",
        hoversubplots="axis",
        legend=dict(orientation="h", x=0, y=-0.18, yanchor="top"),
        margin=dict(t=110, b=160),
    )
    return figure


def make_stop_dynamics_identity_figure(
    result: StopDynamicsIdentity,
    *,
    display_timezone: str = "America/Vancouver",
) -> go.Figure:
    """Show delay accounting for every eligible visit without model comparisons.

    Parameters
    ----------
    result : StopDynamicsIdentity
        Full-data endpoint observations and feed-inferred scheduled allowances.
    display_timezone : str, default "America/Vancouver"
        Timezone for destination-arrival timestamps.

    Returns
    -------
    plotly.graph_objects.Figure
        Current delay and segment delay-change rows, in minutes, comparing
        reported values only with the exact accounting identity. No fitting,
        data splitting, displaying, saving or input mutation is performed.

    Raises
    ------
    ValueError
        If no eligible visits are available.
    """
    visits = result.observations.sort_values("arrival_time")
    if visits.empty:
        raise ValueError("No visits available for the delay identity figure.")
    times = visits["arrival_time"].dt.tz_convert(display_timezone).tolist()
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=(
            "Current arrival delay: reported and reconstructed",
            "Delay gained / recovered: reported and T + W - S_trip",
        ),
    )
    accounting_change = (
        visits["traversal_seconds"]
        + visits["dwell_seconds"]
        - visits["inferred_allowance_seconds"]
    )
    for values, label, color, row in (
        (visits["arrival_delay"], "Reported current delay", "black", 1),
        (visits["identity_delay_seconds"], "Identity current delay", "royalblue", 1),
        (visits["delta_delay_seconds"], "Reported delay change", "firebrick", 2),
        (accounting_change, "T + W - S_trip", "seagreen", 2),
    ):
        figure.add_trace(
            go.Scattergl(
                x=times,
                y=values / 60,
                mode="markers",
                name=label,
                marker=dict(color=color, size=6, opacity=0.6),
                customdata=visits[
                    [
                        "trip_instance_key",
                        "traversal_seconds",
                        "dwell_seconds",
                        "inferred_allowance_seconds",
                    ]
                ],
                hovertemplate=(
                    "%{x}<br>Journey: %{customdata[0]}"
                    "<br>T: %{customdata[1]:.0f} s<br>W: %{customdata[2]:.0f} s"
                    "<br>S_trip: %{customdata[3]:.0f} s"
                    "<br>Delay: %{y:.2f} min<extra>%{fullData.name}</extra>"
                ),
            ),
            row=row,
            col=1,
        )
    figure.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)
    figure.update_yaxes(title_text="Current delay (min)", row=1, col=1)
    figure.update_yaxes(title_text="Delay change (min)", row=2, col=1)
    figure.update_yaxes(fixedrange=False)
    figure.update_xaxes(
        title_text=f"Destination arrival ({display_timezone})",
        row=2,
        col=1,
    )
    figure.update_layout(
        title=f"Segment {result.segment['segment_id']}: full-data delay accounting",
        height=850,
        hovermode="x unified",
        hoversubplots="axis",
        legend=dict(orientation="h", x=0, y=-0.18, yanchor="top"),
        margin=dict(t=110, b=160),
    )
    return figure


def make_segment_timing_figure(
    analysis: StopDynamicsEDA,
    timing: SegmentTimingEDA,
    *,
    display_timezone: str = "America/Vancouver",
    display_start: str | pd.Timestamp | None = None,
    display_end: str | pd.Timestamp | None = None,
) -> go.Figure:
    """Compare arrival-delay change, probe speed, traversal and upstream dwell.

    Parameters
    ----------
    analysis : StopDynamicsEDA
        Selected segment's existing delay and instantaneous-speed observations.
    timing : SegmentTimingEDA
        Timing observations prepared from the same analysis.
    display_timezone : str, optional
        Timezone for the common time axis and endpoint hover labels.
    display_start, display_end : str or pandas.Timestamp or None, optional
        Initial visible bounds. Naive timestamps mean UTC. Defaults cover the
        complete bin grid; zooming never recomputes full-snapshot baselines.

    Returns
    -------
    plotly.graph_objects.Figure
        Four matched time axes with independent vertical zoom, individual visits,
        bin summaries, support in hover, and full-snapshot reference medians.
        No plot is shown or saved. Durations are seconds; delays are minutes
        and instantaneous probe speeds are km/h.

    Raises
    ------
    ValueError
        If the preparations disagree or display bounds are invalid/reversed.
    """
    if not analysis.segment.equals(
        timing.segment
    ) or not analysis.speed_bins.index.equals(timing.bins.index):
        raise ValueError(
            "Timing and delay preparations must describe the same segment/grid."
        )
    grid = analysis.speed_bins.index
    start = (
        grid[0] if display_start is None else pd.to_datetime(display_start, utc=True)
    )
    end = (
        grid[-1] + pd.Timedelta(analysis.time_bin_size)
        if display_end is None
        else pd.to_datetime(display_end, utc=True)
    )
    if pd.isna(start) or pd.isna(end) or start >= end:
        raise ValueError(
            "Display bounds must be valid timestamps with start before end."
        )
    bin_times = grid.tz_convert(display_timezone)
    figure = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.055,
        subplot_titles=(
            "Arrival-delay change on the incoming segment",
            "Mean probe speed vs median probe speed",
            "Traversal: upstream departure to destination arrival",
            "Upstream dwell: upstream arrival to upstream departure",
        ),
    )
    pairs = analysis.delay_changes.loc[
        analysis.delay_changes["exclusion_reason"].eq("included")
    ]
    figure.add_trace(
        go.Scattergl(
            x=pairs["arrival_time"].dt.tz_convert(display_timezone).tolist(),
            y=pairs["delta_delay_seconds"] / 60,
            mode="markers",
            name="Individual arrival-delay change",
            marker=dict(color="firebrick", size=5, opacity=0.45),
            customdata=pairs[
                ["trip_instance_key", "from_stop_sequence", "stop_sequence"]
            ],
            hovertemplate=(
                "%{x}<br>Journey: %{customdata[0]}"
                "<br>Sequences: %{customdata[1]} -> %{customdata[2]}"
                "<br>Arrival-delay change: %{y:.2f} min<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=bin_times,
            y=analysis.delay_bins["median_delta_delay_seconds"] / 60,
            mode="lines+markers",
            name=f"Median delay change ({analysis.time_bin_size})",
            line=dict(color="firebrick"),
            connectgaps=False,
            customdata=analysis.delay_bins[["pair_count"]],
            hovertemplate=(
                "%{x}<br>Median change: %{y:.2f} min"
                "<br>Delay pairs: %{customdata[0]}<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )
    figure.add_hline(y=0, line_dash="dot", line_color="gray", row=1, col=1)
    figure.add_trace(
        go.Scatter(
            x=bin_times,
            y=analysis.speed_bins["observed_speed"] * 3.6,
            mode="lines+markers",
            name=f"Mean probe speed ({analysis.time_bin_size})",
            line=dict(color="royalblue"),
            connectgaps=False,
            customdata=analysis.speed_bins[["probe_count", "bus_count"]],
            hovertemplate=(
                "%{x}<br>Speed: %{y:.2f} km/h<br>Probes: %{customdata[0]}"
                "<br>Distinct vehicles: %{customdata[1]}<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=bin_times,
            y=analysis.speed_bins["baseline_speed"] * 3.6,
            mode="lines",
            name="Snapshot median probe speed",
            line=dict(color="darkorange", dash="dash"),
            hovertemplate="%{x}<br>Median probe speed: %{y:.2f} km/h<extra></extra>",
        ),
        row=2,
        col=1,
    )
    for row, metric, color, label in (
        (3, "traversal", "seagreen", "Traversal"),
        (4, "dwell", "darkviolet", "Upstream dwell"),
    ):
        visits = timing.observations.loc[
            timing.observations[f"{metric}_exclusion_reason"].eq("included")
        ].copy()
        for column in (
            "upstream_arrival_time",
            "upstream_departure_time",
            "arrival_time",
        ):
            visits[f"{column}_local"] = (
                visits[column]
                .dt.tz_convert(display_timezone)
                .dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            )
        figure.add_trace(
            go.Scattergl(
                x=visits["arrival_time"].dt.tz_convert(display_timezone).tolist(),
                y=visits[f"{metric}_seconds"],
                mode="markers",
                name=f"{label} visits",
                marker=dict(color=color, size=5, opacity=0.45),
                customdata=visits[
                    [
                        "trip_instance_key",
                        "from_stop_sequence",
                        "stop_sequence",
                        "upstream_arrival_time_local",
                        "upstream_departure_time_local",
                        "arrival_time_local",
                    ]
                ],
                hovertemplate=(
                    "%{x}<br>Journey: %{customdata[0]}"
                    "<br>Sequences: %{customdata[1]} -> %{customdata[2]}"
                    "<br>Upstream arrival: %{customdata[3]}"
                    "<br>Upstream departure: %{customdata[4]}"
                    "<br>Destination arrival: %{customdata[5]}"
                    f"<br>{label}: %{{y:.1f}} s<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )
        metric_label = "upstream dwell" if metric == "dwell" else metric
        figure.add_trace(
            go.Scatter(
                x=bin_times,
                y=timing.bins[f"median_{metric}_seconds"],
                mode="lines+markers",
                name=f"Median {metric_label} ({analysis.time_bin_size})",
                line=dict(color=color),
                connectgaps=False,
                customdata=timing.bins[[f"{metric}_count"]],
                hovertemplate=(
                    f"%{{x}}<br>Median {metric_label}: %{{y:.1f}} s"
                    "<br>Visits: %{customdata[0]}<extra></extra>"
                ),
            ),
            row=row,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=bin_times,
                y=timing.bins[f"baseline_{metric}_seconds"],
                mode="lines",
                name=f"Snapshot median {metric_label}",
                line=dict(color="darkorange", dash="dash"),
                connectgaps=False,
                hovertemplate=f"%{{x}}<br>Snapshot median {metric_label}: %{{y:.1f}} s<extra></extra>",
            ),
            row=row,
            col=1,
        )
        if visits.empty:
            figure.add_annotation(
                text=f"No usable {metric_label} observations: inspect timing audit",
                x=0.5,
                y=0.5,
                xref=f"x{row} domain",
                yref=f"y{row} domain",
                showarrow=False,
            )
    if pairs.empty:
        figure.add_annotation(
            text="No usable arrival-delay pairs: inspect delay audit",
            x=0.5,
            y=0.5,
            xref="x domain",
            yref="y domain",
            showarrow=False,
        )
    for row, title in enumerate(
        ("Delay change (min)", "Speed (km/h)", "Traversal (s)", "Upstream dwell (s)"),
        start=1,
    ):
        figure.update_yaxes(title_text=title, row=row, col=1)
    for row in (2, 3, 4):
        figure.update_yaxes(rangemode="tozero", row=row, col=1)
    figure.update_xaxes(
        range=[start.tz_convert(display_timezone), end.tz_convert(display_timezone)]
    )
    figure.update_xaxes(
        title_text=f"Time ({display_timezone})",
        row=4,
        col=1,
        rangeslider=dict(visible=True, thickness=0.06),
        rangeselector=dict(
            buttons=[
                dict(count=7, label="7d", step="day", stepmode="backward"),
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(label="All", step="all"),
            ]
        ),
    )
    figure.update_yaxes(fixedrange=False)
    segment = analysis.segment
    figure.update_layout(
        title=(
            f"Segment {segment['segment_id']}: delay, speed and non-overlapping durations"
            f"<br>{escape(str(segment['from_stop_name']))} -> "
            f"{escape(str(segment['to_stop_name']))} "
            f"(direction {segment['trip_direction_id']}, shape {escape(str(segment['shape_id']))})"
        ),
        height=1450,
        hovermode="x unified",
        hoversubplots="axis",
        legend=dict(orientation="h", x=0, xanchor="left", y=-0.20, yanchor="top"),
        margin=dict(t=140, b=320),
    )
    return figure
