"""Traversal comparison plots with explicit cohorts and forecast lead times."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .road_segment_congestion import _require_columns


def _validate_values(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    """Require nonempty, finite numeric plotting values.

    Parameters
    ----------
    frame : pandas.DataFrame
        Comparison observations.
    columns : sequence of str
        Required numeric columns.

    Raises
    ------
    ValueError
        If observations or finite numeric values are unavailable.
    """
    _require_columns(frame, columns, "comparison")
    if frame.empty:
        raise ValueError("No traversal comparisons available for plotting.")
    if not np.isfinite(frame[list(columns)].to_numpy(dtype=float)).all():
        raise ValueError("Traversal comparison plots require finite numeric values.")


def make_traversal_comparison_timeseries(
    comparison: pd.DataFrame,
    *,
    title: str,
    timezone: str = "America/Vancouver",
) -> go.Figure:
    """Plot traversal estimates and lead before scheduled downstream arrival.

    Parameters
    ----------
    comparison : pandas.DataFrame
        Paired observations with reference, early-feed and harmonic traversals
        in seconds; aware early scheduled endpoint times and origin; and
        ``early_scheduled_arrival_lead_minutes``.
    title : str
        Title identifying training (in-sample) or reserved-week evaluation.
    timezone : str, default "America/Vancouver"
        Local display timezone. Internal forecast identity remains in UTC.

    Returns
    -------
    plotly.graph_objects.Figure
        Three traversal traces above one earliest-feed lead trace, with shared
        local-calendar x axes, a bottom range slider and unlocked vertical axes.
        Lead is early scheduled downstream arrival minus publication origin,
        not time until final observed arrival/departure. Inputs are unchanged.

    Raises
    ------
    ValueError
        For empty, missing, nonfinite or timezone-naive plotting data.
    """
    _validate_values(
        comparison,
        [
            "reference_traversal_seconds",
            "early_implied_traversal_seconds",
            "harmonic_traversal_seconds",
            "early_scheduled_arrival_lead_minutes",
        ],
    )
    clocks = [
        "early_scheduled_upstream_at",
        "early_scheduled_downstream_at",
        "origin_time",
    ]
    _require_columns(comparison, ["trip_instance_key", *clocks], "comparison")
    for column in clocks:
        if (
            not isinstance(comparison[column].dtype, pd.DatetimeTZDtype)
            or comparison[column].isna().any()
        ):
            raise ValueError(
                f"{column} must contain nonmissing timezone-aware timestamps."
            )
    visits = comparison.sort_values("early_scheduled_upstream_at", kind="stable")
    times = (
        visits["early_scheduled_upstream_at"]
        .dt.tz_convert(timezone)
        .dt.tz_localize(None)
        .tolist()
    )
    customdata = np.column_stack(
        [
            visits["trip_instance_key"].to_numpy(),
            visits["origin_time"]
            .dt.tz_convert(timezone)
            .dt.strftime("%Y-%m-%d %H:%M:%S"),
            visits["early_scheduled_downstream_at"]
            .dt.tz_convert(timezone)
            .dt.strftime("%Y-%m-%d %H:%M:%S"),
            visits["early_scheduled_arrival_lead_minutes"].to_numpy(),
        ]
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.14,
        subplot_titles=(
            "Traversal estimates and latest-update reference",
            "Earliest-feed lead before scheduled downstream arrival",
        ),
    )
    for name, column, color in (
        ("Latest-update reference", "reference_traversal_seconds", "black"),
        ("Earliest feed (zero dwell)", "early_implied_traversal_seconds", "darkorange"),
        ("Harmonic (early scheduled clock)", "harmonic_traversal_seconds", "royalblue"),
    ):
        figure.add_trace(
            go.Scattergl(
                x=times,
                y=visits[column] / 60,
                mode="markers",
                name=name,
                marker=dict(size=5, opacity=0.6, color=color),
                customdata=customdata,
                hovertemplate=(
                    "%{x}<br>Traversal: %{y:.2f} min<br>Journey: %{customdata[0]}"
                    "<br>Published: %{customdata[1]}<br>Scheduled downstream: %{customdata[2]}"
                    "<br>Scheduled-arrival lead: %{customdata[3]:.1f} min"
                    "<extra>%{fullData.name}</extra>"
                ),
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scattergl(
            x=times,
            y=visits["early_scheduled_arrival_lead_minutes"],
            mode="markers",
            name="Earliest-feed scheduled-arrival lead",
            showlegend=False,
            marker=dict(size=5, color="darkorange"),
            customdata=customdata,
            hovertemplate=(
                "%{x}<br>Journey: %{customdata[0]}<br>Published: %{customdata[1]}"
                "<br>Scheduled downstream: %{customdata[2]}"
                "<br>Lead: %{y:.1f} min<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_dash="dot", line_color="gray", row=2, col=1)
    figure.update_yaxes(title_text="Traversal (min)", row=1, col=1)
    figure.update_yaxes(title_text="Forecast lead (min)", row=2, col=1)
    figure.update_xaxes(
        title_text=f"Early-implied scheduled upstream time ({timezone})",
        rangeslider=dict(visible=True, thickness=0.08),
        row=2,
        col=1,
    )
    figure.update_layout(
        title=dict(text=title, x=0.05, y=0.98, yanchor="top"),
        height=850,
        margin=dict(t=180, b=130),
        legend=dict(orientation="h", x=0, y=1.08, yanchor="bottom"),
    )
    figure.update_yaxes(fixedrange=False)
    return figure


def make_traversal_prediction_scatter(
    training: pd.DataFrame,
    evaluation: pd.DataFrame,
    *,
    prediction_column: str,
    method_name: str,
) -> go.Figure:
    """Compare a method's predicted traversal with references in separate panels.

    Parameters
    ----------
    training, evaluation : pandas.DataFrame
        Paired training and reserved-week observations. References are in
        ``reference_traversal_seconds``; predictions use ``prediction_column``.
        Trip-instance keys identify points. Finite negative predictions remain.
    prediction_column : str
        Traversal prediction column in seconds.
    method_name : str
        Method label for the figure title.

    Returns
    -------
    plotly.graph_objects.Figure
        Two panels: training (in-sample) and reserved week. X is predicted
        traversal and Y is the latest-reference traversal, both in minutes.
        Each panel has equal unit scales, a 1:1 line, and its own count, MAE,
        median absolute error, RMSE and signed bias (prediction minus reference).
        Error metrics are in minutes, displayed below the panels. Inputs are
        unchanged.

    Raises
    ------
    ValueError
        For empty, missing or nonfinite comparison values.
    """
    numeric_columns = [prediction_column, "reference_traversal_seconds"]
    for frame in (training, evaluation):
        _validate_values(frame, numeric_columns)
        _require_columns(frame, ["trip_instance_key"], "comparison")
    values = np.concatenate(
        [
            frame[numeric_columns].to_numpy(dtype=float).ravel() / 60
            for frame in (training, evaluation)
        ]
    )
    low, high = min(0.0, float(values.min())), max(0.1, float(values.max()))
    padding = (high - low) * 0.05
    bounds = [low - padding if low < 0 else 0.0, high + padding]
    figure = make_subplots(
        rows=1,
        cols=2,
        horizontal_spacing=0.14,
        subplot_titles=("Training (in-sample)", "Reserved week"),
    )
    for column, (frame, color) in enumerate(
        ((training, "royalblue"), (evaluation, "darkorange")),
        start=1,
    ):
        figure.add_trace(
            go.Scattergl(
                x=frame[prediction_column] / 60,
                y=frame["reference_traversal_seconds"] / 60,
                mode="markers",
                showlegend=False,
                marker=dict(size=5, opacity=0.6, color=color),
                customdata=frame[["trip_instance_key"]],
                hovertemplate=(
                    "Prediction: %{x:.2f} min<br>Latest reference: %{y:.2f} min"
                    "<br>Journey: %{customdata[0]}<extra></extra>"
                ),
            ),
            row=1,
            col=column,
        )
        figure.add_shape(
            type="line",
            x0=bounds[0],
            y0=bounds[0],
            x1=bounds[1],
            y1=bounds[1],
            line=dict(dash="dash", color="gray"),
            row=1,
            col=column,
        )
        figure.update_xaxes(
            title_text="Predicted traversal (min)",
            range=bounds,
            constrain="domain",
            fixedrange=False,
            row=1,
            col=column,
        )
        anchor = "x" if column == 1 else "x2"
        figure.update_yaxes(
            title_text="Latest-reference traversal (min)",
            range=bounds,
            scaleanchor=anchor,
            scaleratio=1,
            constrain="domain",
            fixedrange=False,
            row=1,
            col=column,
        )
        error = (
            frame[prediction_column].to_numpy(dtype=float)
            - frame["reference_traversal_seconds"].to_numpy(dtype=float)
        ) / 60
        absolute_error = np.abs(error)
        figure.add_annotation(
            xref=f"{anchor} domain",
            yref="paper",
            x=0.5,
            y=-0.16,
            xanchor="center",
            yanchor="top",
            showarrow=False,
            align="center",
            font=dict(size=11),
            text=(
                f"<b>N={len(frame):,}</b>"
                f"<br>MAE={absolute_error.mean():.3f} | "
                f"MedAE={np.median(absolute_error):.3f} min"
                f"<br>RMSE={np.sqrt(np.mean(error ** 2)):.3f} | "
                f"Bias={error.mean():+.3f} min"
            ),
        )
    figure.update_layout(
        title=dict(
            text=f"{method_name}: predicted versus latest-reference traversal",
            x=0.05,
            y=0.98,
            yanchor="top",
        ),
        height=620,
        margin=dict(t=110, b=155),
    )
    return figure
