import pandas as pd
import pytest
from test_delay_forecast_eval import _config, _journey, _updates

from forecasting.delay_forecast_eval import prepare_origin_visits, run_study
from forecasting.feed_clock import journey_timetable_offsets, timetable_offset_summary


def test_timezone_database_knows_bc_permanent_utc_minus_7():
    # BC stopped changing clocks after 2026-03-08 (issue #13). A stale tz
    # database falls back to UTC-8 on 2026-11-01 and shifts local times 1 h.
    local = pd.Timestamp("2026-11-03 12:00Z").tz_convert("America/Vancouver")
    assert local.utcoffset() == pd.Timedelta(
        hours=-7
    ), "Stale timezone database: update tzdata in transit_env"


def _relabel(frame, hours, trips=None):
    """Simulate a journey whose start descriptor is ``hours`` off its times."""
    relabelled = frame.copy()
    rows = relabelled["trip_id"].isin(trips) if trips is not None else slice(None)
    starts = pd.to_datetime(relabelled.loc[rows, "trip_start_timestamp"])
    relabelled.loc[rows, "trip_start_timestamp"] = (
        starts + pd.Timedelta(hours=hours)
    ).astype(str)
    return relabelled


def test_offsets_are_whole_hours_or_flagged():
    scheduled = pd.Series(
        pd.to_datetime(
            [
                "2026-11-03 15:00Z",
                "2026-11-03 15:00Z",
                "2026-11-03 15:00Z",
                "2026-11-03 15:00Z",
            ]
        )
    )
    descriptor = pd.Series(
        pd.to_datetime(
            ["2026-11-03 08:00", "2026-11-03 09:00", "2026-11-03 08:37", pd.NaT]
        )
    )
    offset, mismatch = journey_timetable_offsets(scheduled, descriptor)
    assert offset.iloc[0] == 0 and offset.iloc[1] == 60
    assert pd.isna(offset.iloc[2]) and bool(mismatch.iloc[2])
    assert pd.isna(offset.iloc[3]) and not bool(mismatch.iloc[3])


def test_relabelled_journey_keeps_event_times_and_uses_timetable_clock():
    updates = pd.DataFrame(
        _journey("ok", "2026-11-03 15:00")
        + _journey("late_label", "2026-11-03 18:00", first_id=100)
    )
    relabelled = _relabel(updates, 1, trips=["late_label"])
    visits = prepare_origin_visits(relabelled, route_id="97-KEL", direction_id=0).visits
    by_trip = visits.set_index(["trip_id", "stop_sequence"])
    assert by_trip.loc[("late_label", 1), "timetable_offset_minutes"] == 60
    assert by_trip.loc[("ok", 1), "timetable_offset_minutes"] == 0
    shifted = by_trip.loc[("late_label", 2)]
    assert shifted["calendar_arrival"] - shifted["scheduled_arrival"] == pd.Timedelta(
        hours=1
    )
    # Event times, truth and exclusions are unaffected by the label offset.
    reference = prepare_origin_visits(updates, route_id="97-KEL", direction_id=0)
    reference = reference.visits.set_index(["trip_id", "stop_sequence"])
    for column in ("final_arrival_time", "truth_delay_seconds", "scheduled_arrival"):
        pd.testing.assert_series_equal(
            by_trip[column], reference[column], check_names=False
        )
    assert by_trip["score_exclusion_reason"].isin(["included", "anchor_stop"]).all()


def test_feed_calendar_clock_ignores_the_offset():
    relabelled = _relabel(pd.DataFrame(_journey("a", "2026-11-03 18:00")), 1)
    visits = prepare_origin_visits(
        relabelled, route_id="97-KEL", direction_id=0, calendar_clock="feed"
    ).visits
    pd.testing.assert_series_equal(
        visits["calendar_arrival"], visits["scheduled_arrival"], check_names=False
    )
    assert visits["timetable_offset_minutes"].eq(60).all()


def test_non_whole_hour_offsets_are_excluded_and_summarized():
    updates = pd.DataFrame(
        _journey("ok", "2026-11-03 15:00")
        + _journey("odd", "2026-11-03 18:00", first_id=100)
    )
    odd = _relabel(updates, 37 / 60, trips=["odd"])
    visits = prepare_origin_visits(odd, route_id="97-KEL", direction_id=0).visits
    reasons = visits.groupby("trip_id")["score_exclusion_reason"].agg(set)
    assert "schedule_clock_mismatch" in reasons["odd"]
    assert "schedule_clock_mismatch" not in reasons["ok"]
    summary = timetable_offset_summary(visits)
    assert summary["share_mismatch"].iloc[0] == pytest.approx(0.5)
    assert summary["share_offset_0"].iloc[0] == pytest.approx(0.5)


def test_run_study_reports_offsets_and_refuses_widespread_mismatch():
    updates = _updates(days=14)
    later = updates["trip_start_timestamp"].ge("2026-10-05")
    relabelled = updates.copy()
    relabelled.loc[later] = _relabel(updates.loc[later], 1)
    run = run_study(_config(), relabelled, model_name="calendar_median", split="dev")
    clock = run.metrics["schedule_clock"].set_index("service_month")
    assert clock.loc["2026-10", "share_offset_60"] > 0.5
    with pytest.raises(ValueError, match="whole"):
        run_study(
            _config(),
            _relabel(updates, 0.5),
            model_name="calendar_median",
            split="dev",
        )
