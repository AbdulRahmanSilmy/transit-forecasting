"""Plotly diagnostics for upstream congestion and stop forecast residuals."""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .stop_congestion_analysis import (
    StopCongestionAnalysis,
    _rank_correlation,
    summarize_stop_congestion_signal,
)

_COLORS = {"corridor": "royalblue", "remaining": "darkorange"}
_LABELS = {
    "corridor": "Whole upstream corridor",
    "remaining": "Remaining upstream path",
}
_FEATURES = {
    "mean_congestion": "Mean",
    "worst_congestion": "Worst segment",
    "trailing_mean_congestion": "Trailing 15-min mean",
    "congestion_change": "15-min change",
}
_CAVEAT = "Feasibility only: full-period baseline; final-reported arrival/delay proxies"


def _primary(analysis: StopCongestionAnalysis, max_age_minutes: int) -> pd.DataFrame:
    """Select an explicitly configured freshness limit.

    Parameters
    ----------
    analysis : StopCongestionAnalysis
        Prepared scoped summaries.
    max_age_minutes : int
        One of the prepared observation-age limits.

    Returns
    -------
    pandas.DataFrame
        A copy of the summaries at the requested limit.

    Raises
    ------
    ValueError
        If the requested freshness limit was not prepared.
    """
    if max_age_minutes not in analysis.comparisons["max_age_minutes"].unique():
        raise ValueError(
            "max_age_minutes must be one of the prepared freshness limits."
        )
    return analysis.comparisons.loc[
        analysis.comparisons["max_age_minutes"].eq(max_age_minutes)
    ].copy()


def make_stop_congestion_example(
    analysis: StopCongestionAnalysis,
    visit_index: int,
    *,
    max_age_minutes: int = 15,
) -> go.Figure:
    """Build aligned prediction, residual, segment, and coverage diagnostics.

    Parameters
    ----------
    analysis : StopCongestionAnalysis
        Prepared stop comparisons with inspectable provenance.
    visit_index : int
        Existing notebook visit index, never a post-filter row number.
    max_age_minutes : int, optional
        Prepared freshness limit, default 15 minutes.

    Returns
    -------
    plotly.graph_objects.Figure
        Five aligned panels. Missing heatmap cells remain blank. All congestion
        summaries describe the observed portion of the named path. Known
        excluded visits retain their delay/reference curves and show explicit
        unavailable-congestion panels, without substituting a path or visit.

    Raises
    ------
    ValueError
        If the visit is absent from the prepared references, the prepared
        analysis is inconsistent, or the freshness limit is absent.

    Warns
    -----
    UserWarning
        If the selected visit has no verified upstream path. Its congestion
        panels are marked unavailable rather than displaying another trip.
    """
    primary = _primary(analysis, max_age_minutes)
    example = primary.loc[primary["visit_index"].eq(visit_index)]
    references = analysis.reference_horizons.loc[
        analysis.reference_horizons["visit_index"].eq(visit_index)
    ]
    if references.empty:
        raise ValueError(
            f"Visit {visit_index} is not in the prepared reference visits. "
            "Rerun congestion preparation after changing the stop or dataset."
        )
    reason = references.iloc[0]["path_exclusion_reason"]
    has_path = not example.empty
    if has_path:
        corridor = example.loc[example["scope"].eq("corridor")]
    else:
        corridor = references
        if corridor["path_available"].any():
            raise ValueError(
                f"Visit {visit_index} has a path but no prepared comparisons; "
                "rerun congestion preparation."
            )
        warnings.warn(
            f"Visit {visit_index}: upstream congestion unavailable. {reason} "
            "Showing delay/reference curves only; no other visit or path is substituted.",
            UserWarning,
            stacklevel=2,
        )
    corridor = corridor.sort_values("horizon_minutes")
    figure = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.17, 0.13, 0.34, 0.18, 0.18],
        vertical_spacing=0.055,
        subplot_titles=(
            "Published prediction and final-reported reference",
            "Remaining error: final delay minus prediction (positive = underestimated)",
            "Upstream congestion in travel order; blank = no fresh, available bin",
            "Mean congestion over observed segments (not total travel-time delay)",
            "Observed segments / all segments in each path",
        ),
    )
    for column, label, color in (
        ("true_delay_minutes", "Final reported delay (proxy)", "black"),
        ("estimated_delay_minutes", "BC Transit published prediction", "royalblue"),
    ):
        figure.add_trace(
            go.Scatter(
                x=corridor["horizon_minutes"],
                y=corridor[column],
                mode="lines+markers",
                name=label,
                line={"color": color},
                connectgaps=False,
            ),
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=corridor["horizon_minutes"],
            y=corridor["remaining_delay_error_minutes"],
            mode="lines+markers",
            name="Remaining signed error",
            line={"color": "firebrick"},
            connectgaps=False,
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_dash="dash", row=2, col=1)
    if has_path:
        segments = analysis.segment_comparisons.loc[
            analysis.segment_comparisons["visit_index"].eq(visit_index)
        ].copy()
        segments["fresh_congestion"] = segments["congestion_index"].where(
            segments["observation_age_minutes"].between(0, max_age_minutes)
        )
        grid = segments.pivot(
            index="path_order", columns="horizon_minutes", values="fresh_congestion"
        ).sort_index()
        path = analysis.paths.loc[
            analysis.paths["visit_index"].eq(visit_index)
        ].sort_values("path_order")
        hover = np.empty(grid.shape, dtype=object)
        for row_index, order in enumerate(grid.index):
            for column_index, horizon in enumerate(grid.columns):
                point = segments.loc[
                    segments["path_order"].eq(order)
                    & segments["horizon_minutes"].eq(horizon)
                ].iloc[0]
                hover[row_index, column_index] = (
                    f"Available (UTC): {point['available_at']}<br>"
                    f"Measurement (UTC): {point['observed_at']}<br>"
                    f"Age: {point['observation_age_minutes']:.1f} min<br>"
                    f"Probes: {point['probe_count']}; buses: {point['bus_count']}<br>"
                    f"Target trip contributed: {point['target_bus_contributed']}<br>"
                    f"On remaining path: {point['in_remaining_path']}"
                )
        figure.add_trace(
            go.Heatmap(
                x=grid.columns,
                y=path["path_order"],
                z=grid.to_numpy(),
                text=hover,
                zmin=0,
                zmax=1,
                colorscale="YlOrRd",
                hoverongaps=False,
                colorbar={"title": "Congestion", "len": 0.27, "y": 0.53, "x": 1.01},
                hovertemplate="%{x} min before arrival<br>Index: %{z:.2f}<br>%{text}<extra></extra>",
                showscale=True,
            ),
            row=3,
            col=1,
        )
        figure.update_yaxes(
            tickmode="array",
            tickvals=path["path_order"],
            ticktext=path["from_stop_name"] + "<br> -> " + path["to_stop_name"],
            autorange="reversed",
            automargin=True,
            row=3,
            col=1,
        )
    else:
        messages = {
            3: f"<b>Upstream congestion unavailable for visit {visit_index}</b><br>"
            f"Trip: {corridor.iloc[0]['trip_id']}<br>{reason}<br>"
            "Use a matching historical GTFS snapshot for a full comparison.",
            4: "Unavailable: no verified upstream path (not zero congestion).",
            5: "Unavailable: upstream segment membership is unknown.",
        }
        for row, message in messages.items():
            figure.add_annotation(
                x=0.5,
                y=0.5,
                xref=f"x{row} domain",
                yref=f"y{row} domain",
                text=message,
                showarrow=False,
                font={"color": "firebrick"},
            )
            figure.update_yaxes(showticklabels=False, row=row, col=1)
    for scope in _COLORS if has_path else ():
        frame = example.loc[example["scope"].eq(scope)].sort_values("horizon_minutes")
        for row, column in ((4, "mean_congestion"), (5, "coverage_percent")):
            figure.add_trace(
                go.Scatter(
                    x=frame["horizon_minutes"],
                    y=frame[column],
                    mode="lines+markers",
                    name=_LABELS[scope],
                    legendgroup=scope,
                    showlegend=row == 4,
                    line={"color": _COLORS[scope]},
                    connectgaps=False,
                    customdata=frame[
                        [
                            "observed_segments",
                            "segment_count",
                            "mean_age_minutes",
                            "min_bus_count",
                            "position_age_minutes",
                        ]
                    ].to_numpy(),
                    hovertemplate=(
                        "%{x} min before arrival<br>Value: %{y:.2f}<br>"
                        "Observed segments: %{customdata[0]} / %{customdata[1]}<br>"
                        "Mean probe age: %{customdata[2]:.1f} min<br>"
                        "Minimum buses per observed segment: %{customdata[3]}<br>"
                        "As-of position age: %{customdata[4]:.1f} min<extra></extra>"
                    ),
                ),
                row=row,
                col=1,
            )
    figure.update_yaxes(title_text="Delay (min)", row=1, col=1)
    figure.update_yaxes(title_text="Error (min)", row=2, col=1)
    figure.update_yaxes(title_text="Index", range=[0, 1], row=4, col=1)
    figure.update_yaxes(title_text="Coverage (%)", range=[0, 105], row=5, col=1)
    figure.update_xaxes(autorange="reversed")
    figure.update_xaxes(
        title_text="Minutes before final reported arrival (proxy)", row=5, col=1
    )
    arrival = corridor.iloc[0]["reference_arrival_time"].tz_convert("America/Vancouver")
    status = "" if has_path else " | upstream congestion unavailable"
    figure.update_layout(
        title=f"Visit {visit_index} | {arrival}{status}<br>"
        f"<sup>Freshness <= {max_age_minutes} min | {_CAVEAT}</sup>",
        height=1350,
        hovermode="x unified",
        margin={"l": 340, "t": 125},
        legend={"orientation": "h", "y": -0.07},
    )
    return figure


def make_stop_congestion_cohort_figures(
    analysis: StopCongestionAnalysis,
    *,
    horizons_minutes: Sequence[int] = (60, 45, 30, 15, 5),
    max_age_minutes: int = 15,
    minimum_pairs: int = 10,
) -> dict[str, go.Figure]:
    """Build descriptive residual, coverage, and robustness figures.

    Parameters
    ----------
    analysis : StopCongestionAnalysis
        Prepared scoped comparisons. Never pools horizons as independent visits.
    horizons_minutes : sequence of int, optional
        Separate comparison horizons, default 60, 45, 30, 15, and 5 minutes.
    max_age_minutes : int, optional
        Primary freshness limit, default 15 minutes.
    minimum_pairs : int, optional
        Correlation display safeguard, default 10 paired visits.

    Returns
    -------
    dict of str to plotly.graph_objects.Figure
        Residual scatter with descriptive binned medians/IQR, signed/absolute
        error distributions, horizon associations, coverage/freshness/day
        diagnostics, and time-of-day/existing-prediction stratification.

    Raises
    ------
    ValueError
        If horizons, freshness, or minimum pair count are invalid.
    """
    horizons = list(horizons_minutes)
    associations = summarize_stop_congestion_signal(
        analysis.comparisons, horizons_minutes=horizons, minimum_pairs=minimum_pairs
    )
    primary = _primary(analysis, max_age_minutes)
    paired = (
        primary.loc[primary["horizon_minutes"].isin(horizons)]
        .dropna(subset=["mean_congestion", "remaining_delay_error_minutes"])
        .copy()
    )
    paired["local_evaluation"] = paired["cutoff_time"].dt.tz_convert(
        "America/Vancouver"
    )
    scatter = go.Figure()
    trace_horizons = []
    for horizon in horizons:
        for scope, color in _COLORS.items():
            frame = paired.loc[
                paired["horizon_minutes"].eq(horizon) & paired["scope"].eq(scope)
            ].copy()
            scatter.add_trace(
                go.Scatter(
                    x=frame["mean_congestion"],
                    y=frame["remaining_delay_error_minutes"],
                    mode="markers",
                    name=f"{_LABELS[scope]} (n={len(frame)})",
                    marker={"color": color, "size": 6, "opacity": 0.35},
                    visible=horizon == horizons[0],
                    customdata=frame[
                        [
                            "visit_index",
                            "coverage_percent",
                            "mean_age_minutes",
                            "estimated_delay_minutes",
                            "target_bus_contributed",
                        ]
                    ].to_numpy(),
                    hovertemplate=(
                        "Mean congestion: %{x:.2f}<br>Remaining error: %{y:.2f} min<br>"
                        "Visit: %{customdata[0]}<br>Coverage: %{customdata[1]:.0f}%<br>"
                        "Mean probe age: %{customdata[2]:.1f} min<br>"
                        "Published delay: %{customdata[3]:.2f} min<br>"
                        "Target contributed: %{customdata[4]}<extra></extra>"
                    ),
                )
            )
            frame["congestion_bin"] = pd.cut(
                frame["mean_congestion"], np.linspace(0, 1, 6), include_lowest=True
            )
            binned = frame.groupby("congestion_bin", observed=True).agg(
                congestion=("mean_congestion", "median"),
                median=("remaining_delay_error_minutes", "median"),
                q25=("remaining_delay_error_minutes", lambda v: v.quantile(0.25)),
                q75=("remaining_delay_error_minutes", lambda v: v.quantile(0.75)),
                count=("visit_index", "size"),
            )
            scatter.add_trace(
                go.Scatter(
                    x=binned["congestion"],
                    y=binned["median"],
                    mode="markers",
                    name=f"{scope}: binned median / IQR",
                    marker={"color": color, "size": 12, "symbol": "diamond"},
                    error_y={
                        "type": "data",
                        "symmetric": False,
                        "array": binned["q75"] - binned["median"],
                        "arrayminus": binned["median"] - binned["q25"],
                    },
                    customdata=binned[["count"]].to_numpy(),
                    hovertemplate="Median error: %{y:.2f} min<br>Visits: %{customdata[0]}<extra></extra>",
                    visible=horizon == horizons[0],
                )
            )
            trace_horizons.extend([horizon, horizon])
    scatter.add_hline(y=0, line_dash="dash")
    scatter.update_layout(
        title=f"Congestion vs. remaining forecast error | choose horizon<br><sup>{_CAVEAT}</sup>",
        xaxis_title="Mean congestion over observed upstream segments",
        yaxis_title="Final delay minus BC Transit prediction (min)",
        updatemenus=[
            {
                "buttons": [
                    {
                        "label": f"{h} min",
                        "method": "update",
                        "args": [
                            {
                                "visible": [
                                    candidate == h for candidate in trace_horizons
                                ]
                            }
                        ],
                    }
                    for h in horizons
                ],
                "y": 1.15,
            }
        ],
        height=600,
    )
    boxes = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=("Signed remaining error", "Absolute prediction error"),
    )
    paired["level"] = pd.cut(
        paired["mean_congestion"],
        [-np.inf, 0.2, 0.5, np.inf],
        labels=["Low", "Moderate", "High"],
        right=False,
    )
    for scope, color in _COLORS.items():
        frame = paired.loc[paired["scope"].eq(scope)]
        counts = frame.groupby(["horizon_minutes", "level"], observed=True)[
            "visit_index"
        ].transform("size")
        x = [
            frame["horizon_minutes"].astype(str).tolist(),
            frame["level"].astype("string").tolist(),
        ]
        for row in (1, 2):
            values = frame["remaining_delay_error_minutes"]
            boxes.add_trace(
                go.Box(
                    x=x,
                    y=values if row == 1 else values.abs(),
                    name=_LABELS[scope],
                    legendgroup=scope,
                    showlegend=row == 1,
                    marker_color=color,
                    boxpoints="outliers",
                    customdata=counts.to_numpy(),
                    hovertemplate="Error: %{y:.2f} min<br>Group n=%{customdata}<extra></extra>",
                ),
                row=row,
                col=1,
            )
    boxes.add_hline(y=0, line_dash="dash", row=1, col=1)
    boxes.update_yaxes(title_text="Minutes")
    boxes.update_xaxes(
        title_text="Minutes before arrival / index band<br>"
        "Low <0.2; Moderate 0.2 to <0.5; High >=0.5",
        row=2,
        col=1,
    )
    boxes.update_layout(
        title=f"Error distributions by horizon and congestion level<br><sup>{_CAVEAT}</sup>",
        boxmode="group",
        height=850,
    )
    heatmap = make_subplots(
        rows=1, cols=2, subplot_titles=tuple(_LABELS.values()), shared_yaxes=True
    )
    for column, scope in enumerate(_COLORS, start=1):
        subset = associations.loc[
            associations["scope"].eq(scope)
            & associations["max_age_minutes"].eq(max_age_minutes)
            & associations["subset"].eq("all_observed")
        ]
        correlations = subset.pivot(
            index="feature", columns="horizon_minutes", values="spearman"
        ).reindex(index=list(_FEATURES), columns=horizons)
        counts = subset.pivot(
            index="feature", columns="horizon_minutes", values="paired_visits"
        ).reindex(index=list(_FEATURES), columns=horizons)
        text = np.array(
            [
                [
                    (
                        f"{value:.2f}<br>n={count}"
                        if pd.notna(value)
                        else f"Unavailable<br>n={count}"
                    )
                    for value, count in zip(values, count_values)
                ]
                for values, count_values in zip(
                    correlations.to_numpy(), counts.to_numpy()
                )
            ]
        )
        heatmap.add_trace(
            go.Heatmap(
                x=[str(h) for h in horizons],
                y=list(_FEATURES.values()),
                z=correlations.to_numpy(),
                customdata=counts.to_numpy(),
                text=text,
                texttemplate="%{text}",
                colorscale="RdBu",
                reversescale=True,
                zmin=-1,
                zmax=1,
                showscale=column == 2,
                hoverongaps=False,
                hovertemplate="Horizon %{x} min<br>Spearman %{z:.2f}<br>Pairs %{customdata}<extra></extra>",
                colorbar={"title": "Spearman"},
            ),
            row=1,
            col=column,
        )
        # Plotly does not display text on NaN heatmap cells.
        for feature in _FEATURES:
            for horizon in horizons:
                if pd.isna(correlations.loc[feature, horizon]):
                    heatmap.add_annotation(
                        x=str(horizon),
                        y=_FEATURES[feature],
                        text=f"N/A<br>n={counts.loc[feature, horizon]}",
                        showarrow=False,
                        row=1,
                        col=column,
                    )
    heatmap.update_xaxes(title_text="Minutes before arrival")
    heatmap.update_layout(
        title=f"Horizon associations with remaining error (minimum n={minimum_pairs})"
        f"<br><sup>{_CAVEAT}; no significance or predictive-improvement claim</sup>",
        height=500,
        margin={"l": 160},
    )
    diagnostics = make_subplots(
        rows=3,
        cols=2,
        vertical_spacing=0.12,
        subplot_titles=(
            "Mean path coverage (unknown positions excluded)",
            "Visits with paired prediction/congestion",
            "Mean measurement age (observed segments)",
            "Minimum distinct buses per observed segment",
            "Mean-congestion correlation: same visits across horizons and freshness",
            f"Service-day consistency at {horizons[0]} min",
        ),
    )
    for scope, color in _COLORS.items():
        all_scoped = analysis.comparisons.loc[analysis.comparisons["scope"].eq(scope)]
        for limit, frame in all_scoped.groupby("max_age_minutes"):
            frame = frame.copy()
            frame["paired"] = (
                frame["mean_congestion"].notna()
                & frame["remaining_delay_error_minutes"].notna()
            )
            summary = frame.groupby("horizon_minutes").agg(
                coverage=("coverage_percent", "mean"),
                pairs=("paired", "sum"),
                age=("mean_age_minutes", "mean"),
                buses=("min_bus_count", "mean"),
            )
            label = f"{scope}, age <= {limit} min"
            for row, column, value in ((1, 1, "coverage"), (1, 2, "pairs")):
                diagnostics.add_trace(
                    go.Scatter(
                        x=summary.index,
                        y=summary[value],
                        mode="lines",
                        name=label,
                        legendgroup=label,
                        showlegend=column == 1,
                        line={
                            "color": color,
                            "dash": "solid" if limit == max_age_minutes else "dot",
                        },
                        opacity=1 if limit == max_age_minutes else 0.4,
                    ),
                    row=row,
                    col=column,
                )
            if limit == max_age_minutes:
                for column, value in ((1, "age"), (2, "buses")):
                    diagnostics.add_trace(
                        go.Scatter(
                            x=summary.index,
                            y=summary[value],
                            name=scope,
                            mode="lines",
                            line={"color": color},
                            showlegend=False,
                        ),
                        row=2,
                        col=column,
                    )
            common = associations.loc[
                associations["scope"].eq(scope)
                & associations["max_age_minutes"].eq(limit)
                & associations["feature"].eq("mean_congestion")
                & associations["subset"].eq("common_visits")
            ].sort_values("horizon_minutes")
            diagnostics.add_trace(
                go.Scatter(
                    x=common["horizon_minutes"],
                    y=common["spearman"],
                    customdata=common[["paired_visits"]].to_numpy(),
                    name=label,
                    mode="lines+markers",
                    showlegend=False,
                    line={
                        "color": color,
                        "dash": "solid" if limit == max_age_minutes else "dot",
                    },
                    hovertemplate="Spearman %{y:.2f}<br>Common visits %{customdata[0]}<extra></extra>",
                ),
                row=3,
                col=1,
            )
        daily = paired.loc[
            paired["scope"].eq(scope) & paired["horizon_minutes"].eq(horizons[0])
        ].copy()
        daily["day"] = daily["local_evaluation"].dt.date
        day_rows = []
        for day, frame in daily.groupby("day"):
            count, correlation = _rank_correlation(
                frame, "mean_congestion", minimum_pairs
            )
            day_rows.append((day, count, correlation))
        diagnostics.add_trace(
            go.Scatter(
                x=[row[0] for row in day_rows],
                y=[row[2] for row in day_rows],
                customdata=[[row[1]] for row in day_rows],
                mode="markers",
                name=scope,
                marker={"color": color},
                showlegend=False,
                hovertemplate="%{x}<br>Spearman %{y:.2f}<br>Pairs %{customdata[0]}<extra></extra>",
            ),
            row=3,
            col=2,
        )
    diagnostics.update_yaxes(title_text="Coverage (%)", range=[0, 105], row=1, col=1)
    diagnostics.update_yaxes(title_text="Visit count", row=1, col=2)
    diagnostics.update_yaxes(title_text="Minutes", row=2, col=1)
    diagnostics.update_yaxes(title_text="Buses", row=2, col=2)
    diagnostics.update_yaxes(title_text="Spearman", range=[-1, 1], row=3, col=1)
    diagnostics.update_yaxes(title_text="Spearman", range=[-1, 1], row=3, col=2)
    diagnostics.update_layout(
        title=f"Coverage, freshness sensitivity, and day-to-day stability<br><sup>{_CAVEAT}</sup>",
        height=1050,
        legend={"orientation": "h", "y": -0.1},
    )
    strata = make_subplots(
        rows=2,
        cols=1,
        vertical_spacing=0.18,
        subplot_titles=(
            f"Evaluation-time bands at {horizons[0]} min (Vancouver)",
            f"BC Transit predicted-delay bands at {horizons[0]} min",
        ),
    )
    for scope, color in _COLORS.items():
        scoped = paired.loc[
            paired["scope"].eq(scope) & paired["horizon_minutes"].eq(horizons[0])
        ].copy()
        scoped["hour_band"] = pd.cut(
            scoped["local_evaluation"].dt.hour,
            [0, 6, 10, 15, 19, 24],
            labels=["00-06", "06-10", "10-15", "15-19", "19-24"],
            right=False,
            include_lowest=True,
        )
        scoped["prediction_band"] = pd.cut(
            scoped["estimated_delay_minutes"],
            [-np.inf, 0, 5, 10, np.inf],
            labels=["Early (<0)", "0-5 min", "5-10 min", ">=10 min"],
            right=False,
        )
        for row, field in ((1, "hour_band"), (2, "prediction_band")):
            labels, counts, values = [], [], []
            for label, frame in scoped.groupby(field, observed=False):
                count, correlation = _rank_correlation(
                    frame, "mean_congestion", minimum_pairs
                )
                labels.append(str(label))
                counts.append(count)
                values.append(correlation)
            strata.add_trace(
                go.Scatter(
                    x=labels,
                    y=values,
                    mode="markers+text",
                    text=[f"n={n}" for n in counts],
                    textposition="top center",
                    name=_LABELS[scope],
                    showlegend=row == 1,
                    marker={"color": color, "size": 11},
                    customdata=counts,
                    hovertemplate="%{x}<br>Spearman %{y:.2f}<br>Pairs %{customdata}<extra></extra>",
                ),
                row=row,
                col=1,
            )
            for label, count, value in zip(labels, counts, values):
                if pd.isna(value):
                    strata.add_annotation(
                        x=label,
                        y=-0.9 if scope == "corridor" else -0.75,
                        text=f"{scope}: N/A (n={count})",
                        showarrow=False,
                        font={"color": color, "size": 10},
                        row=row,
                        col=1,
                    )
    strata.update_yaxes(title_text="Descriptive Spearman", range=[-1.05, 1.05])
    strata.update_layout(
        title=f"Does the association persist within comparable conditions?<br><sup>{_CAVEAT}</sup>",
        height=750,
    )
    return {
        "residual_scatter": scatter,
        "error_distributions": boxes,
        "horizon_associations": heatmap,
        "coverage_robustness": diagnostics,
        "conditional_associations": strata,
    }
