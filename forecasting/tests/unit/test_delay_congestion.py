from dataclasses import replace

import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import (
    make_delay_congestion_map,
    prepare_delay_congestion_snapshots,
    run_delay_congestion_engine,
)


def _static_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stops = pd.DataFrame(
        {
            "stop_id": ["a", "b", "c"],
            "stop_name": ["First", "Second", "Third"],
            "stop_lat": [0.0, 0.0, 0.0],
            "stop_lon": [0.0, 1.0, 2.0],
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["main"] * 3
            + ["express"] * 2
            + ["reverse"] * 3
            + ["variant"] * 3,
            "stop_id": ["a", "b", "c", "a", "c", "c", "b", "a", "a", "b", "c"],
            "stop_sequence": [10, 20, 40, 1, 9, 1, 2, 3, 1, 2, 3],
        }
    )
    trips = pd.DataFrame(
        {
            "route_id": ["97-KEL"] * 4,
            "trip_id": ["main", "express", "reverse", "variant"],
            "direction_id": [0, 0, 1, 0],
            "shape_id": ["forward", "forward", "backward", "different"],
        }
    )
    shapes = pd.DataFrame(
        {
            "shape_id": ["forward"] * 3 + ["backward"] * 3 + ["different"] * 3,
            "shape_pt_sequence": [1, 2, 3] * 3,
            "shape_pt_lat": [0.0] * 9,
            "shape_pt_lon": [0.0, 1.0, 2.0, 2.0, 1.0, 0.0, 0.0, 1.0, 2.0],
        }
    )
    return stops, stop_times, trips, shapes


def _updates(
    delays: tuple[float, float, float] = (300, 420, 360),
    *,
    start: str = "2026-09-08 03:00:00",
    shift: str = "0min",
) -> pd.DataFrame:
    arrivals = pd.date_range("2026-09-08 10:00", periods=3, freq="2min", tz="UTC")
    arrivals += pd.Timedelta(shift)
    return pd.DataFrame(
        {
            "id": [1, 2, 3],
            "trip_route_id": ["97-KEL"] * 3,
            "trip_direction_id": [0] * 3,
            "trip_id": ["main"] * 3,
            "trip_start_timestamp": [start] * 3,
            "stop_id": ["a", "b", "c"],
            "stop_sequence": [10, 20, 40],
            "arrival_delay": list(delays),
            "arrival_time": arrivals,
            "feed_timestamp": arrivals + pd.Timedelta(seconds=15),
            "read_timestamp": arrivals + pd.Timedelta(seconds=20),
        }
    )


def _run(updates: pd.DataFrame, **kwargs):
    return run_delay_congestion_engine(updates, *_static_data(), **kwargs)


@pytest.mark.parametrize(
    ("upstream", "downstream", "expected"),
    [
        (300, 300, 0),
        (300, 420, 120),
        (300, 240, -60),
        (-120, -60, 60),
        (-60, -120, -60),
        (0, 0, 0),
    ],
)
def test_differences_delays_not_absolute_lateness(
    upstream: float,
    downstream: float,
    expected: float,
) -> None:
    result = _run(_updates((upstream, downstream, downstream)))

    assert result.traversals["delta_delay_seconds"].tolist() == [expected, 0]
    assert result.traversals["from_stop_sequence"].tolist() == [10, 20]
    assert result.traversals["to_stop_sequence"].tolist() == [20, 40]
    assert result.congestion["median_delta_delay_seconds"].tolist() == [expected, 0]
    assert result.congestion["traversal_count"].eq(1).all()
    assert result.congestion["time_bin"].eq(pd.Timestamp("2026-09-08 10:00Z")).all()
    assert result.stop_visits["trip_instance_key"].nunique() == 1


def test_latest_rows_and_id_ties_are_used_once_without_mutation() -> None:
    updates = _updates()
    earlier = updates.assign(id=lambda frame: frame["id"] + 10, arrival_delay=999)
    earlier["feed_timestamp"] -= pd.Timedelta(seconds=1)
    tied = updates.iloc[[1]].assign(id=20, arrival_delay=480)
    updates = pd.concat([earlier, updates, tied, tied], ignore_index=True)
    frames = (updates, *_static_data())
    originals = [frame.copy(deep=True) for frame in frames]

    result = run_delay_congestion_engine(*frames)

    assert result.stop_visits["arrival_delay"].tolist() == [300, 480, 360]
    assert result.traversals["delta_delay_seconds"].tolist() == [180, -120]
    assert result.congestion["traversal_count"].eq(1).all()
    for frame, original in zip(frames, originals):
        pdt.assert_frame_equal(frame, original)
    pdt.assert_frame_equal(result.congestion, _run(updates.iloc[::-1]).congestion)


def test_missing_latest_delay_is_not_replaced_and_missing_stop_is_not_bridged() -> None:
    updates = _updates()
    old_c = updates.iloc[[2]].copy()
    updates.loc[2, "arrival_delay"] = float("nan")
    old_c["feed_timestamp"] -= pd.Timedelta(seconds=1)
    with pytest.warns(UserWarning, match="Excluded"):
        result = _run(pd.concat([old_c, updates]))

    assert pd.isna(result.stop_visits.iloc[-1]["arrival_delay"])
    excluded = result.traversals.iloc[-1]
    assert excluded["exclusion_reason"] == "to_missing_latest_delay"
    assert pd.isna(excluded["delta_delay_seconds"])
    assert pd.isna(excluded["time_bin"])
    assert len(result.congestion) == 1
    with pytest.warns(UserWarning, match="Excluded"):
        with pytest.raises(ValueError, match="No completed adjacent"):
            _run(_updates().iloc[[0, 2]])


def test_missing_trip_starts_are_excluded_not_grouped_by_publication_date() -> None:
    reliable = _updates()
    ambiguous = _updates((999, 999, 999)).assign(trip_start_timestamp=None)
    with pytest.warns(UserWarning, match="missing trip starts"):
        result = _run(pd.concat([ambiguous, reliable], ignore_index=True))

    assert len(result.stop_visits) == 3
    count = result.audit.set_index(["stage", "reason"]).loc[
        ("updates", "missing_trip_start"), "count"
    ]
    assert count == 3
    with pytest.warns(UserWarning, match="missing trip starts"):
        with pytest.raises(ValueError, match="reliable trip-start"):
            _run(ambiguous)


def test_normalized_sequence_and_start_keys_do_not_duplicate_traversals() -> None:
    updates = _updates()
    repeated = updates.iloc[[1]].assign(id=10, stop_sequence="20", arrival_delay=480)
    repeated["trip_start_timestamp"] = " 2026-09-08 03:00:00 "

    result = _run(pd.concat([updates, repeated], ignore_index=True))

    assert len(result.stop_visits) == 3
    assert len(result.traversals) == 2
    assert result.traversals["delta_delay_seconds"].tolist() == [180, -120]


def test_daily_instances_and_midnight_crossings_stay_separate() -> None:
    first = _updates((300, 300, 300), start="2026-09-07 23:00:00", shift="4h")
    first["arrival_time"] = pd.to_datetime(
        [
            "2026-09-08 06:59Z",
            "2026-09-08 07:01Z",
            "2026-09-08 07:03Z",
        ]
    )
    first["feed_timestamp"] = first["arrival_time"] + pd.Timedelta(seconds=15)
    first["read_timestamp"] = first["arrival_time"] + pd.Timedelta(seconds=20)
    second = _updates((100, 160, 220), start="2026-09-08 23:00:00", shift="1day")

    result = _run(pd.concat([second, first], ignore_index=True))

    assert result.stop_visits["trip_instance_key"].nunique() == 2
    assert result.traversals.groupby("trip_instance_key")["delta_delay_seconds"].agg(
        list
    ).tolist() == [
        [0, 0],
        [60, 60],
    ]


def test_median_counts_spread_and_sign_shares_use_distinct_traversals() -> None:
    first = _updates((300, 420, 420))
    second = _updates((300, 240, 240), start="2026-09-08 03:01:00")
    third = _updates((300, 300, 300), start="2026-09-08 03:02:00")
    result = _run(pd.concat([first, second, third], ignore_index=True))
    row = result.congestion.iloc[0]

    assert row["median_delta_delay_seconds"] == 0
    assert row["traversal_count"] == 3
    assert row["min_delta_delay_seconds"] == -60
    assert row["max_delta_delay_seconds"] == 120
    assert row["delta_delay_std_seconds"] > 0
    assert row["added_delay_share"] == pytest.approx(1 / 3)
    assert row["recovered_delay_share"] == pytest.approx(1 / 3)
    assert row["unchanged_delay_share"] == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("arrival_time", None, "to_missing_arrival_time"),
        ("arrival_time", pd.Timestamp("2026-09-08 10:05Z"), "to_unfinished_visit"),
        (
            "arrival_time",
            pd.Timestamp("2026-09-08 10:01Z"),
            "nonincreasing_arrival_times",
        ),
        ("stop_id", "unknown", "to_missing_stop_visit"),
        ("stop_sequence", 99, "to_missing_stop_visit"),
    ],
)
def test_invalid_endpoints_are_audited_not_substituted(
    column: str,
    value: object,
    reason: str,
) -> None:
    updates = _updates()
    updates.loc[2, column] = value
    with pytest.warns(UserWarning, match="Excluded"):
        result = _run(updates)

    assert result.traversals.iloc[-1]["exclusion_reason"] == reason
    assert pd.isna(result.traversals.iloc[-1]["delta_delay_seconds"])
    assert len(result.congestion) == 1


def test_directions_shapes_and_express_pairs_are_not_pooled() -> None:
    main = _updates()
    reverse = _updates().assign(
        trip_id="reverse",
        trip_direction_id=1,
        stop_id=["c", "b", "a"],
        stop_sequence=[1, 2, 3],
    )
    variant = _updates((300, 360, 360)).assign(
        trip_id="variant", stop_sequence=[1, 2, 3]
    )
    express = (
        _updates((300, 900, 900))
        .iloc[[0, 2]]
        .assign(
            trip_id="express",
            stop_sequence=[1, 9],
        )
    )
    result = _run(pd.concat([main, reverse, variant, express], ignore_index=True))

    assert result.traversals["trip_direction_id"].eq(0).all()
    assert set(result.traversals["trip_id"]) == {"main", "variant", "express"}
    assert len(result.congestion) == 5
    assert result.congestion["traversal_count"].eq(1).all()
    assert result.traversals.loc[
        result.traversals["trip_id"].eq("express"), "delta_delay_seconds"
    ].tolist() == [600]
    reverse_result = _run(reverse, trip_direction_id=1)
    assert reverse_result.segments["trip_direction_id"].eq(1).all()


def test_proxy_completion_and_both_endpoint_receipts_gate_availability() -> None:
    updates = _updates()
    updates.loc[0, "read_timestamp"] = pd.Timestamp("2026-09-08 10:03Z")
    updates.loc[1, "feed_timestamp"] = pd.Timestamp("2026-09-08 10:01Z")
    updates.loc[1, "read_timestamp"] = pd.Timestamp("2026-09-08 10:01:05Z")
    result = _run(updates)
    first = result.traversals.iloc[0]

    assert first["crossed_at"] == pd.Timestamp("2026-09-08 10:02Z")
    assert first["available_at"] == pd.Timestamp("2026-09-08 10:03Z")
    times = pd.DatetimeIndex(["2026-09-08 10:02:59Z", "2026-09-08 10:03Z"])
    display = prepare_delay_congestion_snapshots(result, times)
    segment = display.loc[display["segment_id"].eq(0)]
    assert segment["status"].tolist() == ["unobserved", "fresh"]
    assert segment["delta_delay_seconds"].iloc[-1] == 120


def test_publication_replay_warns_when_receipts_are_absent() -> None:
    with pytest.warns(UserWarning, match="receipt-time replay is unverified"):
        result = _run(_updates().drop(columns="read_timestamp"))

    assert result.traversals.iloc[0]["available_at"] == pd.Timestamp(
        "2026-09-08 10:02:15Z"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"trip_direction_id": None}, "trip_direction_id"),
        ({"trip_direction_id": True}, "trip_direction_id"),
        ({"time_bin_size": "0min"}, "positive fixed duration"),
        ({"time_bin_size": "-1min"}, "positive fixed duration"),
    ],
)
def test_rejects_invalid_controls(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _run(_updates(), **kwargs)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("stop_sequence", 1.5, "nonnegative integers"),
        ("stop_sequence", None, "nonnegative integers"),
        ("feed_timestamp", None, "missing feed timestamps"),
        ("arrival_delay", float("inf"), "infinite arrival delays"),
        ("arrival_time", "not a date", "not a date"),
    ],
)
def test_rejects_invalid_latest_values(
    column: str, value: object, message: str
) -> None:
    updates = _updates()
    updates[column] = updates[column].astype("object")
    updates.loc[1, column] = value
    with pytest.raises(ValueError, match=message):
        _run(updates)


def test_reports_missing_columns_and_unmatched_routes() -> None:
    with pytest.raises(ValueError, match="missing required columns: arrival_delay"):
        _run(_updates().drop(columns="arrival_delay"))
    with pytest.raises(ValueError, match="No trip updates match"):
        _run(_updates().assign(trip_route_id="other"))


def test_freshness_is_inclusive_and_gaps_do_not_become_zero() -> None:
    result = _run(_updates())
    times = pd.DatetimeIndex(
        [
            "2026-09-08 10:06Z",
            "2026-09-08 10:06:01Z",
        ]
    )
    original = result.congestion.copy(deep=True)
    display = prepare_delay_congestion_snapshots(result, times, max_age="4min")
    first = display.loc[display["segment_id"].eq(0)]

    assert first["status"].tolist() == ["fresh", "stale"]
    assert pd.isna(first["delta_delay_seconds"].iloc[-1])
    assert first["last_delta_delay_seconds"].iloc[-1] == 120
    assert (
        display.loc[display["source_segment_id"].isna(), "delta_delay_seconds"]
        .isna()
        .all()
    )
    pdt.assert_frame_equal(result.congestion, original)


def test_late_old_bins_cannot_replace_newer_crossings_or_refresh_staleness() -> None:
    older = _updates((300, 420, 420))
    older.loc[1, ["feed_timestamp", "read_timestamp"]] = pd.Timestamp(
        "2026-09-08 10:16Z"
    )
    newer = _updates((300, 240, 240), start="2026-09-08 03:10:00", shift="10min")
    result = _run(pd.concat([older, newer], ignore_index=True))
    display = prepare_delay_congestion_snapshots(
        result,
        pd.DatetimeIndex(["2026-09-08 10:15Z", "2026-09-08 10:16Z"]),
    )
    first = display.loc[display["segment_id"].eq(0)]

    assert first["delta_delay_seconds"].tolist() == [-60, -60]
    assert first["observed_at"].eq(pd.Timestamp("2026-09-08 10:12Z")).all()
    only_old = _run(older)
    late = prepare_delay_congestion_snapshots(
        only_old,
        pd.DatetimeIndex(["2026-09-08 10:16Z"]),
        max_age="5min",
    )
    assert late.loc[late["segment_id"].eq(0), "status"].iloc[0] == "stale"


def test_smoothing_uses_available_bins_once_and_does_not_extend_freshness() -> None:
    first = _updates((300, 420, 420))
    second = _updates((300, 240, 240), start="2026-09-08 03:10:00", shift="10min")
    result = _run(pd.concat([first, second], ignore_index=True))
    times = pd.DatetimeIndex(
        [
            "2026-09-08 10:12:19Z",
            "2026-09-08 10:12:20Z",
            "2026-09-08 10:13Z",
            "2026-09-08 10:28Z",
        ]
    )
    display = prepare_delay_congestion_snapshots(
        result, times, smoothing_window="11min"
    )
    rows = display.loc[display["segment_id"].eq(0)]

    assert rows["delta_delay_seconds"].iloc[:3].tolist() == [120, 30, 30]
    assert rows["raw_delta_delay_seconds"].iloc[1:3].tolist() == [-60, -60]
    assert rows["status"].iloc[-1] == "stale"
    assert pd.isna(rows["delta_delay_seconds"].iloc[-1])
    boundary = prepare_delay_congestion_snapshots(
        result, times[1:2], smoothing_window="10min"
    )
    assert (
        boundary.loc[boundary["segment_id"].eq(0), "delta_delay_seconds"].iloc[0] == -60
    )


def test_equivalent_paths_choose_freshest_shape_without_pooling_values() -> None:
    main = _updates((300, 420, 420))
    variant = _updates((300, 240, 240), shift="1min").assign(
        trip_id="variant",
        stop_sequence=[1, 2, 3],
    )
    result = _run(pd.concat([main, variant], ignore_index=True))
    display = prepare_delay_congestion_snapshots(
        result,
        pd.DatetimeIndex(["2026-09-08 10:03:20Z"]),
    )
    row = display.loc[display["segment_id"].eq(0)].iloc[0]

    assert row["delta_delay_seconds"] == -60
    assert row["source_shape_id"] == "different"
    assert row["traversal_count"] == 1
    assert len(row["member_segment_ids"]) == 2


@pytest.mark.parametrize(
    ("times", "kwargs", "message"),
    [
        ([], {}, "times must"),
        (["2026-09-08", "2026-09-08"], {}, "times must"),
        (["2026-09-09", "2026-09-08"], {}, "times must"),
        (["2026-09-08"], {"max_age": "0min"}, "max_age must"),
        (["2026-09-08"], {"smoothing_window": "-1min"}, "smoothing_window must"),
    ],
)
def test_snapshots_reject_invalid_controls(
    times: list, kwargs: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_delay_congestion_snapshots(
            _run(_updates()), pd.DatetimeIndex(times), **kwargs
        )


def test_snapshots_reject_invalid_evidence_times() -> None:
    result = _run(_updates())
    invalid = result.congestion.copy()
    invalid["available_at"] = invalid["observed_at"] - pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="valid proxy evidence"):
        prepare_delay_congestion_snapshots(
            replace(result, congestion=invalid),
            pd.DatetimeIndex(["2026-09-08 10:05Z"]),
        )


def test_an_unobserved_selected_segment_stays_missing() -> None:
    result = _run(_updates())
    segment = result.segments.loc[result.segments["to_stop_sequence"].eq(9)]
    empty = replace(
        result,
        segments=segment,
        congestion=result.congestion.iloc[:0],
    )
    display = prepare_delay_congestion_snapshots(
        empty,
        pd.DatetimeIndex(["2026-09-08 10:05Z"]),
    )

    assert len(display) == 1
    assert display["status"].eq("unobserved").all()
    assert display["delta_delay_seconds"].isna().all()
    assert display["traversal_count"].isna().all()
    figure = make_delay_congestion_map(display, result.route_geometry, title="Unknown")
    assert figure.data[0].line.color == "gray"


def test_map_static_and_animation_share_values_and_do_not_clip_hover() -> None:
    result = _run(_updates((300, 900, 900)))
    times = pd.DatetimeIndex(["2026-09-08 10:01Z", "2026-09-08 10:05Z"])
    display = prepare_delay_congestion_snapshots(result, times)
    animation = make_delay_congestion_map(
        display, result.route_geometry, title="Proxy delays"
    )
    static = make_delay_congestion_map(
        display.loc[display["as_of"].eq(times[-1])],
        result.route_geometry,
        title="Proxy delays",
    )

    assert len(animation.frames) == 2
    assert animation.frames[0].data[0].line.color == "gray"
    assert animation.frames[-1].data[0].line.color == static.data[0].line.color
    assert "+600.0 s" in static.data[0].hovertext
    assert "arrival-delay proxies" in static.data[0].hovertext
    assert (
        animation.layout.updatemenus[0].buttons[0].args[1]["frame"]["duration"] == 250
    )
    assert static.data[-1].marker.cmin == -120
    assert static.data[-1].marker.cmax == 120


@pytest.mark.parametrize(
    "kwargs",
    [
        {"color_limit_seconds": 0},
        {"color_limit_seconds": float("inf")},
        {"frame_duration_ms": -1},
        {"frame_duration_ms": True},
    ],
)
def test_map_rejects_invalid_controls(kwargs: dict) -> None:
    result = _run(_updates())
    display = prepare_delay_congestion_snapshots(
        result, pd.DatetimeIndex(["2026-09-08 10:05Z"])
    )
    with pytest.raises(ValueError):
        make_delay_congestion_map(
            display, result.route_geometry, title="Invalid", **kwargs
        )
