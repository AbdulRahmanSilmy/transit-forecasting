import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import make_delay_event_plot


def _anomalies() -> pd.DataFrame:
    """Return two service days with repeated segment delay episodes."""
    rows = []
    for day in pd.date_range("2026-11-01", periods=2):
        for minute, value in zip(
            [0, 10, 20, 30, 40, 50, 60], [0, 30, 120, 60, 20, 0, -10]
        ):
            rows.append(
                {
                    "segment_id": 8,
                    "position": 2,
                    "service_date": day,
                    "is_weekend": day.dayofweek >= 5,
                    "local_time": day + pd.Timedelta(hours=8, minutes=minute),
                    "trip_instance_key": f"{day:%Y%m%d}-{minute}",
                    "anomaly_seconds": float(value),
                    "baseline_delta_delay_seconds": 15.0,
                    "delta_delay_seconds": float(value + 15),
                }
            )
    return pd.DataFrame(rows)


def test_events_overlay_elapsed_time_and_delay_with_independent_colours() -> None:
    frame = _anomalies().sample(frac=1, random_state=4)
    original = frame.copy(deep=True)
    other_segment = frame.assign(segment_id=9, anomaly_seconds=999)
    figure = make_delay_event_plot(
        pd.concat([frame, other_segment]),
        segment_id=8,
        title="Segment 8",
        before_minutes=20,
        after_minutes=30,
    )

    assert len(figure.data) == 2
    assert len({trace.line.color for trace in figure.data}) == 2
    assert len({trace.legendgroup for trace in figure.data}) == 2
    for trace, day in zip(figure.data, ["2026-11-01", "2026-11-02"]):
        assert list(trace.x) == [-20, -10, 0, 10, 20, 30]
        assert list(trace.y) == [0, 30, 120, 60, 20, 0]
        assert trace.marker.symbol[2] == "diamond"
        assert trace.mode == "lines+markers" and trace.connectgaps is False
        assert trace.customdata[2][0] == f"{day} 08:20:00"
        assert trace.customdata[2][2:] == [135, 15]
        assert all(
            str(row[1]).startswith(day.replace("-", "")) for row in trace.customdata
        )
    assert figure.layout.legend.itemclick == "toggle"
    assert figure.layout.legend.itemdoubleclick == "toggleothers"
    assert "minutes" in figure.layout.xaxis.title.text
    assert "delay" in figure.layout.yaxis.title.text
    assert tuple(figure.layout.xaxis.range) == (-20, 30)
    pdt.assert_frame_equal(frame, original)
    assert '"connectgaps":false' in figure.to_json()


def test_date_filters_anchors_without_recomputing_the_full_history_threshold() -> None:
    frame = _anomalies()
    first_day = frame["service_date"].eq(pd.Timestamp("2026-11-01"))
    frame.loc[first_day, ["anomaly_seconds", "delta_delay_seconds"]] += 1000
    with pytest.raises(ValueError, match="No positive delay events"):
        make_delay_event_plot(
            frame,
            segment_id=8,
            title="No events",
            service_date="2026-11-02",
            event_quantile=0.9,
        )
    figure = make_delay_event_plot(
        _anomalies(),
        segment_id=8,
        title="One day",
        service_date="2026-11-02",
    )
    assert len(figure.data) == 1
    assert "2026-11-02" in figure.data[0].name


def test_missing_baselines_and_long_sampling_gaps_break_curves() -> None:
    frame = _anomalies().iloc[:7].copy()
    frame.loc[3, "anomaly_seconds"] = np.nan
    frame.loc[6, "local_time"] += pd.Timedelta(minutes=40)
    figure = make_delay_event_plot(
        frame,
        segment_id=8,
        title="Sparse",
        after_minutes=90,
    )
    trace = figure.data[0]
    assert list(trace.x) == [-20, -10, 0, 10, 20, 30, None, 80]
    assert list(trace.y) == [0, 30, 120, None, 20, 0, None, -10]
    assert trace.connectgaps is False


def test_windows_keep_midnight_traversals_on_their_service_day() -> None:
    frame = _anomalies().iloc[:7].copy()
    frame["local_time"] += pd.Timedelta(hours=15, minutes=30)
    next_day = frame.iloc[[3]].assign(
        service_date=pd.Timestamp("2026-11-02"),
        trip_instance_key="other-service-day",
        anomaly_seconds=50.0,
    )
    figure = make_delay_event_plot(
        pd.concat([frame, next_day]),
        segment_id=8,
        title="Midnight",
        service_date="2026-11-01",
    )
    trace = figure.data[0]
    assert max(trace.x) == 40
    assert trace.customdata[-1][0] == "2026-11-02 00:30:00"
    assert all(row[1] != "other-service-day" for row in trace.customdata)


def test_zero_before_window_and_unobserved_recovery_are_not_zero_padded() -> None:
    frame = _anomalies().iloc[:3]
    figure = make_delay_event_plot(
        frame,
        segment_id=8,
        title="Boundary",
        before_minutes=0,
    )
    assert list(figure.data[0].x) == [0]
    assert list(figure.data[0].y) == [120]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"before_minutes": -1}, "before_minutes"),
        ({"before_minutes": np.nan}, "before_minutes"),
        ({"after_minutes": 0}, "after_minutes"),
        ({"after_minutes": np.inf}, "after_minutes"),
        ({"max_gap_minutes": 0}, "max_gap_minutes"),
        ({"max_gap_minutes": True}, "max_gap_minutes"),
        ({"event_quantile": 1}, "event_quantile"),
        ({"service_date": "NaT"}, "service_date"),
        ({"segment_id": 99}, "No traversals"),
        ({"service_date": "2026-10-01"}, "No positive delay events"),
    ],
)
def test_invalid_controls_raise_explicit_errors(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_delay_event_plot(
            _anomalies(), title="Invalid", **{"segment_id": 8, **kwargs}
        )


@pytest.mark.parametrize("value", [np.nan, 0.0, -10.0])
def test_missing_or_nonpositive_anomalies_cannot_anchor_events(value: float) -> None:
    frame = _anomalies().assign(anomaly_seconds=value)
    with pytest.raises(ValueError, match="No positive delay events"):
        make_delay_event_plot(frame, segment_id=8, title="No events")


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("local_time", pd.NaT, "local_time"),
        ("service_date", pd.NaT, "service_date"),
        ("anomaly_seconds", np.inf, "finite"),
        ("anomaly_seconds", -np.inf, "finite"),
    ],
)
def test_invalid_observations_raise_explicit_errors(column, value, message) -> None:
    frame = _anomalies()
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=message):
        make_delay_event_plot(frame, segment_id=8, title="Invalid observations")


def test_missing_required_columns_raise_explicit_errors() -> None:
    with pytest.raises(ValueError, match="baseline_delta_delay_seconds"):
        make_delay_event_plot(
            _anomalies().drop(columns="baseline_delta_delay_seconds"),
            segment_id=8,
            title="Missing column",
        )
