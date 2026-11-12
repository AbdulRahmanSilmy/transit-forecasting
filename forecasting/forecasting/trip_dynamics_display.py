"""Aligned full-journey delay and speed-probe plots."""

from __future__ import annotations

from html import escape
from textwrap import wrap

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .trip_dynamics_eda import TripDynamicsEDA


def make_trip_dynamics_figure(
    analysis: TripDynamicsEDA,
    trip_instance_key: str,
    *,
    display_timezone: str = "America/Vancouver",
) -> go.Figure:
    """Plot one journey's delay evolution over its own scheduled stop sequence.

    Parameters
    ----------
    analysis : TripDynamicsEDA
        Prepared journey catalogue and scheduled-stop observations.
    trip_instance_key : str
        Eligible dated journey key from ``analysis.journeys``.
    display_timezone : str, optional
        Timezone for arrival-time hover labels.

    Returns
    -------
    plotly.graph_objects.Figure
        Three shared-stop panels: signed arrival delay, adjacent delay change,
        and mean journey probe speed versus segment median probe baseline.
        Missing scheduled observations remain gaps; no plot is shown or saved.

    Raises
    ------
    ValueError
        If the journey is absent or its scheduled observations are missing.
    """
    selected = analysis.journeys.loc[
        analysis.journeys["trip_instance_key"].eq(trip_instance_key)
    ]
    visits = analysis.visits.loc[
        analysis.visits["trip_instance_key"].eq(trip_instance_key)
    ].sort_values("position")
    if len(selected) != 1 or visits.empty:
        raise ValueError(f"No prepared eligible journey {trip_instance_key!r}.")
    journey = selected.iloc[0]
    hover = visits.assign(
        arrival_local=visits["arrival_time"]
        .dt.tz_convert(display_timezone)
        .dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
        upstream_minutes=visits["upstream_arrival_delay"] / 60,
        reported_minutes=visits["reported_arrival_delay"] / 60,
    )
    x = visits["position"].tolist()
    common = ["stop_name", "stop_id", "stop_sequence", "arrival_local"]
    endpoint_hover = (
        "%{customdata[0]} (%{customdata[1]})<br>Sequence: %{customdata[2]}"
        "<br>Reported arrival: %{customdata[3]}<br>"
    )
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        subplot_titles=(
            "Arrival delay: persistence, accumulation, and recovery",
            "Delay gained / recovered on each incoming segment",
            "This journey's mean probe speed vs segment median probe speed",
        ),
    )
    figure.add_trace(
        go.Scatter(
            x=x,
            y=visits["arrival_delay"] / 60,
            mode="lines+markers",
            name="Arrival delay (final reported proxy)",
            connectgaps=False,
            line=dict(color="royalblue"),
            customdata=hover[[*common, "exclusion_reason"]],
            hovertemplate=endpoint_hover
            + "Delay: %{y:.2f} min<br>Status: %{customdata[4]}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=x,
            y=visits["delta_delay_seconds"] / 60,
            name="Delay gained (+) / recovered (-)",
            marker_color=np.where(
                visits["delta_delay_seconds"].ge(0), "firebrick", "seagreen"
            ),
            customdata=hover[
                [
                    *common,
                    "upstream_minutes",
                    "reported_minutes",
                    "delta_exclusion_reason",
                ]
            ],
            hovertemplate=endpoint_hover
            + (
                "Upstream: %{customdata[4]:.2f} min<br>Current: %{customdata[5]:.2f} min"
                "<br>Change: %{y:.2f} min<br>Status: %{customdata[6]}<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=x,
            y=visits["observed_speed_kmh"],
            mode="lines+markers",
            name="Journey mean probe speed",
            connectgaps=False,
            line=dict(color="darkviolet"),
            customdata=hover[
                [*common, "probe_count", "first_probe_time", "last_probe_time"]
            ],
            hovertemplate=endpoint_hover
            + (
                "Speed: %{y:.2f} km/h<br>Probes: %{customdata[4]}"
                "<br>First / last probe (UTC): %{customdata[5]} / %{customdata[6]}<extra></extra>"
            ),
        ),
        row=3,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=x,
            y=visits["baseline_speed_kmh"],
            mode="lines+markers",
            name="Full-snapshot median probe speed",
            connectgaps=False,
            line=dict(color="darkorange", dash="dash"),
            customdata=hover[[*common, "segment_id", "baseline_probe_count"]],
            hovertemplate=endpoint_hover
            + (
                "Segment: %{customdata[4]}<br>Median speed: %{y:.2f} km/h"
                "<br>Baseline probes: %{customdata[5]}<extra></extra>"
            ),
        ),
        row=3,
        col=1,
    )
    for row in (1, 2):
        figure.add_hline(y=0, line_dash="dot", line_color="gray", row=row, col=1)
    for row, column, message in (
        (2, "delta_delay_seconds", "No usable adjacent delay pairs"),
        (3, "observed_speed_kmh", "No unambiguous journey speed probes"),
    ):
        if visits[column].notna().sum() == 0:
            figure.add_annotation(
                text=message,
                x=0.5,
                y=0.5,
                xref=f"x{row} domain",
                yref=f"y{row} domain",
                showarrow=False,
            )
    figure.update_yaxes(title_text="Arrival delay (min)", row=1, col=1)
    figure.update_yaxes(title_text="Delay change (min)", row=2, col=1)
    figure.update_yaxes(
        title_text="Probe speed (km/h)", rangemode="tozero", row=3, col=1
    )
    figure.update_yaxes(fixedrange=False)
    labels = []
    for row in visits.itertuples(index=False):
        name = "<br>".join(escape(line) for line in wrap(str(row.stop_name), width=24))
        labels.append(f"{row.stop_sequence}: {name}<br>({escape(str(row.stop_id))})")
    figure.update_xaxes(
        tickmode="array",
        tickvals=x,
        ticktext=labels,
        range=[-0.5, len(visits) - 0.5],
        fixedrange=False,
    )
    figure.update_xaxes(
        title_text="Scheduled stops in travel order",
        tickangle=-45,
        automargin=True,
        row=3,
        col=1,
    )
    coverage = (
        f"{journey['usable_stop_count']}/{journey['scheduled_stop_count']} usable stops"
        + (
            "; INCOMPLETE: observed maximum may be understated"
            if journey["incomplete"]
            else ""
        )
    )
    vehicle = (
        f"vehicle {escape(str(journey['vehicle_id']))}"
        if journey["vehicle_identity_status"] == "unique"
        else f"vehicle identity {journey['vehicle_identity_status']}"
    )
    figure.update_layout(
        title=(
            f"Journey {escape(str(trip_instance_key))} | shape {escape(str(journey['shape_id']))}"
            f" | direction {journey['trip_direction_id']}<br>"
            f"Maximum delay: {journey['max_delay_minutes']:.3f} min | {vehicle}<br>{coverage}"
        ),
        height=1100,
        hovermode="x unified",
        hoversubplots="axis",
        legend=dict(orientation="h", x=0, y=-0.52, yanchor="top"),
        margin=dict(t=170, b=340, l=115, r=40),
    )
    return figure
