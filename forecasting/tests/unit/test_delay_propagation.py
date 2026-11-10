import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    compute_delay_anomalies,
    event_space_time_response,
    make_propagation_heatmap,
    make_time_space_diagram,
    order_route_segments,
    same_bus_propagation,
    select_delay_events,
    summarize_propagation,
)
from forecasting.delay_propagation import _shift_to_other_days

_FAST = {"n_null_draws": 100, "n_bootstrap": 100, "seed": 1}
_BUSES = 36


def _bus(day: int) -> int:
    """Return a per-day event bus far enough from other days' clock times."""
    return 1 + 5 * day


def _anomaly_frame(days: int = 7, seed: int = 0) -> pd.DataFrame:
    """Build noisy anomalies for buses every 10 min over five segments."""
    rng = np.random.default_rng(seed)
    rows = []
    for date in pd.bdate_range("2026-09-07", periods=days):
        for bus in range(_BUSES):
            start = date + pd.Timedelta(hours=8, minutes=10 * bus)
            for position in range(5):
                rows.append(
                    {
                        "trip_instance_key": f"{date:%Y%m%d}-{bus}",
                        "segment_id": 10 + position,
                        "position": position,
                        "service_date": date,
                        "is_weekend": False,
                        "from_local_time": start + pd.Timedelta(minutes=3 * position),
                        "local_time": start + pd.Timedelta(minutes=3 * position + 3),
                        "from_shape_dist": 1000.0 * position,
                        "to_shape_dist": 1000.0 * (position + 1),
                        "from_is_timepoint": position == 4,
                        "delta_delay_seconds": 0.0,
                        "baseline_delta_delay_seconds": 0.0,
                        "anomaly_seconds": rng.normal(0, 5),
                    }
                )
    return pd.DataFrame(rows)


def _set(frame: pd.DataFrame, day: int, buses, position: int, value: float) -> None:
    dates = sorted(frame["service_date"].unique())
    keys = [f"{pd.Timestamp(dates[day]):%Y%m%d}-{bus}" for bus in buses]
    mask = frame["trip_instance_key"].isin(keys) & frame["position"].eq(position)
    frame.loc[mask, "anomaly_seconds"] = value


def _cell(response: pd.DataFrame, offset: int, lag_start: float) -> pd.Series:
    return response.loc[
        response["offset"].eq(offset) & response["lag_start_minutes"].eq(lag_start)
    ].iloc[0]


def _summary_response(frame: pd.DataFrame, **kwargs) -> pd.DataFrame:
    return event_space_time_response(
        frame,
        offsets=range(-2, 3),
        lag_edges_minutes=(-30, 0, 30),
        source_groups={12: "middle"},
        **_FAST,
        **kwargs,
    )


def test_delay_that_lingers_on_its_segment_is_labelled_sticky() -> None:
    frame = _anomaly_frame()
    for day in range(7):
        _set(frame, day, [_bus(day)], 2, 300)
        _set(frame, day, [_bus(day) + 1, _bus(day) + 2], 2, 120)
    response = _summary_response(frame)

    # Events include the follow-on 120 s readings, diluting the mean response.
    assert _cell(response, 0, 0)["excess_seconds"] > 15
    assert abs(_cell(response, -1, 0)["excess_seconds"]) < 10
    assert abs(_cell(response, 1, 0)["excess_seconds"]) < 10
    summary = summarize_propagation(response, min_events=5)
    assert summary["label"].tolist() == ["sticks"]


def test_queue_growing_upstream_is_labelled_upstream_spillback() -> None:
    frame = _anomaly_frame()
    for day in range(7):
        _set(frame, day, [_bus(day)], 2, 300)
        _set(frame, day, [_bus(day) + k for k in (1, 2, 3)], 1, 150)
    response = _summary_response(frame, event_quantile=0.95)

    assert _cell(response, -1, 0)["excess_seconds"] > 50
    assert abs(_cell(response, 1, 0)["excess_seconds"]) < 10
    summary = summarize_propagation(response, min_events=5)
    assert summary["label"].tolist() == ["spills upstream"]
    assert summary["spread_ratio"].isna().all() or summary["spread_ratio"].iloc[0] > 1


def test_shared_time_of_day_pattern_is_absorbed_by_the_other_day_null() -> None:
    frame = _anomaly_frame()
    rush = frame["local_time"].dt.time.between(
        pd.Timestamp("09:00").time(), pd.Timestamp("09:40").time()
    )
    frame.loc[rush, "anomaly_seconds"] += 80
    response = _summary_response(frame)

    for offset in (-1, 0, 1):
        assert abs(_cell(response, offset, 0)["excess_seconds"]) < 15
    summary = summarize_propagation(response, min_events=5)
    assert summary["label"].tolist() == ["no lasting signal"]


def test_other_bus_response_excludes_the_event_bus_by_default() -> None:
    frame = _anomaly_frame()
    for day in range(7):
        _set(frame, day, [_bus(day)], 2, 300)
        _set(frame, day, [_bus(day)], 3, 200)
    kwargs = dict(
        offsets=[1],
        lag_edges_minutes=(0, 5),
        event_quantile=0.97,
        source_groups={12: "middle"},
        **_FAST,
    )

    excluded = event_space_time_response(frame, **kwargs)
    included = event_space_time_response(frame, exclude_same_trip=False, **kwargs)

    assert excluded["response_count"].iloc[0] == 0
    assert included["mean_anomaly_seconds"].iloc[0] > 100


def test_same_bus_propagation_splits_by_timepoint_crossing() -> None:
    frame = _anomaly_frame()
    for day in range(7):
        _set(frame, day, [_bus(day)], 2, 300)
        _set(frame, day, [_bus(day)], 3, 100)
    result = same_bus_propagation(
        frame,
        event_quantile=0.97,
        max_offset=2,
        source_groups={12: "middle"},
        n_bootstrap=100,
        seed=1,
    ).set_index(["offset", "crosses_timepoint"])

    assert result.loc[(1, False), "excess_seconds"] > 50
    assert result.loc[(1, False), "excess_ci_low_seconds"] > 0
    assert abs(result.loc[(-1, False), "excess_seconds"]) < 10
    # Segment position 4 starts at a timepoint, so 2 -> 4 passes one.
    assert (2, True) in result.index and (2, False) not in result.index


def test_null_moves_each_days_events_together_to_permuted_days() -> None:
    frame = _anomaly_frame()
    events = select_delay_events(frame, event_quantile=0.9)
    rng = np.random.default_rng(3)
    days = np.sort(frame["service_date"].unique())

    for _ in range(5):
        shifted = _shift_to_other_days(events, days, rng)
        moved = shifted["local_time"] - events["local_time"]
        assert moved.dt.total_seconds().mod(86_400).eq(0).all()
        targets = events["service_date"] + moved
        mapping = targets.groupby(events["service_date"]).nunique()
        assert mapping.eq(1).all()
        assert targets.groupby(events["service_date"]).first().is_unique
        assert shifted["trip_instance_key"].isna().all()


def test_events_use_segment_specific_quantiles() -> None:
    frame = _anomaly_frame()
    frame.loc[frame["position"].eq(0), "anomaly_seconds"] += 1000
    events = select_delay_events(frame, event_quantile=0.9)

    counts = events.groupby("position").size()
    assert len(counts) == 5 and counts.eq(counts.iloc[0]).all()
    assert counts.iloc[0] == np.ceil(0.1 * 7 * _BUSES)
    with pytest.raises(ValueError, match="event_quantile"):
        select_delay_events(frame, event_quantile=1.0)


def _traversals() -> pd.DataFrame:
    rows = []
    for day, delta in zip(["2026-09-07", "2026-09-08", "2026-09-09"], [10, 20, 60]):
        start = pd.Timestamp(f"{day} 08:00")
        rows.append(
            {
                "trip_instance_key": f"t|{start}",
                "trip_start_timestamp": start,
                "segment_id": 1,
                "exclusion_reason": "included",
                "delta_delay_seconds": float(delta),
                "from_arrival_time": start.tz_localize("America/Vancouver").tz_convert(
                    "UTC"
                ),
                "crossed_at": (start + pd.Timedelta(minutes=5))
                .tz_localize("America/Vancouver")
                .tz_convert("UTC"),
                "shape_id": "s",
            }
        )
    excluded = dict(rows[0], exclusion_reason="from_missing_stop_visit")
    return pd.DataFrame([*rows, excluded])


def _ordered() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "segment_id": [1],
            "shape_id": ["s"],
            "position": [0],
            "from_shape_dist": [0.0],
            "to_shape_dist": [500.0],
            "from_is_timepoint": [True],
        }
    )


def test_baseline_leaves_out_each_traversals_own_service_day() -> None:
    traversals = _traversals()
    original = traversals.copy()
    anomalies = compute_delay_anomalies(traversals, _ordered(), min_baseline_count=2)

    pdt.assert_frame_equal(traversals, original)
    assert len(anomalies) == 3
    assert anomalies["baseline_delta_delay_seconds"].tolist() == [40.0, 35.0, 15.0]
    assert anomalies["anomaly_seconds"].tolist() == [-30.0, -15.0, 45.0]
    assert anomalies["hour"].eq(8).all() and not anomalies["is_weekend"].any()

    sparse = compute_delay_anomalies(traversals, _ordered(), min_baseline_count=3)
    assert sparse["anomaly_seconds"].isna().all()
    assert sparse["baseline_count"].eq(2).all()


def test_order_route_segments_uses_shape_distance_and_timepoints() -> None:
    segments = pd.DataFrame(
        {
            "segment_id": [7, 5, 9],
            "shape_id": ["s", "s", "other"],
            "from_stop_id": ["b", "a", "a"],
            "to_stop_id": ["c", "b", "c"],
            "to_stop_sequence": [3, 2, 2],
            "geometry": [None] * 3,
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["long"] * 3 + ["short"] * 2,
            "stop_id": ["a", "b", "c", "a", "c"],
            "stop_sequence": [1, 2, 3, 1, 2],
            "shape_dist_traveled": [0, 400, 1000, 0, 1000],
            "timepoint": [1, 0, None, 1, 1],
        }
    )
    trips = pd.DataFrame({"trip_id": ["long", "short"], "shape_id": ["s", "s"]})

    ordered = order_route_segments(segments, stop_times, trips, shape_id="s")

    assert ordered["segment_id"].tolist() == [5, 7]
    assert ordered["position"].tolist() == [0, 1]
    assert ordered["length_shape_dist"].tolist() == [400.0, 600.0]
    assert ordered["from_is_timepoint"].tolist() == [True, False]
    assert "geometry" not in ordered

    reversed_stops = stop_times.assign(shape_dist_traveled=[0, 1200, 1000, 0, 1000])
    with pytest.raises(ValueError, match="increasing"):
        order_route_segments(segments, reversed_stops, trips, shape_id="s")
    with pytest.raises(ValueError, match="No segments"):
        order_route_segments(segments, stop_times, trips, shape_id="missing")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"offsets": []}, "offsets"),
        ({"lag_edges_minutes": (0,)}, "lag_edges"),
        ({"lag_edges_minutes": (10, 0)}, "lag_edges"),
        ({"n_null_draws": 0}, "n_null_draws"),
        ({"n_bootstrap": True}, "n_bootstrap"),
    ],
)
def test_response_rejects_invalid_controls(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        event_space_time_response(_anomaly_frame(days=2), **kwargs)


def test_summary_requires_the_after_event_bin() -> None:
    response = event_space_time_response(
        _anomaly_frame(days=3), lag_edges_minutes=(0, 15), **_FAST
    )
    with pytest.raises(ValueError, match="lag bin"):
        summarize_propagation(response)


def test_figures_show_excess_cells_and_one_day_of_trips() -> None:
    frame = _anomaly_frame()
    for day in range(7):
        _set(frame, day, [_bus(day)], 2, 300)
        _set(frame, day, [_bus(day) + 1, _bus(day) + 2], 2, 120)
    response = _summary_response(frame)
    heatmap = make_propagation_heatmap(response, title="Propagation")

    assert np.asarray(heatmap.data[0].z).shape == (2, 5)
    assert any(label.endswith("*") for label in np.ravel(heatmap.data[0].text))
    with pytest.raises(ValueError, match="not in the response"):
        make_propagation_heatmap(response, group="missing", title="x")

    ordered = frame.drop_duplicates("position").assign(
        from_stop_name=lambda f: "stop " + f["position"].astype(str),
        to_stop_name=lambda f: "stop " + (f["position"] + 1).astype(str),
    )
    diagram = make_time_space_diagram(
        frame, ordered, service_date="2026-09-07", title="Day"
    )
    assert len(diagram.data[1].x) == _BUSES * 5
    with pytest.raises(ValueError, match="No traversals"):
        make_time_space_diagram(frame, ordered, service_date="2026-01-01", title="x")
