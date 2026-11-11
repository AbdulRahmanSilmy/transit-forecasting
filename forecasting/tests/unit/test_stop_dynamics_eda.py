import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import prepare_stop_dynamics_eda, run_congestion_engine
from forecasting.stop_dynamics_eda import _segment_catalogue


def _static_data():
    stops = pd.DataFrame(
        {
            "stop_id": ["a", "b", "c"],
            "stop_name": ["First", "Second", "Third"],
            "stop_lat": [0.0] * 3,
            "stop_lon": [0.0, 1.0, 2.0],
        }
    )
    stop_times = pd.DataFrame(
        {
            "trip_id": ["main"] * 3
            + ["express"] * 2
            + ["variant"] * 3
            + ["reverse"] * 3,
            "stop_id": ["a", "b", "c", "a", "c", "a", "b", "c", "c", "b", "a"],
            "stop_sequence": [10, 20, 40, 1, 9, 1, 2, 3, 1, 2, 3],
        }
    )
    trips = pd.DataFrame(
        {
            "route_id": ["97-KEL"] * 4,
            "trip_id": ["main", "express", "variant", "reverse"],
            "direction_id": [0, 0, 0, 1],
            "shape_id": ["forward", "forward", "different", "backward"],
        }
    )
    shapes = pd.DataFrame(
        {
            "shape_id": ["forward"] * 3 + ["different"] * 3 + ["backward"] * 3,
            "shape_pt_sequence": [1, 2, 3] * 3,
            "shape_pt_lat": [0.0] * 9,
            "shape_pt_lon": [0.0, 1.0, 2.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0],
        }
    )
    return stops, stop_times, trips, shapes


def _updates(delays=(300.0, 420.0, 360.0), shift="0min", start="2026-09-08 03:00"):
    arrivals = pd.date_range("2026-09-08 10:00Z", periods=3, freq="2min")
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
            "arrival_delay": delays,
            "arrival_time": arrivals,
            "feed_timestamp": arrivals + pd.Timedelta(seconds=15),
        }
    )


def _probes():
    return pd.DataFrame(
        {
            "trip_route_id": ["97-KEL"] * 3,
            "trip_direction_id": [0] * 3,
            "trip_id": ["main"] * 3,
            "stop_id": ["b"] * 3,
            "current_stop_sequence": [20] * 3,
            "feed_timestamp": pd.to_datetime(
                ["2026-09-08 10:01Z", "2026-09-08 10:03Z", "2026-09-08 10:06Z"]
            ),
            "position_speed": [10.0, 20.0, 5.0],
            "vehicle_id": ["v1", "v1", "v2"],
        }
    )


def _run(updates=None, probes=None, **kwargs):
    stops, times, trips, _ = _static_data()
    return prepare_stop_dynamics_eda(
        _probes() if probes is None else probes,
        _updates() if updates is None else updates,
        stops,
        times,
        trips,
        segment_id=kwargs.pop("segment_id", 0),
        **kwargs,
    )


@pytest.mark.parametrize("direction", [0, 1])
def test_catalogue_matches_geometry_engine_numbering(direction):
    stops, times, trips, shapes = _static_data()
    catalogue, lookup = _segment_catalogue(stops, times, trips, "97-KEL", direction)
    engine = run_congestion_engine(
        _probes(), stops, times, trips, shapes, trip_direction_id=direction
    )
    columns = [
        "segment_id",
        "trip_direction_id",
        "shape_id",
        "from_stop_id",
        "to_stop_id",
        "to_stop_sequence",
    ]
    pdt.assert_frame_equal(
        catalogue[columns].reset_index(drop=True),
        engine.segments[columns].reset_index(drop=True),
        check_dtype=False,
    )
    assert not lookup.duplicated(["trip_id", "to_stop_sequence"]).any()


def test_engine_equivalent_probe_weighting_baseline_and_input_preservation():
    probes, updates = _probes(), _updates()
    stops, times, trips, shapes = _static_data()
    frames = (probes, updates, stops, times, trips)
    originals = [frame.copy(deep=True) for frame in frames]
    result = prepare_stop_dynamics_eda(*frames, segment_id=0)
    engine = run_congestion_engine(
        probes, stops, times, trips, shapes, trip_direction_id=0
    )
    expected = engine.congestion.query("segment_id == 0").set_index("time_bin")
    pdt.assert_frame_equal(
        result.speed_bins.loc[expected.index, ["observed_speed", "baseline_speed"]],
        expected[["observed_speed", "baseline_speed"]],
    )
    assert result.speed_bins["baseline_speed"].eq(10).all()
    assert result.speed_bins.iloc[0]["observed_speed"] == 15
    assert result.speed_bins.iloc[0]["probe_count"] == 2
    assert result.speed_bins.iloc[0]["bus_count"] == 1
    for frame, original in zip(frames, originals):
        pdt.assert_frame_equal(frame, original)


@pytest.mark.parametrize(
    "upstream,current,expected",
    [
        (300, 420, 120),
        (300, 300, 0),
        (300, 240, -60),
        (-120, -60, 60),
        (-60, -120, -60),
    ],
)
def test_signed_spatial_change_and_delay_bin_units(upstream, current, expected):
    result = _run(_updates((upstream, current, current)))
    assert result.delay_changes["delta_delay_seconds"].tolist() == [expected]
    assert result.delay_changes["from_stop_sequence"].tolist() == [10]
    assert result.stop_visits["arrival_delay"].tolist() == [current]
    assert result.delay_bins["median_delta_delay_seconds"].dropna().tolist() == [
        expected
    ]
    assert result.delay_bins["median_current_delay_seconds"].dropna().tolist() == [
        current
    ]
    assert result.delay_bins["pair_count"].sum() == 1


def test_latest_rows_timestamp_and_id_ties_do_not_use_earlier_predictions():
    updates = _updates()
    older = updates.assign(arrival_delay=999)
    older["feed_timestamp"] -= pd.Timedelta(seconds=1)
    tied = updates.iloc[[1]].assign(id=20, arrival_delay=480)
    rows = pd.concat([older, updates, tied, tied], ignore_index=True)
    result = _run(rows)
    assert result.stop_visits["arrival_delay"].tolist() == [480]
    assert result.delay_changes["delta_delay_seconds"].tolist() == [180]
    pdt.assert_frame_equal(result.delay_bins, _run(rows.iloc[::-1]).delay_bins)


@pytest.mark.parametrize(
    "endpoint,reason,visits",
    [
        (0, "upstream_missing_latest_delay", 1),
        (1, "current_missing_latest_delay", 0),
    ],
)
def test_missing_latest_delay_remains_missing(endpoint, reason, visits):
    updates = _updates()
    older = updates.iloc[[endpoint]].copy()
    older["feed_timestamp"] -= pd.Timedelta(seconds=1)
    updates.loc[endpoint, "arrival_delay"] = np.nan
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(pd.concat([older, updates], ignore_index=True))
    assert result.delay_changes["exclusion_reason"].tolist() == [reason]
    assert result.delay_changes["delta_delay_seconds"].isna().all()
    assert result.delay_bins["visit_count"].sum() == visits
    assert result.delay_bins["pair_count"].sum() == 0


def test_missing_upstream_preserves_current_and_does_not_bridge_stop():
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(_updates().iloc[[1, 2]])
    assert result.delay_changes["exclusion_reason"].tolist() == [
        "upstream_missing_visit"
    ]
    assert result.delay_bins["visit_count"].sum() == 1
    assert result.delay_bins["pair_count"].sum() == 0
    assert result.upstream_visits.empty
    assert result.delay_bins["upstream_visit_count"].sum() == 0
    assert result.delay_bins["median_upstream_delay_seconds"].isna().all()
    with pytest.warns(UserWarning, match="excluded"):
        missing_middle = _run(
            _updates().iloc[[0, 2]],
            segment_id=1,
            probes=_probes().assign(stop_id="c", current_stop_sequence=40),
        )
    assert missing_middle.delay_changes["from_stop_id"].tolist() == ["b"]
    assert missing_middle.delay_changes["exclusion_reason"].tolist() == [
        "upstream_missing_visit"
    ]


def test_daily_trip_instances_and_midnight_crossings_stay_separate():
    first = _updates(shift="13h59min")
    first["trip_start_timestamp"] = "2026-09-08 16:59"
    second = _updates((60, 120, 120), shift="37h59min", start="2026-09-09 16:59")
    result = _run(pd.concat([first, second], ignore_index=True))
    assert result.delay_changes["delta_delay_seconds"].tolist() == [120, 60]
    assert result.stop_visits["trip_instance_key"].nunique() == 2
    assert result.stop_visits["arrival_time"].dt.hour.tolist() == [0, 0]
    assert result.delay_changes["upstream_arrival_time"].dt.day.tolist() == [8, 9]
    assert result.delay_changes["arrival_time"].dt.day.tolist() == [9, 10]


def test_missing_start_and_unmatched_historical_trip_are_audited():
    unknown = _updates().assign(trip_id="obsolete-trip")
    missing = _updates().assign(trip_start_timestamp=None)
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(pd.concat([_updates(), unknown, missing], ignore_index=True))
    audit = result.audit.set_index(["stage", "reason"])["count"]
    assert audit.loc[("updates", "unmatched_selected_segment")] == 2
    assert audit.loc[("updates", "missing_trip_start")] == 2
    assert len(result.delay_changes) == 1
    assert result.coverage["candidate_updates"].sum() == 6
    assert result.coverage["matched_updates"].sum() == 4


def test_other_shapes_directions_and_wrong_stop_sequences_are_not_pooled():
    probes = pd.concat(
        [
            _probes(),
            _probes().assign(
                trip_id="variant", current_stop_sequence=2, position_speed=100
            ),
            _probes().assign(
                trip_id="reverse",
                trip_direction_id=1,
                current_stop_sequence=2,
                position_speed=100,
            ),
            _probes().assign(current_stop_sequence=40, position_speed=100),
        ],
        ignore_index=True,
    )
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(probes=probes)
    assert result.speed_bins["probe_count"].sum() == 3
    assert result.speed_bins["baseline_speed"].eq(10).all()
    assert (
        result.audit.query(
            "stage == 'probes' and reason == 'unmatched_selected_segment'"
        )["count"].iloc[0]
        == 6
    )


@pytest.mark.parametrize(
    "endpoint,flag,reason",
    [
        (0, 1, "upstream_skipped_visit"),
        (1, 1, "current_skipped_visit"),
        (0, 2, "upstream_no_data_visit"),
    ],
)
def test_latest_skipped_or_no_data_endpoints_are_excluded(endpoint, flag, reason):
    updates = _updates().assign(schedule_relationship=0)
    older = updates.iloc[[endpoint]].copy()
    older["feed_timestamp"] -= pd.Timedelta(seconds=1)
    updates.loc[endpoint, "schedule_relationship"] = flag
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(pd.concat([updates, older], ignore_index=True))
    assert result.delay_changes["exclusion_reason"].tolist() == [reason]


def test_cancelled_trip_does_not_supply_delay_points():
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(_updates().assign(trip_schedule_relationship=3))
    assert result.stop_visits["exclusion_reason"].tolist() == ["cancelled_trip"]
    assert result.delay_bins["visit_count"].sum() == 0


def test_duplicated_trip_schedule_flag_is_not_treated_as_cancellation():
    result = _run(_updates().assign(trip_schedule_relationship=6))
    assert result.stop_visits["exclusion_reason"].tolist() == ["included"]
    assert result.delay_changes["delta_delay_seconds"].tolist() == [120]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("unfinished", "current_unfinished_visit"),
        ("reversed", "nonincreasing_arrival_times"),
        ("missing", "current_missing_arrival_time"),
    ],
)
def test_missing_unfinished_and_nonincreasing_arrivals(case, reason):
    updates = _updates()
    if case == "unfinished":
        updates.loc[1, "arrival_time"] += pd.Timedelta("1h")
    elif case == "reversed":
        updates.loc[0, "arrival_time"] = updates.loc[1, "arrival_time"]
    else:
        updates.loc[1, "arrival_time"] = pd.NaT
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(updates)
    assert result.delay_changes["exclusion_reason"].tolist() == [reason]


def test_upstream_before_recording_boundary_is_retained_for_current_visit():
    updates = _updates()
    updates.loc[0, "arrival_time"] -= pd.Timedelta("1h")
    result = _run(updates)
    assert result.delay_changes["delta_delay_seconds"].tolist() == [120]
    assert result.upstream_visits["exclusion_reason"].tolist() == [
        "before_recording_window"
    ]
    assert result.delay_bins["upstream_visit_count"].sum() == 0


def test_upstream_visits_and_medians_use_their_own_arrival_clock():
    first = _updates(shift="2min")
    second = _updates((60, 120, 120), shift="4min", start="2026-09-08 03:04")
    result = _run(pd.concat([first, second], ignore_index=True))
    assert result.upstream_visits["arrival_delay"].tolist() == [300, 60]
    assert result.upstream_visits["arrival_time"].tolist() == [
        pd.Timestamp("2026-09-08 10:02Z"),
        pd.Timestamp("2026-09-08 10:04Z"),
    ]
    upstream_bin = pd.Timestamp("2026-09-08 10:00Z")
    assert result.delay_bins.loc[upstream_bin, "median_upstream_delay_seconds"] == 180
    assert result.delay_bins.loc[upstream_bin, "upstream_visit_count"] == 2
    assert result.coverage["usable_upstream_visits"].sum() == 2


def test_upstream_visits_remain_available_without_current_visit():
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(_updates(shift="2min").iloc[[0, 2]])
    assert result.stop_visits.empty
    assert result.delay_changes.empty
    assert result.upstream_visits["arrival_delay"].tolist() == [300]
    assert result.upstream_visits["exclusion_reason"].tolist() == ["included"]
    assert result.delay_bins["upstream_visit_count"].sum() == 1
    assert result.delay_bins["visit_count"].sum() == 0


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing_delay", "missing_latest_delay"),
        ("skipped", "skipped_visit"),
        ("unfinished", "unfinished_visit"),
    ],
)
def test_unusable_latest_upstream_values_are_not_plotted(case, reason):
    updates = _updates(shift="2min").assign(schedule_relationship=0)
    earlier = updates.iloc[[0]].copy()
    earlier["feed_timestamp"] -= pd.Timedelta("1s")
    if case == "missing_delay":
        updates.loc[0, "arrival_delay"] = np.nan
    elif case == "skipped":
        updates.loc[0, "schedule_relationship"] = 1
    else:
        updates.loc[0, "arrival_time"] += pd.Timedelta("1h")
    with pytest.warns(UserWarning, match="excluded"):
        result = _run(pd.concat([updates, earlier], ignore_index=True))
    assert result.upstream_visits["exclusion_reason"].tolist() == [reason]
    assert result.delay_bins["upstream_visit_count"].sum() == 0
    assert result.delay_bins["median_upstream_delay_seconds"].isna().all()


def test_repeated_stops_are_joined_by_each_actual_predecessor_sequence():
    stops, _, trips, _ = _static_data()
    trips = trips.query("trip_id == 'main'").copy()
    times = pd.DataFrame(
        {
            "trip_id": ["main"] * 5,
            "stop_id": ["a", "b", "a", "b", "c"],
            "stop_sequence": [10, 20, 30, 40, 50],
        }
    )
    arrivals = pd.date_range("2026-09-08 10:00Z", periods=5, freq="2min")
    updates = pd.DataFrame(
        {
            "id": range(5),
            "trip_route_id": ["97-KEL"] * 5,
            "trip_direction_id": [0] * 5,
            "trip_id": ["main"] * 5,
            "trip_start_timestamp": ["2026-09-08 03:00"] * 5,
            "stop_id": ["a", "b", "a", "b", "c"],
            "stop_sequence": [10, 20, 30, 40, 50],
            "arrival_delay": [60, 120, 180, 150, 150],
            "arrival_time": arrivals,
            "feed_timestamp": arrivals + pd.Timedelta("15s"),
        }
    )
    result = prepare_stop_dynamics_eda(
        _probes(), updates, stops, times, trips, segment_id=0
    )
    assert result.delay_changes["delta_delay_seconds"].tolist() == [60, -30]
    assert result.delay_changes["from_stop_sequence"].tolist() == [10, 30]
    assert result.upstream_visits["stop_sequence"].tolist() == [10, 30]
    assert result.upstream_visits["arrival_delay"].tolist() == [60, 180]


def test_gaps_remain_missing_with_zero_support_not_zero_measurements():
    updates = _updates(shift="30min")
    result = _run(updates)
    gap = pd.Timestamp("2026-09-08 10:15Z")
    assert pd.isna(result.speed_bins.loc[gap, "observed_speed"])
    assert pd.isna(result.delay_bins.loc[gap, "median_current_delay_seconds"])
    assert pd.isna(result.delay_bins.loc[gap, "median_delta_delay_seconds"])
    assert pd.isna(result.delay_bins.loc[gap, "median_upstream_delay_seconds"])
    assert result.delay_bins.loc[gap, "upstream_visit_count"] == 0
    assert result.speed_bins.loc[gap, "probe_count"] == 0
    assert result.delay_bins.loc[gap, "pair_count"] == 0
    assert result.speed_bins.index.equals(result.delay_bins.index)


def test_utc_bins_distinguish_repeated_local_hour_at_dst_transition():
    first = _updates(
        shift=pd.Timedelta(days=54) - pd.Timedelta("90min"), start="2026-11-01 00:30"
    )
    second = _updates(
        shift=pd.Timedelta(days=54) - pd.Timedelta("30min"), start="2026-11-01 01:30"
    )
    result = _run(pd.concat([first, second], ignore_index=True))
    bins = result.delay_bins.query("pair_count > 0").index
    assert len(bins) == 2
    local = bins.tz_convert("America/Vancouver")
    assert local.strftime("%H:%M").tolist() == ["01:30", "01:30"]
    assert local.strftime("%z").tolist() == ["-0700", "-0800"]


def test_empty_matched_updates_produce_explicit_warning_and_delay_gaps():
    with pytest.warns(UserWarning):
        result = _run(_updates().assign(trip_id="obsolete"))
    assert result.stop_visits.empty
    assert result.delay_changes.empty
    assert result.delay_bins["visit_count"].eq(0).all()
    assert result.delay_bins["median_current_delay_seconds"].isna().all()


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"route_id": ""}, "route_id"),
        ({"trip_direction_id": True}, "direction"),
        ({"trip_direction_id": 2}, "direction"),
        ({"segment_id": True}, "segment_id"),
        ({"segment_id": 1.5}, "segment_id"),
        ({"segment_id": 999}, "No segment"),
        ({"time_bin_size": "0min"}, "positive"),
        ({"time_bin_size": "1M"}, "ambiguous"),
    ],
)
def test_invalid_configuration_is_explicit(kwargs, match):
    with pytest.raises(ValueError, match=match):
        _run(**kwargs)


@pytest.mark.parametrize(
    "speeds,match",
    [
        ([0.25] * 3, "above 0.5"),
        ([None] * 3, "No valid"),
        ([-1.0] * 3, "No valid"),
        ([np.inf] * 3, "infinite"),
    ],
)
def test_invalid_or_ineligible_speed_data_is_explicit(speeds, match):
    with pytest.raises(ValueError, match=match):
        _run(probes=_probes().assign(position_speed=speeds))


def test_missing_required_columns_and_duplicate_static_keys_are_explicit():
    with pytest.raises(ValueError, match="arrival_delay"):
        _run(_updates().drop(columns="arrival_delay"))
    stops, times, trips, _ = _static_data()
    with pytest.raises(ValueError, match="duplicate trip/stop sequences"):
        prepare_stop_dynamics_eda(
            _probes(),
            _updates(),
            stops,
            pd.concat([times, times.iloc[[0]]]),
            trips,
            segment_id=0,
        )
