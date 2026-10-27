from dataclasses import replace
from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest
from shapely.geometry import LineString, MultiLineString

from forecasting import (
    CongestionResult,
    get_congestion_service_activity,
    make_congestion_map,
    prepare_congestion_snapshots,
)


def _result() -> CongestionResult:
    segments = pd.DataFrame(
        {
            "segment_id": [0, 1],
            "trip_direction_id": [0, 0],
            "shape_id": ["main", "main"],
            "from_stop_id": ["a", "b"],
            "to_stop_id": ["b", "c"],
            "from_stop_name": ["First", "Second"],
            "to_stop_name": ["Second", "Third"],
            "geometry": [LineString([(0, 0), (1, 0)]), LineString([(1, 0), (2, 0)])],
        }
    )
    times = pd.to_datetime(["2026-10-24 10:01Z", "2026-10-24 10:06Z"])
    congestion = pd.DataFrame(
        {
            "segment_id": [0, 0],
            "time_bin": times.floor("5min"),
            "congestion_index": [0.2, 0.8],
        }
    )
    probes = congestion[["segment_id", "time_bin"]].assign(feed_timestamp=times)
    return CongestionResult(
        route_id="97-KEL",
        representative_trip_id="trip",
        route_geometry=MultiLineString(segments["geometry"].tolist()),
        segments=segments,
        vehicle_probes=probes,
        baseline_speeds=pd.DataFrame(),
        congestion=congestion,
    )


def test_seeds_history_expires_by_actual_probe_time_and_preserves_inputs() -> None:
    result = _result()
    originals = [
        frame.copy(deep=True)
        for frame in (result.segments, result.congestion, result.vehicle_probes)
    ]
    times = pd.to_datetime(
        ["2026-10-24 10:10Z", "2026-10-24 10:21Z", "2026-10-24 10:21:01Z"],
        format="mixed",
    )

    snapshots = prepare_congestion_snapshots(result, times, max_age="15min")
    observed = snapshots.loc[snapshots["segment_id"].eq(0)]

    assert observed["congestion_index"].iloc[:2].tolist() == [0.8, 0.8]
    assert pd.isna(observed["congestion_index"].iloc[2])
    assert observed["last_congestion_index"].eq(0.8).all()
    assert observed["observed_at"].eq(pd.Timestamp("2026-10-24 10:06Z")).all()
    assert snapshots.loc[snapshots["segment_id"].eq(1), "congestion_index"].isna().all()
    for frame, original in zip(
        (result.segments, result.congestion, result.vehicle_probes), originals
    ):
        pdt.assert_frame_equal(frame, original)


def test_bin_label_does_not_make_future_probes_available() -> None:
    snapshots = prepare_congestion_snapshots(
        _result(), pd.to_datetime(["2026-10-24 10:00Z", "2026-10-24 10:05Z"])
    )
    observed = snapshots.loc[snapshots["segment_id"].eq(0)]
    assert pd.isna(observed["congestion_index"].iloc[0])
    assert observed["congestion_index"].iloc[1] == 0.2
    assert observed["observed_at"].iloc[1] == pd.Timestamp("2026-10-24 10:01Z")


def test_empty_estimates_remain_missing() -> None:
    result = _result()
    result = replace(
        result,
        congestion=result.congestion.iloc[:0],
        vehicle_probes=result.vehicle_probes.iloc[:0],
    )
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:00Z"])
    )
    assert len(snapshots) == 2
    assert snapshots["congestion_index"].isna().all()


@pytest.mark.parametrize("max_age", ["0min", "-1min", "NaT", "invalid"])
def test_rejects_invalid_maximum_age(max_age: str) -> None:
    with pytest.raises(ValueError):
        prepare_congestion_snapshots(
            _result(), pd.to_datetime(["2026-10-24 10:00Z"]), max_age=max_age
        )


@pytest.mark.parametrize(
    "times", [[], ["NaT"], ["2026-10-24", "2026-10-24"], ["2026-10-25", "2026-10-24"]]
)
def test_rejects_invalid_frame_times(times: list[str]) -> None:
    with pytest.raises(ValueError, match="times must"):
        prepare_congestion_snapshots(_result(), pd.DatetimeIndex(times))


def test_rejects_estimate_without_probe_provenance() -> None:
    result = _result()
    with pytest.raises(ValueError, match="contributing probes"):
        prepare_congestion_snapshots(
            replace(result, vehicle_probes=result.vehicle_probes.iloc[:0]),
            pd.to_datetime(["2026-10-24 10:00Z"]),
        )


def test_latest_map_equals_final_animation_frame() -> None:
    result = _result()
    times = pd.to_datetime(["2026-10-24 10:05Z", "2026-10-24 10:10Z"])
    history = prepare_congestion_snapshots(result, times)
    latest = prepare_congestion_snapshots(result, times[-1:])
    pdt.assert_frame_equal(
        history.loc[history["as_of"].eq(times[-1])].reset_index(drop=True), latest
    )
    animation = make_congestion_map(history, result.route_geometry, title="History")
    snapshot = make_congestion_map(latest, result.route_geometry, title="Latest")
    assert [trace.to_plotly_json() for trace in animation.frames[-1].data] == [
        trace.to_plotly_json() for trace in snapshot.data
    ]
    assert "Age:" in snapshot.data[0].hovertext
    assert "Observed (UTC):" in snapshot.data[0].hovertext
    assert len(snapshot.layout.updatemenus[0].buttons[0].args[0]["visible"]) == len(
        snapshot.data
    )


def test_rejects_inconsistent_map_frames() -> None:
    result = _result()
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:05Z", "2026-10-24 10:10Z"])
    )
    with pytest.raises(ValueError, match="same segments"):
        make_congestion_map(snapshots.iloc[:-1], result.route_geometry, title="Invalid")


def _with_duplicate(
    result: CongestionResult, *, observed_at: str | None = None
) -> CongestionResult:
    duplicate = result.segments.iloc[[0]].assign(segment_id=2, shape_id="short")
    segments = pd.concat([result.segments, duplicate], ignore_index=True)
    if observed_at is None:
        return replace(result, segments=segments)
    timestamp = pd.Timestamp(observed_at)
    observation = pd.DataFrame(
        {
            "segment_id": [2],
            "time_bin": [timestamp.floor("5min")],
            "congestion_index": [0.6],
        }
    )
    probe = observation[["segment_id", "time_bin"]].assign(feed_timestamp=timestamp)
    return replace(
        result,
        segments=segments,
        congestion=pd.concat([result.congestion, observation], ignore_index=True),
        vehicle_probes=pd.concat([result.vehicle_probes, probe], ignore_index=True),
    )


def test_missing_duplicate_cannot_hide_observed_identical_road() -> None:
    result = _with_duplicate(_result())
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:10Z"])
    )
    assert snapshots["segment_id"].tolist() == [0, 1]
    road = snapshots.iloc[0]
    assert road["congestion_index"] == 0.8
    assert road["member_segment_ids"] == (0, 2)
    assert road["member_shape_ids"] == ("main", "short")
    assert road["source_segment_id"] == 0
    assert road["source_shape_id"] == "main"
    figure = make_congestion_map(snapshots, result.route_geometry, title="Observed")
    assert len(figure.data) == 2
    assert figure.data[0].line.color != "gray"
    assert "Equivalent segments: (0, 2)" in figure.data[0].hovertext


def test_freshest_source_changes_without_changing_display_identity() -> None:
    result = _with_duplicate(_result(), observed_at="2026-10-24 10:08Z")
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:07Z", "2026-10-24 10:09Z"])
    )
    road = snapshots.loc[snapshots["segment_id"].eq(0)]
    assert road["source_segment_id"].tolist() == [0, 2]
    assert road["congestion_index"].tolist() == [0.8, 0.6]
    assert road["member_segment_ids"].tolist() == [(0, 2), (0, 2)]


def test_equal_timestamps_use_lowest_source_id_independent_of_input_order() -> None:
    result = _with_duplicate(_result(), observed_at="2026-10-24 10:06Z")
    times = pd.to_datetime(["2026-10-24 10:10Z"])
    first = prepare_congestion_snapshots(result, times)
    reversed_result = replace(
        result,
        segments=result.segments.iloc[::-1],
        congestion=result.congestion.iloc[::-1],
        vehicle_probes=result.vehicle_probes.iloc[::-1],
    )
    second = prepare_congestion_snapshots(reversed_result, times)
    pdt.assert_frame_equal(first, second)
    assert first.iloc[0]["source_segment_id"] == 0


@pytest.mark.parametrize("difference", ["direction", "stop_pair", "geometry"])
def test_only_equivalent_directed_stop_pair_paths_are_combined(difference: str) -> None:
    result = _with_duplicate(_result())
    segments = result.segments.copy()
    if difference == "direction":
        segments.loc[segments["segment_id"].eq(2), "trip_direction_id"] = 1
    elif difference == "stop_pair":
        segments.loc[segments["segment_id"].eq(2), "to_stop_id"] = "express"
    else:
        segments.at[2, "geometry"] = LineString([(0, 0), (0.5, 1), (1, 0)])
    snapshots = prepare_congestion_snapshots(
        replace(result, segments=segments), pd.to_datetime(["2026-10-24 10:10Z"])
    )
    assert snapshots["segment_id"].tolist() == [0, 1, 2]
    assert snapshots["member_segment_ids"].tolist() == [(0,), (1,), (2,)]


def test_geometrically_equal_paths_with_extra_vertices_are_combined() -> None:
    result = _with_duplicate(_result())
    result.segments.at[2, "geometry"] = LineString([(0, 0), (0.5, 0), (1, 0)])
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:10Z"])
    )
    assert snapshots["segment_id"].tolist() == [0, 1]


def _schedule(
    root: Path, *, departure: str = "05:44:00", arrival: str = "05:55:00"
) -> None:
    pd.DataFrame({"agency_timezone": ["America/Vancouver"]}).to_csv(
        root / "agency.txt", index=False
    )
    pd.DataFrame(
        {
            "trip_id": ["trip"],
            "route_id": ["97-KEL"],
            "shape_id": ["main"],
            "direction_id": [0],
            "service_id": ["weekday"],
        }
    ).to_csv(root / "trips.txt", index=False)
    pd.DataFrame(
        {
            "trip_id": ["trip"] * 3,
            "stop_id": ["a", "b", "c"],
            "stop_sequence": [1, 2, 3],
            "arrival_time": [departure, departure, arrival],
            "departure_time": [departure, departure, arrival],
        }
    ).to_csv(root / "stop_times.txt", index=False)
    pd.DataFrame(
        {
            "service_id": ["weekday"],
            **{
                day: [1]
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
            },
            "saturday": [0],
            "sunday": [0],
            "start_date": [20261001],
            "end_date": [20261031],
        }
    ).to_csv(root / "calendar.txt", index=False)


def test_service_calendar_explains_inactive_weekend_variants(tmp_path: Path) -> None:
    _schedule(tmp_path)
    activity = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-24 01:06Z", end="2026-10-26 01:05Z"
    )
    assert activity["scheduled_trips"].tolist() == [0, 0]
    assert not activity["service_active"].any()
    friday = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-23 12:44Z", end="2026-10-23 12:50Z"
    )
    assert friday["scheduled_trips"].tolist() == [1, 1]


@pytest.mark.parametrize("calendar_only", [False, True])
def test_dated_service_additions_work_with_or_without_weekly_calendar(
    tmp_path: Path, calendar_only: bool
) -> None:
    _schedule(tmp_path)
    if calendar_only:
        (tmp_path / "calendar.txt").unlink()
    pd.DataFrame(
        {"service_id": ["weekday"], "date": [20261024], "exception_type": [1]}
    ).to_csv(tmp_path / "calendar_dates.txt", index=False)
    activity = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-24 12:44Z", end="2026-10-24 12:50Z"
    )
    assert activity["scheduled_trips"].tolist() == [1, 1]


def test_service_removals_override_weekly_calendar(tmp_path: Path) -> None:
    _schedule(tmp_path)
    pd.DataFrame(
        {"service_id": ["weekday"], "date": [20261023], "exception_type": [2]}
    ).to_csv(tmp_path / "calendar_dates.txt", index=False)
    activity = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-23 12:44Z", end="2026-10-23 12:50Z"
    )
    assert activity["scheduled_trips"].eq(0).all()


def test_service_times_beyond_midnight_use_previous_service_day(tmp_path: Path) -> None:
    _schedule(tmp_path, departure="25:10:00", arrival="25:30:00")
    activity = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-24 08:15Z", end="2026-10-24 08:20Z"
    )
    assert activity["scheduled_trips"].tolist() == [1, 1]


def test_service_interval_overlap_is_inclusive(tmp_path: Path) -> None:
    _schedule(tmp_path)
    activity = get_congestion_service_activity(
        _result(), tmp_path, start="2026-10-23 12:55Z", end="2026-10-23 12:55Z"
    )
    assert activity["service_active"].all()


def test_missing_calendar_is_explicit_error(tmp_path: Path) -> None:
    _schedule(tmp_path)
    (tmp_path / "calendar.txt").unlink()
    with pytest.raises(FileNotFoundError, match="calendar.txt or calendar_dates.txt"):
        get_congestion_service_activity(
            _result(), tmp_path, start="2026-10-24", end="2026-10-25"
        )


def test_unknown_service_definition_is_explicit_error(tmp_path: Path) -> None:
    _schedule(tmp_path)
    calendar = pd.read_csv(tmp_path / "calendar.txt")
    calendar["service_id"] = "different"
    calendar.to_csv(tmp_path / "calendar.txt", index=False)
    with pytest.raises(ValueError, match="Missing service definitions"):
        get_congestion_service_activity(
            _result(), tmp_path, start="2026-10-24", end="2026-10-25"
        )


@pytest.mark.parametrize(
    "start,end", [("NaT", "2026-10-25"), ("2026-10-25", "2026-10-24")]
)
def test_rejects_invalid_service_period(tmp_path: Path, start: str, end: str) -> None:
    with pytest.raises(ValueError, match="valid start <= end"):
        get_congestion_service_activity(_result(), tmp_path, start=start, end=end)


def test_fresh_stale_unobserved_and_unavailable_states() -> None:
    times = pd.to_datetime(
        ["2026-10-24 10:00Z", "2026-10-24 10:10Z", "2026-10-24 11:00Z"]
    )
    result = _result()
    snapshots = prepare_congestion_snapshots(result, times)
    assert snapshots.loc[snapshots["segment_id"].eq(0), "status"].tolist() == [
        "unobserved",
        "fresh",
        "stale",
    ]
    assert snapshots.loc[snapshots["segment_id"].eq(1), "status"].eq("unobserved").all()
    unavailable = prepare_congestion_snapshots(
        replace(result, congestion=result.congestion.iloc[:0]), times
    )
    assert unavailable.loc[unavailable["segment_id"].eq(0), "status"].tolist() == [
        "unobserved",
        "unavailable",
        "unavailable",
    ]


def test_inactive_duplicate_is_reported_without_hiding_fresh_source() -> None:
    result = _with_duplicate(_result())
    activity = pd.DataFrame({"segment_id": [0, 1, 2], "scheduled_trips": [2, 0, 0]})
    snapshots = prepare_congestion_snapshots(
        result, pd.to_datetime(["2026-10-24 10:10Z"]), service_activity=activity
    )
    assert snapshots["status"].tolist() == ["fresh", "inactive"]
    assert snapshots.iloc[0]["inactive_shape_ids"] == ("short",)
    assert snapshots.iloc[0]["scheduled_trips"] == 2
    figure = make_congestion_map(snapshots, result.route_geometry, title="States")
    assert "Inactive shapes in selected period: ('short',)" in figure.data[0].hovertext
    assert "Inactive service variant" in figure.data[1].hovertext


def test_incomplete_service_metadata_is_rejected() -> None:
    with pytest.raises(ValueError, match="one valid count per segment"):
        prepare_congestion_snapshots(
            _result(),
            pd.to_datetime(["2026-10-24 10:10Z"]),
            service_activity=pd.DataFrame({"segment_id": [0], "scheduled_trips": [1]}),
        )
