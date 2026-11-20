import pandas as pd
import pytest
from test_delay_forecast_eval import _config, _journey, _updates

from forecasting.delay_forecast_eval import prepare_origin_visits, run_study
from forecasting.timestamp_correction import (
    correct_timestamp_shifts,
    schedule_clock_agreement,
)


def _shift_rows(frame, start, hours):
    """Simulate the storage defect: rows written from ``start`` are early."""
    shifted = frame.copy()
    columns = ("feed_timestamp", "arrival_time", "departure_time")
    for column in columns:
        shifted[column] = pd.to_datetime(shifted[column], utc=True)
    rows = shifted["feed_timestamp"].ge(pd.Timestamp(start, tz="UTC"))
    for column in columns:
        shifted.loc[rows, column] = shifted.loc[rows, column] - pd.Timedelta(
            hours=hours
        )
    return shifted


def test_correction_shifts_only_rows_written_inside_window():
    frame = pd.DataFrame(
        {
            "feed_timestamp": pd.to_datetime(["2026-11-01 10:00", "2026-11-02 10:00"]),
            "arrival_time": pd.to_datetime(["2026-11-01 12:00", pd.NaT]),
        }
    )
    corrected = correct_timestamp_shifts(
        frame, [{"from_stored": "2026-11-02 08:00", "to_stored": None, "add_hours": 1}]
    )
    assert corrected["feed_timestamp"].tolist() == [
        pd.Timestamp("2026-11-01 10:00", tz="UTC"),
        pd.Timestamp("2026-11-02 11:00", tz="UTC"),
    ]
    assert corrected["arrival_time"].iloc[0] == pd.Timestamp(
        "2026-11-01 12:00", tz="UTC"
    )
    assert pd.isna(corrected["arrival_time"].iloc[1])
    assert frame["feed_timestamp"].iloc[1] == pd.Timestamp("2026-11-02 10:00")


def test_overlapping_windows_are_rejected():
    frame = pd.DataFrame({"feed_timestamp": pd.to_datetime(["2026-11-02 10:00"])})
    windows = [
        {"from_stored": "2026-11-01", "to_stored": None, "add_hours": 1},
        {"from_stored": "2026-11-02", "to_stored": None, "add_hours": 1},
    ]
    with pytest.raises(ValueError, match="overlap"):
        correct_timestamp_shifts(frame, windows)


def test_shifted_journeys_disagree_with_descriptor_and_are_excluded():
    updates = pd.DataFrame(
        _journey("ok", "2026-11-03 15:00")
        + _journey("bad", "2026-11-03 18:00", first_id=100)
    )
    bad = updates["trip_id"].eq("bad")
    updates.loc[bad] = _shift_rows(updates.loc[bad], "2026-01-01", 1)
    visits = prepare_origin_visits(updates, route_id="97-KEL", direction_id=0).visits
    reasons = visits.groupby("trip_id")["score_exclusion_reason"].agg(set)
    assert "schedule_clock_mismatch" in reasons["bad"]
    assert "schedule_clock_mismatch" not in reasons["ok"]
    agreement = schedule_clock_agreement(visits)
    assert agreement["agreement"].iloc[0] == pytest.approx(0.5)
    assert agreement["median_difference_minutes"].iloc[0] == pytest.approx(-30)


def test_run_study_corrects_configured_shift_and_refuses_uncorrected_data():
    shifted = _shift_rows(_updates(days=14), "2026-10-05", 1)
    with pytest.raises(ValueError, match="timestamp_shifts"):
        run_study(_config(), shifted, model_name="calendar_median", split="dev")
    window = ({"from_stored": "2026-10-05", "to_stored": None, "add_hours": 1},)
    run = run_study(
        _config(timestamp_shifts=window),
        shifted,
        model_name="calendar_median",
        split="dev",
    )
    clock = run.metrics["schedule_clock"]
    assert clock["agreement"].min() == pytest.approx(1.0)
