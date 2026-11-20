"""Diagnostic figures for delay forecast harness runs.

All figures read the harness ``predictions`` frame (one row per scored visit,
``truth_delay_seconds`` plus ``pred_<method>`` columns) and show delays in
minutes. Every method in a figure is drawn from identical rows: those where
all plotted methods have a finite prediction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .delay_forecast_eval import segment_labels

# Reference categorical slots 1-4 in fixed order; observed delay uses ink, not a hue.
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
OBSERVED_COLOR = "#3d3d3a"
GRID_COLOR = "#e4e3dc"
TEMPLATE = "plotly_white"


def plot_methods(predictions: pd.DataFrame, candidate: str | None) -> list[str]:
    """Choose plotted methods in a fixed order: B0, candidate, B3, B2.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    candidate : str or None
        Candidate method, if one ran.

    Returns
    -------
    list of str
        Method names present in ``predictions``.
    """
    order = ["B0_transit_bc", candidate, "B3_calendar_median", "B2_segment_median"]
    return [m for m in order if m is not None and f"pred_{m}" in predictions]


def _label(method: str) -> str:
    """Readable legend label."""
    return {
        "B0_transit_bc": "Transit BC earliest (B0)",
        "B2_segment_median": "Segment median (B2)",
        "B3_calendar_median": "Calendar median (B3)",
    }.get(method, f"Candidate: {method}")


def _common(predictions: pd.DataFrame, methods: Sequence[str]) -> pd.DataFrame:
    """Rows where every plotted method is finite, with minute columns."""
    finite = np.all([np.isfinite(predictions[f"pred_{m}"]) for m in methods], axis=0)
    rows = predictions.loc[finite].copy()
    rows["actual_min"] = rows["truth_delay_seconds"] / 60
    for method in methods:
        rows[f"{method}_min"] = rows[f"pred_{method}"] / 60
    return rows


def _style(figure: go.Figure, title: str, height: int) -> go.Figure:
    """Shared layout: recessive grid, legend above the plot."""
    figure.update_layout(
        template=TEMPLATE,
        title=title,
        height=height,
        hovermode="closest",
        legend=dict(orientation="h", x=0, y=1.02, yanchor="bottom"),
        margin=dict(t=110, l=70, r=30, b=60),
    )
    figure.update_xaxes(gridcolor=GRID_COLOR, zeroline=False)
    figure.update_yaxes(gridcolor=GRID_COLOR, zerolinecolor="#c3c2b7")
    return figure


def make_daily_error_figure(
    predictions: pd.DataFrame,
    methods: Sequence[str],
    *,
    holidays: Iterable[pd.Timestamp] = (),
) -> go.Figure:
    """Daily MAE and daily mean delay per method across service days.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    methods : sequence of str
        Methods in fixed color order.
    holidays : iterable of pandas.Timestamp, optional
        Service days to shade (scored separately from the primary metric).

    Returns
    -------
    plotly.graph_objects.Figure
        Two rows sharing the date axis: daily MAE, then daily mean delay
        (observed in ink, each method in its series color).
    """
    rows = _common(predictions, methods)
    daily = rows.groupby("service_day")
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Daily MAE", "Daily mean arrival delay"),
    )
    support = daily.size()
    for color, method in zip(SERIES_COLORS, methods):
        error = (rows[f"{method}_min"] - rows["actual_min"]).abs()
        mae = error.groupby(rows["service_day"]).mean()
        figure.add_trace(
            go.Scatter(
                x=mae.index,
                y=mae,
                mode="lines+markers",
                name=_label(method),
                legendgroup=method,
                line=dict(color=color, width=2),
                marker=dict(size=8),
                customdata=support.reindex(mae.index),
                hovertemplate="%{x|%a %Y-%m-%d}<br>MAE %{y:.2f} min"
                "<br>%{customdata:,} visits<extra>%{fullData.name}</extra>",
            ),
            row=1,
            col=1,
        )
        mean = daily[f"{method}_min"].mean()
        figure.add_trace(
            go.Scatter(
                x=mean.index,
                y=mean,
                mode="lines+markers",
                name=_label(method),
                legendgroup=method,
                showlegend=False,
                line=dict(color=color, width=2),
                marker=dict(size=8),
                hovertemplate="%{x|%a %Y-%m-%d}<br>Mean predicted %{y:.2f} min"
                "<extra>%{fullData.name}</extra>",
            ),
            row=2,
            col=1,
        )
    observed = daily["actual_min"].mean()
    figure.add_trace(
        go.Scatter(
            x=observed.index,
            y=observed,
            mode="lines+markers",
            name="Observed (final report)",
            line=dict(color=OBSERVED_COLOR, width=2, dash="dot"),
            marker=dict(size=8),
            hovertemplate="%{x|%a %Y-%m-%d}<br>Mean observed %{y:.2f} min<extra></extra>",
        ),
        row=2,
        col=1,
    )
    for day in holidays:
        day = pd.Timestamp(day)
        if day in support.index:
            figure.add_vrect(
                x0=day - pd.Timedelta(hours=12),
                x1=day + pd.Timedelta(hours=12),
                fillcolor="#c3c2b7",
                opacity=0.3,
                line_width=0,
                annotation_text="holiday",
                annotation_position="top left",
            )
    figure.update_yaxes(title_text="MAE (min)", rangemode="tozero", row=1, col=1)
    figure.update_yaxes(title_text="Delay (min)", row=2, col=1)
    figure.update_xaxes(title_text="Service day", row=2, col=1)
    return _style(figure, "Forecast error and mean delay by service day", 760)


def make_stop_timeseries_figure(
    predictions: pd.DataFrame,
    methods: Sequence[str],
    *,
    stop_sequence: int,
    timezone: str,
) -> go.Figure:
    """Per-visit observed and predicted delay at one stop over time.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    methods : sequence of str
        Methods in fixed color order.
    stop_sequence : int
        Stop to show; later stops accumulate more chained error.
    timezone : str
        Display timezone for scheduled arrival.

    Returns
    -------
    plotly.graph_objects.Figure
        Markers at each visit's scheduled arrival, with range buttons.
    """
    rows = _common(predictions, methods)
    rows = rows.loc[rows["stop_sequence"].eq(stop_sequence)].sort_values(
        "scheduled_arrival"
    )
    clock = rows["scheduled_arrival"].dt.tz_convert(timezone).dt.tz_localize(None)
    figure = go.Figure()
    figure.add_trace(
        go.Scattergl(
            x=clock,
            y=rows["actual_min"],
            mode="markers",
            name="Observed (final report)",
            marker=dict(color=OBSERVED_COLOR, size=8, symbol="circle-open"),
            customdata=rows[["trip_instance_key", "lead_minutes"]],
            hovertemplate="%{x|%a %m-%d %H:%M}<br>Observed %{y:.2f} min"
            "<br>%{customdata[0]}<br>Lead %{customdata[1]:.0f} min<extra></extra>",
        )
    )
    for color, method in zip(SERIES_COLORS, methods):
        figure.add_trace(
            go.Scattergl(
                x=clock,
                y=rows[f"{method}_min"],
                mode="markers",
                name=_label(method),
                marker=dict(color=color, size=8, opacity=0.75),
                hovertemplate="%{x|%a %m-%d %H:%M}<br>Predicted %{y:.2f} min"
                "<extra>%{fullData.name}</extra>",
            )
        )
    stop_id = rows["stop_id"].iloc[0] if len(rows) else "?"
    figure.update_xaxes(
        title_text=f"Scheduled arrival ({timezone})",
        rangeselector=dict(
            x=1,
            xanchor="right",
            y=1.01,
            yanchor="bottom",
            buttons=[
                dict(count=1, label="1 day", step="day", stepmode="backward"),
                dict(count=7, label="1 week", step="day", stepmode="backward"),
                dict(label="All", step="all"),
            ],
        ),
    )
    figure.update_yaxes(title_text="Arrival delay (min)", fixedrange=False)
    return _style(
        figure,
        f"Observed vs predicted delay at stop sequence {stop_sequence} "
        f"(stop {stop_id}), {len(rows):,} visits",
        620,
    )


def make_hourly_error_figure(
    predictions: pd.DataFrame,
    methods: Sequence[str],
    *,
    timezone: str,
) -> go.Figure:
    """MAE and bias by scheduled local hour, showing peak-period errors.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    methods : sequence of str
        Methods in fixed color order.
    timezone : str
        Timezone for the hour of day.

    Returns
    -------
    plotly.graph_objects.Figure
        Two rows sharing the hour axis: MAE, then mean signed error
        (positive means the method predicted later than observed).
    """
    rows = _common(predictions, methods)
    clock = (
        rows["calendar_arrival"]
        if "calendar_arrival" in rows
        else rows["scheduled_arrival"]
    )
    hour = clock.dt.tz_convert(timezone).dt.hour
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("MAE by hour", "Bias by hour (predicted - observed)"),
    )
    support = hour.value_counts().sort_index()
    for color, method in zip(SERIES_COLORS, methods):
        error = rows[f"{method}_min"] - rows["actual_min"]
        for row, values, name in (
            (1, error.abs().groupby(hour).mean(), "MAE"),
            (2, error.groupby(hour).mean(), "Bias"),
        ):
            figure.add_trace(
                go.Scatter(
                    x=values.index,
                    y=values,
                    mode="lines+markers",
                    name=_label(method),
                    legendgroup=method,
                    showlegend=row == 1,
                    line=dict(color=color, width=2),
                    marker=dict(size=8),
                    customdata=support.reindex(values.index),
                    hovertemplate=f"%{{x}}:00<br>{name} %{{y:.2f}} min"
                    "<br>%{customdata:,} visits<extra>%{fullData.name}</extra>",
                ),
                row=row,
                col=1,
            )
    figure.add_hline(y=0, line_color="#c3c2b7", row=2, col=1)
    figure.update_yaxes(title_text="MAE (min)", rangemode="tozero", row=1, col=1)
    figure.update_yaxes(title_text="Bias (min)", row=2, col=1)
    figure.update_xaxes(
        title_text=f"Scheduled arrival hour ({timezone})", dtick=1, row=2, col=1
    )
    return _style(figure, "Error by time of day", 760)


def make_actual_vs_predicted_figure(
    predictions: pd.DataFrame,
    methods: Sequence[str],
) -> go.Figure:
    """Small multiples of predicted (x) vs observed (y) delay with a 1:1 line.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    methods : sequence of str
        Methods in fixed color order, one panel each.

    Returns
    -------
    plotly.graph_objects.Figure
        Panels share equal axis ranges; titles state each method's MAE.
    """
    rows = _common(predictions, methods)
    values = rows[["actual_min"] + [f"{m}_min" for m in methods]]
    low, high = np.nanpercentile(values.to_numpy(), [0.5, 99.5])
    pad = max(0.05 * (high - low), 0.5)
    bounds = [low - pad, high + pad]
    titles = [
        f"{_label(m)}<br>MAE {(rows[f'{m}_min'] - rows['actual_min']).abs().mean():.2f} min"
        for m in methods
    ]
    columns = min(len(methods), 2)
    figure = make_subplots(
        rows=int(np.ceil(len(methods) / columns)),
        cols=columns,
        subplot_titles=titles,
        horizontal_spacing=0.1,
        vertical_spacing=0.14,
    )
    for i, (color, method) in enumerate(zip(SERIES_COLORS, methods)):
        row, col = i // columns + 1, i % columns + 1
        figure.add_trace(
            go.Scattergl(
                x=rows[f"{method}_min"],
                y=rows["actual_min"],
                mode="markers",
                name=_label(method),
                marker=dict(color=color, size=8, opacity=0.35),
                customdata=rows[["trip_instance_key", "stop_sequence"]],
                hovertemplate="Predicted %{x:.2f} min<br>Observed %{y:.2f} min"
                "<br>%{customdata[0]} stop %{customdata[1]}"
                "<extra>%{fullData.name}</extra>",
            ),
            row=row,
            col=col,
        )
        figure.add_trace(
            go.Scatter(
                x=bounds,
                y=bounds,
                mode="lines",
                name="1:1 (perfect)",
                showlegend=i == 0,
                line=dict(color=OBSERVED_COLOR, dash="dash", width=2),
                hoverinfo="skip",
            ),
            row=row,
            col=col,
        )
        figure.update_xaxes(
            title_text="Predicted delay (min)", range=bounds, row=row, col=col
        )
        figure.update_yaxes(
            title_text="Observed delay (min)", range=bounds, row=row, col=col
        )
    figure = _style(
        figure,
        f"Predicted vs observed arrival delay ({len(rows):,} visits; axes clipped to "
        "0.5-99.5th percentile; dashed line is 1:1)",
        480 * int(np.ceil(len(methods) / columns)) + 80,
    )
    figure.update_layout(showlegend=False, margin=dict(t=90))
    return figure


def _common_traversals(
    traversals: pd.DataFrame, methods: Sequence[str]
) -> pd.DataFrame:
    """Traversal rows where every plotted method is finite, in minutes."""
    finite = np.all(
        [np.isfinite(traversals[f"trav_{m}"]) for m in methods], axis=0
    ) & np.isfinite(traversals["actual_traversal_s"])
    rows = traversals.loc[finite].copy()
    rows["actual_min"] = rows["actual_traversal_s"] / 60
    for method in methods:
        rows[f"{method}_trav_min"] = rows[f"trav_{method}"] / 60
    return rows


def _segment_name(label: str) -> str:
    """File-safe figure name from a segment label like ``seq 4: a -> b``."""
    sequence, stops = label.split(":", 1)
    from_stop, to_stop = (part.strip() for part in stops.split("->"))
    return f"segment_{int(sequence.split()[1]):02d}_{from_stop}-{to_stop}"


def make_segment_error_overview(
    predictions: pd.DataFrame,
    traversals: pd.DataFrame,
    methods: Sequence[str],
) -> go.Figure:
    """Traversal MAE per segment next to arrival-delay MAE per stop.

    Delay error at stop *k* accumulates the traversal and dwell errors of
    every segment before it, so the two rows show where error enters and how
    it builds up along the route.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness visit predictions.
    traversals : pandas.DataFrame
        Harness traversal rows.
    methods : sequence of str
        Methods in fixed color order.

    Returns
    -------
    plotly.graph_objects.Figure
        Two rows sharing the downstream stop-sequence axis.
    """
    links = _common_traversals(traversals, methods)
    visits = _common(predictions, methods)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.1,
        subplot_titles=(
            "Traversal MAE of the segment ending at each stop",
            "Arrival-delay MAE at each stop (accumulated along the route)",
        ),
    )
    for color, method in zip(SERIES_COLORS, methods):
        for row, frame, column in (
            (1, links, f"{method}_trav_min"),
            (2, visits, f"{method}_min"),
        ):
            error = (frame[column] - frame["actual_min"]).abs()
            mae = error.groupby(frame["stop_sequence"]).mean()
            support = frame["stop_sequence"].value_counts().reindex(mae.index)
            figure.add_trace(
                go.Scatter(
                    x=mae.index,
                    y=mae,
                    mode="lines+markers",
                    name=_label(method),
                    legendgroup=method,
                    showlegend=row == 1,
                    line=dict(color=color, width=2),
                    marker=dict(size=8),
                    customdata=support,
                    hovertemplate="Stop sequence %{x}<br>MAE %{y:.2f} min"
                    "<br>%{customdata:,} rows<extra>%{fullData.name}</extra>",
                ),
                row=row,
                col=1,
            )
    figure.update_yaxes(title_text="Traversal MAE (min)", rangemode="tozero", row=1)
    figure.update_yaxes(title_text="Delay MAE (min)", rangemode="tozero", row=2)
    figure.update_xaxes(title_text="Downstream stop sequence", dtick=1, row=2, col=1)
    return _style(figure, "Where forecast error enters: segments vs stops", 760)


def make_segment_timeseries_figure(
    predictions: pd.DataFrame,
    traversals: pd.DataFrame,
    methods: Sequence[str],
    *,
    segment_key: str,
    label: str,
    timezone: str,
) -> go.Figure:
    """Traversal, upstream dwell and downstream delay over time for a segment.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness visit predictions.
    traversals : pandas.DataFrame
        Harness traversal rows.
    methods : sequence of str
        Methods in fixed color order.
    segment_key : str
        Segment to show.
    label : str
        Readable segment label for the title.
    timezone : str
        Display timezone.

    Returns
    -------
    plotly.graph_objects.Figure
        Three rows sharing the scheduled upstream departure axis: traversal,
        upstream dwell, and arrival delay at the downstream stop; observed
        values in ink, predictions in series colors.
    """
    links = _common_traversals(
        traversals.loc[traversals["segment_key"].eq(segment_key)], methods
    ).sort_values("feed_scheduled_departure")
    clock = (
        links["feed_scheduled_departure"].dt.tz_convert(timezone).dt.tz_localize(None)
    )
    visits = _common(predictions, methods).merge(
        links[["trip_instance_key", "stop_sequence", "feed_scheduled_departure"]],
        on=["trip_instance_key", "stop_sequence"],
    )
    visit_clock = (
        visits["feed_scheduled_departure"].dt.tz_convert(timezone).dt.tz_localize(None)
    )
    figure = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=(
            "Traversal time (upstream departure to arrival)",
            "Dwell at the upstream stop",
            "Arrival delay at the downstream stop",
        ),
    )
    observed = dict(color=OBSERVED_COLOR, size=8, symbol="circle-open")
    hover = "%{x|%a %m-%d %H:%M}<br>%{y:.2f} min<extra>%{fullData.name}</extra>"
    figure.add_trace(
        go.Scattergl(
            x=clock,
            y=links["actual_min"],
            mode="markers",
            name="Observed",
            legendgroup="observed",
            marker=observed,
            hovertemplate=hover,
        ),
        row=1,
        col=1,
    )
    for color, method in zip(SERIES_COLORS, methods):
        figure.add_trace(
            go.Scattergl(
                x=clock,
                y=links[f"{method}_trav_min"],
                mode="markers",
                name=_label(method),
                legendgroup=method,
                marker=dict(color=color, size=8, opacity=0.75),
                hovertemplate=hover,
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scattergl(
                x=visit_clock,
                y=visits[f"{method}_min"],
                mode="markers",
                name=_label(method),
                legendgroup=method,
                showlegend=False,
                marker=dict(color=color, size=8, opacity=0.75),
                hovertemplate=hover,
            ),
            row=3,
            col=1,
        )
    dwell = links.loc[~links["upstream_is_first_stop"]]
    dwell_clock = clock.loc[dwell.index]
    for name, values, marker in (
        ("Observed", dwell["actual_dwell_s"] / 60, observed),
        (
            "Dwell: scheduled (Transit BC)",
            dwell["dwell_B0_transit_bc"] / 60,
            dict(color=SERIES_COLORS[0], size=8, opacity=0.75),
        ),
        (
            "Dwell: stop median (our models)",
            dwell["dwell_model"] / 60,
            dict(color=SERIES_COLORS[1], size=8, opacity=0.75, symbol="diamond"),
        ),
    ):
        figure.add_trace(
            go.Scattergl(
                x=dwell_clock,
                y=values,
                mode="markers",
                name=name,
                legendgroup="observed" if name == "Observed" else name,
                showlegend=name != "Observed",
                marker=marker,
                hovertemplate=hover,
            ),
            row=2,
            col=1,
        )
    figure.add_trace(
        go.Scattergl(
            x=visit_clock,
            y=visits["actual_min"],
            mode="markers",
            name="Observed",
            legendgroup="observed",
            showlegend=False,
            marker=observed,
            hovertemplate=hover,
        ),
        row=3,
        col=1,
    )
    if dwell.empty:
        figure.add_annotation(
            text="Upstream stop is the journey's first stop: no dwell modelled",
            x=0.5,
            y=0.5,
            xref="x2 domain",
            yref="y2 domain",
            showarrow=False,
        )
    figure.update_yaxes(title_text="Traversal (min)", rangemode="tozero", row=1)
    figure.update_yaxes(title_text="Dwell (min)", rangemode="tozero", row=2)
    figure.update_yaxes(title_text="Delay (min)", row=3)
    figure.update_xaxes(
        title_text=f"Scheduled upstream departure, feed clock ({timezone})",
        row=3,
        col=1,
    )
    return _style(figure, f"Segment {label}: {len(links):,} traversals", 980)


def make_segment_traversal_scatter(
    traversals: pd.DataFrame,
    methods: Sequence[str],
    *,
    segment_key: str,
    label: str,
) -> go.Figure:
    """Small multiples of predicted (x) vs observed (y) traversal for a segment.

    Parameters
    ----------
    traversals : pandas.DataFrame
        Harness traversal rows.
    methods : sequence of str
        Methods in fixed color order, one panel each.
    segment_key : str
        Segment to show.
    label : str
        Readable segment label for the title.

    Returns
    -------
    plotly.graph_objects.Figure
        Panels with equal axis ranges, a dashed 1:1 line and each method's
        traversal MAE in the panel title.
    """
    rows = _common_traversals(
        traversals.loc[traversals["segment_key"].eq(segment_key)], methods
    )
    values = rows[["actual_min"] + [f"{m}_trav_min" for m in methods]].to_numpy()
    low, high = np.nanpercentile(values, [0.5, 99.5]) if len(rows) else (0, 1)
    pad = max(0.05 * (high - low), 0.25)
    bounds = [max(low - pad, 0), high + pad]
    titles = [
        f"{_label(m)}<br>MAE "
        f"{(rows[f'{m}_trav_min'] - rows['actual_min']).abs().mean():.2f} min"
        for m in methods
    ]
    columns = min(len(methods), 2)
    figure = make_subplots(
        rows=int(np.ceil(len(methods) / columns)),
        cols=columns,
        subplot_titles=titles,
        horizontal_spacing=0.1,
        vertical_spacing=0.16,
    )
    for i, (color, method) in enumerate(zip(SERIES_COLORS, methods)):
        row, col = i // columns + 1, i % columns + 1
        figure.add_trace(
            go.Scattergl(
                x=rows[f"{method}_trav_min"],
                y=rows["actual_min"],
                mode="markers",
                name=_label(method),
                marker=dict(color=color, size=8, opacity=0.4),
                hovertemplate="Predicted %{x:.2f} min<br>Observed %{y:.2f} min"
                "<extra>%{fullData.name}</extra>",
            ),
            row=row,
            col=col,
        )
        figure.add_trace(
            go.Scatter(
                x=bounds,
                y=bounds,
                mode="lines",
                line=dict(color=OBSERVED_COLOR, dash="dash", width=2),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=row,
            col=col,
        )
        figure.update_xaxes(
            title_text="Predicted traversal (min)", range=bounds, row=row, col=col
        )
        figure.update_yaxes(
            title_text="Observed traversal (min)", range=bounds, row=row, col=col
        )
    figure = _style(
        figure,
        f"Segment {label}: predicted vs observed traversal ({len(rows):,}; "
        "axes clipped to 0.5-99.5th percentile; dashed line is 1:1)",
        460 * int(np.ceil(len(methods) / columns)) + 80,
    )
    figure.update_layout(showlegend=False, margin=dict(t=90))
    return figure


def make_run_figures(
    predictions: pd.DataFrame,
    *,
    candidate: str | None,
    timezone: str,
    holidays: Iterable[pd.Timestamp] = (),
    traversals: pd.DataFrame | None = None,
) -> dict[str, go.Figure]:
    """Build the standard diagnostic figures for one harness run.

    Parameters
    ----------
    predictions : pandas.DataFrame
        Harness predictions.
    candidate : str or None
        Candidate method, if one ran.
    timezone : str
        Display timezone.
    holidays : iterable of pandas.Timestamp, optional
        Holidays to shade on the daily figure.
    traversals : pandas.DataFrame or None, optional
        Harness traversal rows; adds ``segment_error_overview`` and, per
        segment, ``segment_NN_<from>-<to>_timeseries`` and
        ``segment_NN_<from>-<to>_traversal_scatter``.

    Returns
    -------
    dict of str to plotly.graph_objects.Figure
        ``daily_error`` (all days, holidays shaded), plus ``hourly_error``,
        ``actual_vs_predicted``, one ``timeseries_stop_<n>`` for the second
        and last scored stops and the segment figures, which exclude holidays
        like the primary metric.
    """
    methods = plot_methods(predictions, candidate)
    holidays = [pd.Timestamp(day) for day in holidays]
    primary = predictions.loc[~predictions["service_day"].isin(holidays)]
    stops = sorted(primary["stop_sequence"].unique())
    figures = {
        "daily_error": make_daily_error_figure(predictions, methods, holidays=holidays),
        "hourly_error": make_hourly_error_figure(primary, methods, timezone=timezone),
        "actual_vs_predicted": make_actual_vs_predicted_figure(primary, methods),
    }
    for stop in dict.fromkeys([stops[0], stops[-1]]):
        figures[f"timeseries_stop_{stop}"] = make_stop_timeseries_figure(
            primary, methods, stop_sequence=int(stop), timezone=timezone
        )
    if traversals is not None and not traversals.empty:
        links = traversals.loc[~traversals["service_day"].isin(holidays)]
        figures["segment_error_overview"] = make_segment_error_overview(
            primary, links, methods
        )
        for segment_key, label in segment_labels(links).items():
            name = _segment_name(label)
            figures[f"{name}_timeseries"] = make_segment_timeseries_figure(
                primary,
                links,
                methods,
                segment_key=segment_key,
                label=label,
                timezone=timezone,
            )
            figures[f"{name}_traversal_scatter"] = make_segment_traversal_scatter(
                links, methods, segment_key=segment_key, label=label
            )
    return figures
