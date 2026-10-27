from dataclasses import replace

import pandas as pd
import pandas.testing as pdt
import pytest
from shapely.geometry import LineString, MultiLineString

from forecasting import (
    CongestionResult,
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
