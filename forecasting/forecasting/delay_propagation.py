"""Measure whether segment delay anomalies stay local or spread along a route."""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager

import numpy as np
import pandas as pd

from .road_segment_congestion import _require_columns

DEFAULT_LAG_EDGES_MINUTES = (-60, -30, -15, 0, 15, 30, 60, 90)
_ALL_SEGMENTS = "all segments"
_TRAVERSAL_COLUMNS = (
    "trip_instance_key",
    "trip_start_timestamp",
    "segment_id",
    "exclusion_reason",
    "delta_delay_seconds",
    "from_arrival_time",
    "crossed_at",
)
_ORDERED_COLUMNS = (
    "segment_id",
    "position",
    "from_shape_dist",
    "to_shape_dist",
    "from_is_timepoint",
)
_ANOMALY_COLUMNS = (
    "trip_instance_key",
    "segment_id",
    "position",
    "service_date",
    "is_weekend",
    "local_time",
    "anomaly_seconds",
)


def order_route_segments(
    segments: pd.DataFrame,
    df_stop_times: pd.DataFrame,
    df_trips: pd.DataFrame,
    *,
    shape_id: str,
) -> pd.DataFrame:
    """Order one shape's segments along the route and attach stop positions.

    The reference pattern is the shape's trip with the most scheduled stops.
    Along-route positions come from GTFS ``shape_dist_traveled`` in the feed's
    own units (metres for BC Transit). The ``timepoint`` flag of each
    segment's upstream stop marks where schedule holding can absorb or add
    delay; an empty GTFS value means exact (1), and an absent column leaves
    the flag missing. Inputs are not modified.

    Parameters
    ----------
    segments : pandas.DataFrame
        Engine segments with ``segment_id``, ``shape_id``, stop IDs, and
        ``to_stop_sequence``, such as ``DelayCongestionResult.segments``.
    df_stop_times : pandas.DataFrame
        Static GTFS stop times with ``shape_dist_traveled``.
    df_trips : pandas.DataFrame
        Static GTFS trips with ``trip_id`` and ``shape_id``.
    shape_id : str
        Shape whose segments define the route axis.

    Returns
    -------
    pandas.DataFrame
        One row per segment in travel order, without geometry, adding
        ``position`` (0-based), ``from_shape_dist``, ``to_shape_dist``,
        ``length_shape_dist``, and ``from_is_timepoint``.

    Raises
    ------
    ValueError
        If the shape has no segments or stop distances are missing or not
        increasing along the route.
    """
    _require_columns(
        segments,
        ("segment_id", "shape_id", "from_stop_id", "to_stop_id", "to_stop_sequence"),
        "segments",
    )
    _require_columns(
        df_stop_times,
        ("trip_id", "stop_id", "stop_sequence", "shape_dist_traveled"),
        "df_stop_times",
    )
    _require_columns(df_trips, ("trip_id", "shape_id"), "df_trips")
    shape_id = str(shape_id)
    ordered = (
        segments.loc[segments["shape_id"].astype("string").eq(shape_id)]
        .drop(columns="geometry", errors="ignore")
        .sort_values("to_stop_sequence", kind="stable")
        .reset_index(drop=True)
    )
    if ordered.empty:
        raise ValueError(f"No segments use shape {shape_id!r}.")
    shape_trips = df_trips.loc[
        df_trips["shape_id"].astype("string").eq(shape_id), "trip_id"
    ].astype("string")
    stop_times = df_stop_times.loc[
        df_stop_times["trip_id"].astype("string").isin(shape_trips)
    ].copy()
    if stop_times.empty:
        raise ValueError(f"No scheduled stop times use shape {shape_id!r}.")
    stop_times["trip_id"] = stop_times["trip_id"].astype("string")
    reference = stop_times.groupby("trip_id").size().sort_index().idxmax()
    pattern = stop_times.loc[stop_times["trip_id"].eq(reference)].copy()
    pattern["stop_id"] = pattern["stop_id"].astype("string")
    pattern["shape_dist"] = pd.to_numeric(pattern["shape_dist_traveled"])
    if "timepoint" in pattern:
        timepoint = pd.to_numeric(pattern["timepoint"]).fillna(1).eq(1)
    else:
        timepoint = pd.Series(pd.NA, index=pattern.index, dtype="boolean")
    pattern["is_timepoint"] = timepoint.astype("boolean")
    pattern = pattern.drop_duplicates("stop_id").set_index("stop_id")
    for prefix in ("from", "to"):
        stop_ids = ordered[f"{prefix}_stop_id"].astype("string")
        ordered[f"{prefix}_shape_dist"] = stop_ids.map(pattern["shape_dist"]).astype(
            "float64"
        )
    ordered["from_is_timepoint"] = (
        ordered["from_stop_id"].astype("string").map(pattern["is_timepoint"])
    ).astype("boolean")
    distances = ordered[["from_shape_dist", "to_shape_dist"]]
    if (
        distances.isna().any().any()
        or not ordered["to_shape_dist"].gt(ordered["from_shape_dist"]).all()
        or not ordered["to_shape_dist"].is_monotonic_increasing
    ):
        raise ValueError(
            "Segment stops need increasing shape_dist_traveled values on the "
            "reference trip."
        )
    ordered["length_shape_dist"] = ordered["to_shape_dist"] - ordered["from_shape_dist"]
    ordered.insert(1, "position", np.arange(len(ordered), dtype="int64"))
    return ordered


def compute_delay_anomalies(
    traversals: pd.DataFrame,
    ordered_segments: pd.DataFrame,
    *,
    timezone: str = "America/Vancouver",
    min_baseline_count: int = 5,
) -> pd.DataFrame:
    """Remove each segment's usual time-of-day delay change from traversals.

    The baseline is the median ``delta_delay_seconds`` of the same segment,
    local hour of the downstream arrival, and weekday/weekend class, computed
    leave-one-service-day-out so that a traversal never contributes to its
    own baseline. Service dates are the trip-start dates, so trips running
    past midnight stay with their service day. Inputs are not modified.

    Parameters
    ----------
    traversals : pandas.DataFrame
        Engine traversals, such as ``DelayCongestionResult.traversals``.
        Only ``exclusion_reason == "included"`` rows are used.
    ordered_segments : pandas.DataFrame
        Output of :func:`order_route_segments`; other segments are dropped.
    timezone : str, optional
        Local service timezone, default ``"America/Vancouver"``. Naive trip
        starts are interpreted as local times.
    min_baseline_count : int, optional
        Minimum other-day traversals needed for a baseline, default 5.
        Rows below it keep a missing baseline and anomaly.

    Returns
    -------
    pandas.DataFrame
        Included traversals with segment position and distances, naive local
        ``from_local_time`` and ``local_time`` (downstream arrival),
        ``hour``, ``service_date``, ``is_weekend``, ``baseline_count``,
        ``baseline_delta_delay_seconds``, and ``anomaly_seconds``.

    Raises
    ------
    ValueError
        If controls are invalid or no included traversal uses the segments.
    """
    _require_columns(traversals, _TRAVERSAL_COLUMNS, "traversals")
    _require_columns(ordered_segments, _ORDERED_COLUMNS, "ordered_segments")
    _check_positive_int("min_baseline_count", min_baseline_count)
    frame = (
        traversals.loc[traversals["exclusion_reason"].eq("included")]
        .drop(columns="shape_id", errors="ignore")
        .merge(
            ordered_segments[list(_ORDERED_COLUMNS) + ["shape_id"]],
            on="segment_id",
            validate="many_to_one",
        )
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError("No included traversals use the ordered segments.")
    for source, target in (
        ("from_arrival_time", "from_local_time"),
        ("crossed_at", "local_time"),
    ):
        times = pd.to_datetime(frame[source], utc=True, errors="raise")
        frame[target] = times.dt.tz_convert(timezone).dt.tz_localize(None)
    starts = pd.to_datetime(frame["trip_start_timestamp"], errors="raise")
    if starts.dt.tz is not None:
        starts = starts.dt.tz_convert(timezone).dt.tz_localize(None)
    frame["service_date"] = starts.dt.normalize()
    frame["is_weekend"] = frame["service_date"].dt.dayofweek.ge(5)
    frame["hour"] = frame["local_time"].dt.hour
    values = frame["delta_delay_seconds"].to_numpy("float64")
    baseline = np.full(len(frame), np.nan)
    counts = np.zeros(len(frame), dtype="int64")
    for _, group in frame.groupby(["segment_id", "hour", "is_weekend"], sort=False):
        rows = group.index.to_numpy()
        days = group["service_date"].to_numpy()
        for day in np.unique(days):
            own = days == day
            others = values[rows[~own]]
            counts[rows[own]] = others.size
            if others.size >= min_baseline_count:
                baseline[rows[own]] = np.median(others)
    frame["baseline_count"] = counts
    frame["baseline_delta_delay_seconds"] = baseline
    frame["anomaly_seconds"] = frame["delta_delay_seconds"] - baseline
    return frame.sort_values(["local_time", "position"], kind="stable").reset_index(
        drop=True
    )


def select_delay_events(
    anomalies: pd.DataFrame, *, event_quantile: float = 0.9
) -> pd.DataFrame:
    """Select traversals with unusually large added delay on their segment.

    Parameters
    ----------
    anomalies : pandas.DataFrame
        Output of :func:`compute_delay_anomalies`.
    event_quantile : float, optional
        Segment-specific anomaly quantile defining an event, default 0.9.

    Returns
    -------
    pandas.DataFrame
        Event traversals with their segment's ``event_threshold_seconds``.

    Raises
    ------
    ValueError
        If ``event_quantile`` is not strictly between 0 and 1.
    """
    _require_columns(anomalies, _ANOMALY_COLUMNS, "anomalies")
    _check_quantile(event_quantile)
    usable = anomalies.dropna(subset=["anomaly_seconds"])
    thresholds = usable.groupby("segment_id")["anomaly_seconds"].transform(
        "quantile", event_quantile
    )
    return usable.loc[usable["anomaly_seconds"].ge(thresholds)].assign(
        event_threshold_seconds=thresholds
    )


def event_space_time_response(
    anomalies: pd.DataFrame,
    *,
    event_quantile: float = 0.9,
    offsets: Iterable[int] = range(-4, 5),
    lag_edges_minutes: Iterable[float] = DEFAULT_LAG_EDGES_MINUTES,
    source_groups: Mapping[int, str] | None = None,
    exclude_same_trip: bool = True,
    n_null_draws: int = 200,
    n_bootstrap: int = 500,
    seed: int = 0,
) -> pd.DataFrame:
    """Average other buses' anomalies around delay events, by space and time.

    For each event on source segment ``s`` (local arrival time ``t``), every
    traversal of segment ``s + offset`` whose arrival lies in a lag bin
    ``(a, b]`` minutes after ``t`` contributes its anomaly. Negative offsets
    are upstream and negative lags are before the event. Excluding the event
    bus isolates road-borne spread from the bus's own measurement coupling.

    The null repeats the calculation after randomly permuting service days
    within the weekday/weekend class, moving each day's events together to
    the same local clock times on their assigned day. This keeps the shared
    time-of-day pattern and the clustering of events while breaking same-day
    links. Confidence intervals resample event service days.

    Parameters
    ----------
    anomalies : pandas.DataFrame
        Output of :func:`compute_delay_anomalies`.
    event_quantile : float, optional
        Segment-specific anomaly quantile defining an event, default 0.9.
    offsets : iterable of int, optional
        Segment-position offsets from the source, default -4 through 4.
    lag_edges_minutes : iterable of float, optional
        Increasing lag-bin edges in minutes, default
        ``DEFAULT_LAG_EDGES_MINUTES``.
    source_groups : mapping of int to str, optional
        Source segment ID to group label. Events from unmapped segments are
        ignored. Default pools every segment into ``"all segments"``.
    exclude_same_trip : bool, optional
        Ignore the event bus's own traversals, default True.
    n_null_draws : int, optional
        Day-shift null draws, default 200.
    n_bootstrap : int, optional
        Service-day bootstrap resamples, default 500.
    seed : int, optional
        Random seed, default 0.

    Returns
    -------
    pandas.DataFrame
        One row per group, offset, and lag bin with ``event_count``,
        ``response_count``, ``mean_anomaly_seconds``, ``null_mean_seconds``,
        95% ``null_low_seconds``/``null_high_seconds``, ``excess_seconds``
        (observed minus null mean), its 95% day-bootstrap interval, and a
        two-sided permutation ``p_value``. Cells without responses are missing.

    Raises
    ------
    ValueError
        If controls are invalid.
    """
    _require_columns(anomalies, _ANOMALY_COLUMNS, "anomalies")
    offsets = np.asarray(sorted(set(offsets)), dtype="int64")
    edges = np.asarray(list(lag_edges_minutes), dtype="float64")
    if offsets.size == 0:
        raise ValueError("offsets must not be empty.")
    if edges.size < 2 or not np.all(np.diff(edges) > 0):
        raise ValueError("lag_edges_minutes needs at least two increasing edges.")
    _check_positive_int("n_null_draws", n_null_draws)
    _check_positive_int("n_bootstrap", n_bootstrap)
    rng = np.random.default_rng(seed)
    usable = anomalies.dropna(subset=["anomaly_seconds"])
    events = select_delay_events(usable, event_quantile=event_quantile)
    labels, events = _assign_groups(events, source_groups)
    days = np.sort(usable["service_date"].unique())
    events["_day"] = np.searchsorted(days, events["service_date"].to_numpy())
    responses = _response_lookup(usable)
    shape = (len(labels), offsets.size, edges.size - 1)

    observed_sums = np.zeros((days.size, *shape))
    observed_counts = np.zeros((days.size, *shape))
    _accumulate(
        events,
        responses,
        offsets,
        edges,
        exclude_same_trip,
        observed_sums,
        observed_counts,
    )
    null_means = np.full((n_null_draws, *shape), np.nan)
    for draw in range(n_null_draws):
        shifted = _shift_to_other_days(events, days, rng)
        sums = np.zeros((1, *shape))
        counts = np.zeros((1, *shape))
        _accumulate(shifted, responses, offsets, edges, False, sums, counts)
        null_means[draw] = _divide(sums[0], counts[0])
    weights = np.stack(
        [
            np.bincount(rng.integers(0, days.size, days.size), minlength=days.size)
            for _ in range(n_bootstrap)
        ]
    )
    boot_means = _divide(
        np.tensordot(weights, observed_sums, axes=1),
        np.tensordot(weights, observed_counts, axes=1),
    )
    counts = observed_counts.sum(axis=0)
    observed = _divide(observed_sums.sum(axis=0), counts)
    with _ignore_all_nan():
        null_mean = np.nanmean(null_means, axis=0)
        null_low, null_high = np.nanpercentile(null_means, [2.5, 97.5], axis=0)
        boot_low, boot_high = np.nanpercentile(boot_means, [2.5, 97.5], axis=0)
        extreme = np.abs(null_means - null_mean) >= np.abs(observed - null_mean)
        p_value = (1 + extreme.sum(axis=0)) / (1 + np.isfinite(null_means).sum(axis=0))
    event_counts = (
        events.groupby("_group").size().reindex(range(len(labels)), fill_value=0)
    )
    group_index, offset_index, lag_index = np.indices(shape)
    table = pd.DataFrame(
        {
            "group": np.asarray(labels, dtype=object)[group_index.ravel()],
            "offset": offsets[offset_index.ravel()],
            "lag_start_minutes": edges[lag_index.ravel()],
            "lag_end_minutes": edges[lag_index.ravel() + 1],
            "event_count": event_counts.to_numpy()[group_index.ravel()],
            "response_count": counts.ravel().astype("int64"),
            "mean_anomaly_seconds": observed.ravel(),
            "null_mean_seconds": null_mean.ravel(),
            "null_low_seconds": null_low.ravel(),
            "null_high_seconds": null_high.ravel(),
            "excess_seconds": (observed - null_mean).ravel(),
            "excess_ci_low_seconds": (boot_low - null_mean).ravel(),
            "excess_ci_high_seconds": (boot_high - null_mean).ravel(),
            "p_value": np.where(np.isfinite(observed), p_value, np.nan).ravel(),
        }
    )
    table.insert(
        4,
        "lag_bin",
        "("
        + table["lag_start_minutes"].map("{:g}".format)
        + ", "
        + table["lag_end_minutes"].map("{:g}".format)
        + "]",
    )
    return table


def same_bus_propagation(
    anomalies: pd.DataFrame,
    *,
    event_quantile: float = 0.9,
    max_offset: int = 4,
    source_groups: Mapping[int, str] | None = None,
    n_bootstrap: int = 500,
    seed: int = 0,
) -> pd.DataFrame:
    """Test whether a bus that gains unusual delay keeps gaining it later.

    For each trip and source segment ``s``, the bus's own anomaly on segment
    ``s + offset`` is compared between event traversals of ``s`` and all
    other traversals of ``s``. Adjacent offsets share a stop's arrival-delay
    proxy, so its estimate error couples them negatively; offsets of two or
    more avoid that coupling. Rows are split by whether the bus passes a
    scheduled timepoint between the two segments, where holding can absorb
    delay. Confidence intervals resample service days.

    Parameters
    ----------
    anomalies : pandas.DataFrame
        Output of :func:`compute_delay_anomalies`, with ``from_is_timepoint``.
    event_quantile : float, optional
        Segment-specific anomaly quantile defining an event, default 0.9.
    max_offset : int, optional
        Largest absolute offset, default 4. Negative offsets look upstream
        at what the same bus did before the event.
    source_groups : mapping of int to str, optional
        Source segment ID to group label; default pools all segments.
    n_bootstrap : int, optional
        Service-day bootstrap resamples, default 500.
    seed : int, optional
        Random seed, default 0.

    Returns
    -------
    pandas.DataFrame
        One row per group, offset, and ``crosses_timepoint`` with
        ``event_count``, ``pair_count``, ``event_mean_seconds``,
        ``other_mean_seconds``, ``excess_seconds`` and its 95% interval, and
        the ``spearman`` correlation of source and response anomalies.

    Raises
    ------
    ValueError
        If controls are invalid.
    """
    _require_columns(anomalies, (*_ANOMALY_COLUMNS, "from_is_timepoint"), "anomalies")
    _check_positive_int("max_offset", max_offset)
    _check_positive_int("n_bootstrap", n_bootstrap)
    usable = anomalies.dropna(subset=["anomaly_seconds"])
    events = select_delay_events(usable, event_quantile=event_quantile)
    source = usable[
        [
            "trip_instance_key",
            "segment_id",
            "position",
            "service_date",
            "anomaly_seconds",
        ]
    ].copy()
    source["is_event"] = source.index.isin(events.index)
    labels, source = _assign_groups(source, source_groups)
    timepoints = (
        usable.drop_duplicates("position")
        .set_index("position")["from_is_timepoint"]
        .astype("boolean")
        .fillna(False)
        .reindex(range(int(usable["position"].max()) + 1), fill_value=False)
    )
    passed = np.concatenate([[0], np.cumsum(timepoints.to_numpy(dtype="int64"))])
    target = usable[["trip_instance_key", "position", "anomaly_seconds"]].rename(
        columns={"position": "target_position", "anomaly_seconds": "response_seconds"}
    )
    rng = np.random.default_rng(seed)
    days = np.sort(usable["service_date"].unique())
    weights = np.stack(
        [
            np.bincount(rng.integers(0, days.size, days.size), minlength=days.size)
            for _ in range(n_bootstrap)
        ]
    )
    rows = []
    for offset in [k for k in range(-max_offset, max_offset + 1) if k]:
        pairs = source.assign(target_position=source["position"] + offset).merge(
            target, on=["trip_instance_key", "target_position"], validate="one_to_one"
        )
        # Stops strictly between the two segments are the upstream stops of
        # positions low + 1 through high.
        low = np.minimum(pairs["position"], pairs["target_position"]).to_numpy()
        high = np.maximum(pairs["position"], pairs["target_position"]).to_numpy()
        pairs["crosses_timepoint"] = passed[high + 1] - passed[low + 1] > 0
        pairs["_day"] = np.searchsorted(days, pairs["service_date"].to_numpy())
        for (group, crosses), pair_group in pairs.groupby(
            ["_group", "crosses_timepoint"], sort=True
        ):
            is_event = pair_group["is_event"]
            day_stats = np.zeros((4, days.size))
            for row, mask in enumerate((is_event, ~is_event)):
                values = pair_group.loc[mask]
                day_stats[2 * row] = np.bincount(
                    values["_day"], values["response_seconds"], minlength=days.size
                )
                day_stats[2 * row + 1] = np.bincount(
                    values["_day"], minlength=days.size
                )
            totals = day_stats.sum(axis=1)
            boot = weights @ day_stats.T
            boot_excess = _divide(boot[:, 0], boot[:, 1]) - _divide(
                boot[:, 2], boot[:, 3]
            )
            event_mean = _divide(totals[0], totals[1])
            other_mean = _divide(totals[2], totals[3])
            with _ignore_all_nan():
                ci_low, ci_high = np.nanpercentile(boot_excess, [2.5, 97.5])
            rows.append(
                {
                    "group": labels[group],
                    "offset": offset,
                    "crosses_timepoint": bool(crosses),
                    "event_count": int(totals[1]),
                    "pair_count": len(pair_group),
                    "event_mean_seconds": event_mean,
                    "other_mean_seconds": other_mean,
                    "excess_seconds": event_mean - other_mean,
                    "excess_ci_low_seconds": ci_low,
                    "excess_ci_high_seconds": ci_high,
                    "spearman": pair_group["anomaly_seconds"].corr(
                        pair_group["response_seconds"], method="spearman"
                    ),
                }
            )
    return pd.DataFrame(rows)


def summarize_propagation(
    response: pd.DataFrame,
    *,
    window_minutes: float = 30,
    neighbour_offsets: Iterable[int] = (1, 2),
    alpha: float = 0.05,
    min_excess_seconds: float = 10.0,
    min_events: int = 20,
) -> pd.DataFrame:
    """Label each source group as sticking, spreading, or inconclusive.

    Uses the ``(0, window]`` lag bin of :func:`event_space_time_response`;
    compute it with edges such as ``(-window, 0, window)``. A neighbour shows
    spread when its excess is at least ``min_excess_seconds`` with a
    permutation p-value below ``alpha`` divided by the number of neighbour
    offsets tested on that side (Bonferroni). The same effect-size floor
    applies to the source segment itself, so large samples cannot turn
    negligible differences into labels. The ``(-window, 0]`` bin, when present, reports whether
    neighbours were already elevated before the event, which indicates a
    shared cause rather than propagation.

    Parameters
    ----------
    response : pandas.DataFrame
        Output of :func:`event_space_time_response`.
    window_minutes : float, optional
        Length of the after-event window, default 30.
    neighbour_offsets : iterable of int, optional
        Absolute offsets treated as neighbours on each side, default (1, 2).
    alpha : float, optional
        Family-wise significance level per side, default 0.05.
    min_excess_seconds : float, optional
        Smallest excess treated as a meaningful effect, default 10 seconds.
    min_events : int, optional
        Fewer events than this give ``"insufficient evidence"``, default 20.

    Returns
    -------
    pandas.DataFrame
        One row per group with event count, self excess and p-value,
        strongest upstream/downstream excess after the event, neighbour
        excess before the event, ``spread_ratio`` (strongest neighbour
        excess over self excess), and ``label``: ``"sticks"``,
        ``"spills upstream"``, ``"spreads downstream"``,
        ``"spreads both ways"``, ``"no lasting signal"``, or
        ``"insufficient evidence"``.

    Raises
    ------
    ValueError
        If the after-event bin is absent or controls are invalid.
    """
    _require_columns(
        response,
        (
            "group",
            "offset",
            "lag_start_minutes",
            "lag_end_minutes",
            "event_count",
            "excess_seconds",
            "p_value",
        ),
        "response",
    )
    neighbours = sorted({abs(int(offset)) for offset in neighbour_offsets} - {0})
    if not neighbours:
        raise ValueError("neighbour_offsets needs at least one nonzero offset.")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1.")
    after = response.loc[
        response["lag_start_minutes"].eq(0)
        & response["lag_end_minutes"].eq(window_minutes)
    ]
    if after.empty:
        raise ValueError(f"response has no (0, {window_minutes:g}] lag bin.")
    before = response.loc[
        response["lag_start_minutes"].eq(-window_minutes)
        & response["lag_end_minutes"].eq(0)
    ]
    threshold = alpha / len(neighbours)
    rows = []
    for group, cells in after.groupby("group", sort=False):
        cells = cells.set_index("offset")
        self_cell = cells.loc[0] if 0 in cells.index else None
        self_excess = np.nan if self_cell is None else self_cell["excess_seconds"]
        self_p = np.nan if self_cell is None else self_cell["p_value"]
        sides = {}
        for side, sign in (("upstream", -1), ("downstream", 1)):
            side_cells = cells.reindex([sign * k for k in neighbours]).dropna(
                subset=["excess_seconds"]
            )
            sides[side] = (
                side_cells["excess_seconds"].max() if len(side_cells) else np.nan,
                bool(
                    (
                        side_cells["excess_seconds"].ge(min_excess_seconds)
                        & side_cells["p_value"].lt(threshold)
                    ).any()
                ),
            )
        prior = before.loc[
            before["group"].eq(group) & before["offset"].abs().isin(neighbours),
            "excess_seconds",
        ]
        neighbour_excess = [value for value, _ in sides.values() if not pd.isna(value)]
        strongest = max(neighbour_excess) if neighbour_excess else np.nan
        event_count = int(cells["event_count"].iloc[0])
        sticks = bool(self_excess >= min_excess_seconds and self_p < alpha)
        if event_count < min_events:
            label = "insufficient evidence"
        elif sides["upstream"][1] and sides["downstream"][1]:
            label = "spreads both ways"
        elif sides["upstream"][1]:
            label = "spills upstream"
        elif sides["downstream"][1]:
            label = "spreads downstream"
        elif sticks:
            label = "sticks"
        else:
            label = "no lasting signal"
        rows.append(
            {
                "group": group,
                "event_count": event_count,
                "self_excess_seconds": self_excess,
                "self_p_value": self_p,
                "upstream_excess_seconds": sides["upstream"][0],
                "downstream_excess_seconds": sides["downstream"][0],
                "neighbour_excess_before_seconds": (
                    prior.mean() if len(prior) else np.nan
                ),
                "spread_ratio": strongest / self_excess if self_excess > 0 else np.nan,
                "label": label,
            }
        )
    return pd.DataFrame(rows)


def _check_positive_int(name: str, value: int) -> None:
    """Raise when a count control is not a positive integer.

    Parameters
    ----------
    name : str
        Control name used in the error message.
    value : int
        Value to validate.
    """
    if (
        isinstance(value, (bool, np.bool_))
        or not isinstance(value, (int, np.integer))
        or value < 1
    ):
        raise ValueError(f"{name} must be a positive integer.")


def _check_quantile(event_quantile: float) -> None:
    """Raise when an event quantile is outside the open unit interval.

    Parameters
    ----------
    event_quantile : float
        Quantile to validate.
    """
    if isinstance(event_quantile, (bool, np.bool_)) or not 0 < event_quantile < 1:
        raise ValueError("event_quantile must be strictly between 0 and 1.")


def _assign_groups(
    frame: pd.DataFrame, source_groups: Mapping[int, str] | None
) -> tuple[list[str], pd.DataFrame]:
    """Attach integer group codes to source rows, dropping unmapped segments.

    Parameters
    ----------
    frame : pandas.DataFrame
        Rows with ``segment_id``.
    source_groups : mapping of int to str or None
        Segment ID to group label; None pools every row.

    Returns
    -------
    tuple of (list of str, pandas.DataFrame)
        Group labels in first-seen mapping order and a copy with ``_group``.
    """
    frame = frame.copy()
    if source_groups is None:
        frame["_group"] = 0
        return [_ALL_SEGMENTS], frame
    labels = list(dict.fromkeys(source_groups.values()))
    codes = {segment: labels.index(label) for segment, label in source_groups.items()}
    frame["_group"] = frame["segment_id"].map(codes)
    frame = frame.loc[frame["_group"].notna()].copy()
    frame["_group"] = frame["_group"].astype("int64")
    return labels, frame


def _response_lookup(
    usable: pd.DataFrame,
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Index anomalies by segment position as time-sorted arrays.

    Parameters
    ----------
    usable : pandas.DataFrame
        Anomalies with nonmissing ``anomaly_seconds``.

    Returns
    -------
    dict
        Position to (local arrival times, anomalies, trip keys).
    """
    lookup = {}
    for position, group in usable.groupby("position"):
        group = group.sort_values("local_time", kind="stable")
        lookup[int(position)] = (
            group["local_time"].to_numpy("datetime64[ns]"),
            group["anomaly_seconds"].to_numpy("float64"),
            group["trip_instance_key"].astype("string").to_numpy(object),
        )
    return lookup


def _accumulate(
    events: pd.DataFrame,
    responses: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]],
    offsets: np.ndarray,
    edges: np.ndarray,
    exclude_same_trip: bool,
    sums: np.ndarray,
    counts: np.ndarray,
) -> None:
    """Add event-to-response anomalies into day/group/offset/lag arrays.

    Parameters
    ----------
    events : pandas.DataFrame
        Events with ``position``, ``local_time``, ``trip_instance_key``,
        ``_group``, and ``_day`` (ignored when ``sums`` has one day).
    responses : dict
        Output of :func:`_response_lookup`.
    offsets, edges : numpy.ndarray
        Segment offsets and lag-bin edges in minutes.
    exclude_same_trip : bool
        Whether to skip the event bus's own traversals.
    sums, counts : numpy.ndarray
        Arrays shaped (day, group, offset, lag bin), updated in place.
    """
    window = (
        np.timedelta64(int(edges[0] * 60e9), "ns"),
        np.timedelta64(int(edges[-1] * 60e9), "ns"),
    )
    single_day = sums.shape[0] == 1
    for position, source in events.groupby("position", sort=False):
        times = source["local_time"].to_numpy("datetime64[ns]")
        trips = source["trip_instance_key"].astype("string").to_numpy(object)
        groups = source["_group"].to_numpy()
        days = (
            np.zeros(len(source), "int64") if single_day else source["_day"].to_numpy()
        )
        for offset_index, offset in enumerate(offsets):
            target = responses.get(int(position) + int(offset))
            if target is None:
                continue
            target_times, target_values, target_trips = target
            start = np.searchsorted(target_times, times + window[0], side="right")
            stop = np.searchsorted(target_times, times + window[1], side="right")
            sizes = stop - start
            event_index = np.repeat(np.arange(len(source)), sizes)
            target_index = (
                np.arange(sizes.sum())
                - np.repeat(np.cumsum(sizes) - sizes, sizes)
                + np.repeat(start, sizes)
            )
            lag = (target_times[target_index] - times[event_index]) / np.timedelta64(
                1, "m"
            )
            lag_index = np.searchsorted(edges, lag, side="left") - 1
            keep = (lag_index >= 0) & (lag_index < edges.size - 1)
            if exclude_same_trip:
                keep &= target_trips[target_index] != trips[event_index]
            index = (
                days[event_index][keep],
                groups[event_index][keep],
                np.full(keep.sum(), offset_index),
                lag_index[keep],
            )
            np.add.at(sums, index, target_values[target_index][keep])
            np.add.at(counts, index, 1)


def _shift_to_other_days(
    events: pd.DataFrame, days: np.ndarray, rng: np.random.Generator
) -> pd.DataFrame:
    """Permute service days within each class, moving each day's events together.

    Moving whole days keeps clustered events (one episode seen by several
    buses) sharing their responses, as they do in the observed data. The
    observed arrangement is one possible permutation, so observed and null
    means are exchangeable when days are unrelated.

    Parameters
    ----------
    events : pandas.DataFrame
        Events with ``service_date``, ``is_weekend``, and ``local_time``.
    days : numpy.ndarray
        Sorted observed service dates.
    rng : numpy.random.Generator
        Random generator.

    Returns
    -------
    pandas.DataFrame
        Events at the same local clock times on permuted days. Events whose
        class has a single day are dropped.
    """
    shifted = []
    weekend = pd.DatetimeIndex(days).dayofweek >= 5
    for is_weekend, group in events.groupby("is_weekend", sort=False):
        candidates = days[weekend == bool(is_weekend)]
        if candidates.size < 2:
            continue
        targets = rng.permutation(candidates)[
            np.searchsorted(candidates, group["service_date"].to_numpy())
        ]
        moved = group.copy()
        moved["local_time"] = moved["local_time"] + (
            targets - moved["service_date"].to_numpy()
        )
        moved["trip_instance_key"] = pd.NA
        shifted.append(moved)
    if not shifted:
        return events.iloc[0:0]
    return pd.concat(shifted)


def _divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Divide elementwise, returning NaN where the denominator is zero.

    Parameters
    ----------
    numerator, denominator : numpy.ndarray
        Arrays of the same shape.

    Returns
    -------
    numpy.ndarray
        Quotient with NaN for empty cells.
    """
    numerator = np.asarray(numerator, dtype="float64")
    denominator = np.asarray(denominator, dtype="float64")
    return np.divide(
        numerator,
        denominator,
        out=np.full(np.broadcast(numerator, denominator).shape, np.nan),
        where=denominator > 0,
    )


@contextmanager
def _ignore_all_nan() -> Iterator[None]:
    """Silence runtime warnings from summaries of cells without responses.

    Yields
    ------
    None
        Control while all-NaN ``RuntimeWarning`` messages are ignored.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yield
