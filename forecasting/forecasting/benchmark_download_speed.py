"""Benchmark the notebook's database downloads, excluding static GTFS and caching.

Run from the repository root with
``conda run -n transit_env python -m forecasting.benchmark_download_speed``.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import mysql.connector
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .backtest_data import (
    _connection_parameters_from_env_file,
    _get_connection_parameters,
    download_backtest_data,
)

DEFAULT_HOURS = (1, 2, 4, 8, 12, 24, 48, 72, 96, 120, 144, 168)
_VEHICLE_QUERY = """
SELECT *
FROM TRANSIT_VEHICLE_TABLE
WHERE feed_timestamp >= %(window_start)s
  AND feed_timestamp < %(window_end)s
  AND trip_route_id = %(route_id)s
  AND trip_direction_id = %(direction_id)s
ORDER BY id DESC
"""
_TRIP_QUERY = _VEHICLE_QUERY.replace("TRANSIT_VEHICLE_TABLE", "TRANSIT_TRIP_TABLE")


def _database_time(parameters: Mapping[str, Any]) -> datetime:
    """Return NOW() from the database, using the notebook's server-time basis."""
    connection = mysql.connector.connect(**parameters)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT NOW()")
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()
    if not isinstance(row, tuple) or len(row) != 1 or not isinstance(row[0], datetime):
        raise RuntimeError("The database did not return a datetime for SELECT NOW().")
    return row[0]


def summarize_download_timings(timings: pd.DataFrame) -> pd.DataFrame:
    """Aggregate repeated downloads without treating empty windows as data.

    Parameters
    ----------
    timings : pandas.DataFrame
        Trial records returned by ``run_download_benchmark``.

    Returns
    -------
    pandas.DataFrame
        Median row counts and elapsed seconds, observed minimum/maximum
        seconds, repeat counts, and median milliseconds per returned row.
        Per-row time is missing for trials returning no rows.
    """
    samples = timings.assign(
        milliseconds_per_row=(
            1000 * timings["elapsed_seconds"] / timings["total_rows"].replace(0, np.nan)
        )
    )
    return (
        samples.groupby("lookback_hours", as_index=False)
        .agg(
            trials=("elapsed_seconds", "size"),
            vehicle_rows=("vehicle_rows", "median"),
            trip_rows=("trip_rows", "median"),
            total_rows=("total_rows", "median"),
            median_seconds=("elapsed_seconds", "median"),
            min_seconds=("elapsed_seconds", "min"),
            max_seconds=("elapsed_seconds", "max"),
            milliseconds_per_row=("milliseconds_per_row", "median"),
        )
        .sort_values("lookback_hours")
        .reset_index(drop=True)
    )


def make_download_scaling_plot(summary: pd.DataFrame) -> go.Figure:
    """Plot measured scaling against a proportional O(n) reference.

    Parameters
    ----------
    summary : pandas.DataFrame
        Output of ``summarize_download_timings`` with at least one nonempty
        window. Rows, not requested hours, define input size.

    Returns
    -------
    plotly.graph_objects.Figure
        Median elapsed time with observed min/max bars, an O(n) reference
        anchored at the smallest nonempty result, and time per returned row.
        A descriptive log-log exponent is shown for three or more distinct
        positive row counts; it is not an asymptotic-complexity test.

    Raises
    ------
    ValueError
        If no window returned rows with a positive elapsed time.
    """
    positive = summary.loc[
        (summary["total_rows"] > 0) & (summary["median_seconds"] > 0)
    ].sort_values("total_rows")
    if positive.empty:
        raise ValueError(
            "No nonempty timed windows; database scaling cannot be estimated."
        )

    figure = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Download time vs returned rows", "Time per returned row"),
    )
    labels = summary["lookback_hours"].map(lambda hours: f"{hours:g} h")
    figure.add_trace(
        go.Scatter(
            x=summary["total_rows"],
            y=summary["median_seconds"],
            mode="markers+text",
            text=labels,
            textposition="top center",
            name="Median download",
            error_y={
                "type": "data",
                "symmetric": False,
                "array": summary["max_seconds"] - summary["median_seconds"],
                "arrayminus": summary["median_seconds"] - summary["min_seconds"],
            },
        ),
        row=1,
        col=1,
    )
    anchor = positive.iloc[0]
    reference = positive["total_rows"] * (
        anchor["median_seconds"] / anchor["total_rows"]
    )
    figure.add_trace(
        go.Scatter(
            x=positive["total_rows"],
            y=reference,
            mode="lines",
            line={"dash": "dash"},
            name="O(n) reference (smallest nonempty window)",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=positive["total_rows"],
            y=positive["milliseconds_per_row"],
            mode="markers+lines",
            text=positive["lookback_hours"].map(lambda hours: f"{hours:g} h"),
            name="Median milliseconds/row",
        ),
        row=1,
        col=2,
    )
    if positive["total_rows"].nunique() >= 3:
        exponent, _ = np.polyfit(
            np.log(positive["total_rows"]), np.log(positive["median_seconds"]), 1
        )
        note = (
            f"Descriptive fit: time ~ rows^p, p = {exponent:.2f} "
            "(1 = proportional; >1 = superlinear over sampled sizes)."
        )
    else:
        note = "At least three distinct nonempty row counts are needed to estimate p."
    figure.add_annotation(
        text=note + "<br>Empirical measurements, not proof of asymptotic complexity.",
        xref="paper",
        yref="paper",
        x=0.5,
        y=-0.28,
        showarrow=False,
    )
    figure.update_xaxes(title_text="Vehicle + trip rows returned")
    figure.update_yaxes(title_text="Seconds", row=1, col=1)
    figure.update_yaxes(title_text="Milliseconds / row", row=1, col=2)
    figure.update_layout(
        title="Database download scaling",
        template="plotly_white",
        height=650,
        margin={"b": 180},
        legend={"orientation": "h", "y": -0.5},
    )
    return figure


def run_download_benchmark(
    output_dir: str | Path,
    *,
    connection_parameters: Mapping[str, Any] | None = None,
    hours: Sequence[int] = DEFAULT_HOURS,
    repeats: int = 3,
    route_id: str = "97-KEL",
    direction_id: int = 0,
) -> pd.DataFrame:
    """Measure the notebook's complete pair of realtime database downloads.

    Parameters
    ----------
    output_dir : str or pathlib.Path
        New directory for incremental ``timings.csv``, ``summary.csv``, and
        a self-contained ``scaling.html`` plot. Existing paths are rejected.
    connection_parameters : collections.abc.Mapping or None, optional
        Database settings, or None to resolve ``TRANSIT_DB_*`` variables.
        Credentials are never written to benchmark results.
    hours : sequence of int, optional
        Distinct lookback windows between 1 and 168 hours, inclusive.
    repeats : int, optional
        Number of measured sweeps. The first increases from smallest to
        largest; later sweeps are shuffled with fixed seed 0.
    route_id : str, optional
        Notebook route filter, default ``97-KEL``.
    direction_id : {0, 1}, optional
        Notebook direction filter, default 0.

    Returns
    -------
    pandas.DataFrame
        One record per trial, with fixed server-time bounds, filters, row
        counts for both tables, and combined elapsed seconds. Timing includes
        connecting, SQL execution/sorting, transfer, DataFrame construction,
        and closing the connection, but excludes GTFS, disk writes, and plots.

    Notes
    -----
    Every trial really downloads both tables through ``download_backtest_data``.
    DataFrames are released between trials. No caches are flushed and no
    database configuration or data is changed. The fixed upper bound prevents
    normal live ingestion from extending later trials, but late backfills or
    retention can still change row counts; both counts are recorded each time.
    Query exceptions propagate; already completed trials remain in the CSV.

    Raises
    ------
    ValueError
        If windows, filters, or repeats are invalid, or all windows are empty.
    FileExistsError
        If the output directory already exists.
    RuntimeError
        If required database settings or the server timestamp are unavailable.
    """
    windows = list(hours)
    if (
        not windows
        or any(type(hour) is not int or not 1 <= hour <= 168 for hour in windows)
        or len(set(windows)) != len(windows)
    ):
        raise ValueError("hours must contain distinct integers between 1 and 168.")
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer.")
    if not isinstance(route_id, str) or not route_id.strip():
        raise ValueError("route_id must be a nonempty string.")
    if type(direction_id) is not int or direction_id not in (0, 1):
        raise ValueError("direction_id must be 0 or 1.")

    parameters = _get_connection_parameters(connection_parameters)
    parameters.setdefault("connection_timeout", 30)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    window_end = _database_time(parameters)
    print(f"Fixed database-time endpoint: {window_end.isoformat()}", flush=True)
    records: list[dict[str, Any]] = []
    rng = random.Random(0)
    with (directory / "timings.csv").open("w", newline="") as stream:
        writer = None
        for repeat in range(1, repeats + 1):
            ordered_hours = sorted(windows)
            if repeat > 1:
                rng.shuffle(ordered_hours)
            for lookback_hours in ordered_hours:
                query_params = {
                    "window_start": window_end - timedelta(hours=lookback_hours),
                    "window_end": window_end,
                    "route_id": route_id,
                    "direction_id": direction_id,
                }
                print(
                    f"Repeat {repeat}/{repeats}, {lookback_hours} h: downloading...",
                    flush=True,
                )
                start = perf_counter()
                vehicle, trip = download_backtest_data(
                    connection_parameters=parameters,
                    vehicle_query=_VEHICLE_QUERY,
                    vehicle_query_params=query_params,
                    trip_query=_TRIP_QUERY,
                    trip_query_params=query_params,
                )
                elapsed = perf_counter() - start
                record = {
                    "trial_order": len(records) + 1,
                    "repeat": repeat,
                    "lookback_hours": lookback_hours,
                    **query_params,
                    "vehicle_rows": len(vehicle),
                    "trip_rows": len(trip),
                    "total_rows": len(vehicle) + len(trip),
                    "elapsed_seconds": elapsed,
                }
                del vehicle, trip
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(record))
                    writer.writeheader()
                writer.writerow(record)
                stream.flush()
                records.append(record)
                empty_note = " (EMPTY window)" if record["total_rows"] == 0 else ""
                print(
                    f"  {record['vehicle_rows']:,} vehicle + "
                    f"{record['trip_rows']:,} trip rows in {elapsed:.3f} s{empty_note}",
                    flush=True,
                )

    timings = pd.DataFrame.from_records(records)
    summary = summarize_download_timings(timings)
    summary.to_csv(directory / "summary.csv", index=False)
    figure = make_download_scaling_plot(summary)
    figure.update_layout(
        title=(
            f"Database download scaling - {route_id}, direction {direction_id}"
            f"<br><sup>Window ends {window_end.isoformat()} (database time); "
            "bars show observed min/max, not confidence intervals</sup>"
        )
    )
    figure.write_html(directory / "scaling.html", include_plotlyjs=True)
    print(summary.to_string(index=False), flush=True)
    print(f"Results: {directory.resolve()}", flush=True)
    return timings


def main(argv: Sequence[str] | None = None) -> None:
    """Parse command-line settings and write a database download scaling plot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", nargs="+", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--route-id", default="97-KEL")
    parser.add_argument("--direction-id", type=int, choices=(0, 1), default=0)
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env.road_segment_congestion")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data")
        / "download_benchmarks"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
    )
    args = parser.parse_args(argv)
    run_download_benchmark(
        args.output_dir,
        connection_parameters=_connection_parameters_from_env_file(args.env_file),
        hours=args.hours,
        repeats=args.repeats,
        route_id=args.route_id,
        direction_id=args.direction_id,
    )


if __name__ == "__main__":
    main()
