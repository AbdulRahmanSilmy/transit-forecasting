from datetime import date

import numpy as np
import pandas as pd
import pytest

from forecasting.delay_forecast_display import make_run_figures
from forecasting.delay_forecast_eval import (
    B0,
    B1,
    B2,
    B3,
    StopDwellMedian,
    StudyConfig,
    chain_delay_forecast,
    check_test_access,
    day_bootstrap_delta,
    evaluate_block,
    fold_cutoff,
    prepare_origin_visits,
    run_study,
    score_predictions,
    write_run,
)
from forecasting.traversal_models import (
    CalendarMedianTraversal,
    SegmentMedianTraversal,
    ShuffledTargetTraversal,
    make_traversal_model,
)

SCHEDULE = (0, 300, 600, 900)  # scheduled seconds after first departure


def _journey(
    trip,
    first_departure,
    *,
    traversals=(280, 330, 290),
    dwells=(20, 40),
    origin_delay=0,
    middle_delay=None,
    first_id=0,
):
    """Origin, optional middle and final publications for a 4-stop journey."""
    first_departure = pd.Timestamp(first_departure, tz="UTC")
    scheduled = [first_departure + pd.Timedelta(seconds=s) for s in SCHEDULE]
    actual_arrival, actual_departure = [pd.NaT], [first_departure]
    for k, traversal in enumerate(traversals):
        arrival = actual_departure[-1] + pd.Timedelta(seconds=traversal)
        actual_arrival.append(arrival)
        dwell = dwells[k] if k < len(dwells) else None
        actual_departure.append(
            arrival + pd.Timedelta(seconds=dwell) if dwell is not None else pd.NaT
        )
    origin = first_departure - pd.Timedelta(minutes=150)
    final = actual_arrival[-1] + pd.Timedelta(minutes=1)
    publications = [(origin, origin_delay, None)]
    if middle_delay is not None:
        publications.append((origin + pd.Timedelta(minutes=60), middle_delay, None))
    publications.append((final, None, (actual_arrival, actual_departure)))
    rows = []
    for feed, delay, actual in publications:
        for k in range(4):
            if actual is None:
                arrival = (
                    pd.NaT if k == 0 else scheduled[k] + pd.Timedelta(seconds=delay)
                )
                departure = (
                    pd.NaT if k == 3 else scheduled[k] + pd.Timedelta(seconds=delay)
                )
                arrival_delay = departure_delay = delay
            else:
                arrival, departure = actual[0][k], actual[1][k]
                arrival_delay = (
                    0 if pd.isna(arrival) else (arrival - scheduled[k]).total_seconds()
                )
                departure_delay = (
                    0
                    if pd.isna(departure)
                    else (departure - scheduled[k]).total_seconds()
                )
            first_id += 1
            rows.append(
                {
                    "id": first_id,
                    "feed_timestamp": feed,
                    "trip_id": trip,
                    "trip_schedule_relationship": 0,
                    "trip_route_id": "97-KEL",
                    "trip_direction_id": 0,
                    "stop_sequence": k + 1,
                    "stop_id": f"s{k + 1}",
                    "schedule_relationship": 0,
                    "arrival_delay": arrival_delay,
                    "arrival_time": arrival,
                    "departure_delay": departure_delay,
                    "departure_time": departure,
                    "trip_start_timestamp": str(first_departure),
                }
            )
    return rows


def _updates(days=10, start="2026-10-01"):
    """Three journeys per service day; the 15:00 UTC trip is 60 s slower per link."""
    rows = []
    for day in pd.date_range(start, periods=days, freq="D"):
        for hour, extra in ((15, 60), (19, 0), (22, 0)):
            rows += _journey(
                f"t{hour}",
                day + pd.Timedelta(hours=hour),
                traversals=tuple(t + extra for t in (280, 330, 290)),
                first_id=len(rows),
            )
    frame = pd.DataFrame(rows)
    for column in ("feed_timestamp", "arrival_time", "departure_time"):
        frame[column] = frame[column].dt.tz_localize(None)
    return frame


def _config(**overrides):
    values = dict(
        study="unit",
        route_id="97-KEL",
        direction_id=0,
        timezone="America/Vancouver",
        service_day_start_hour=3,
        dev_dataset_id="unit",
        dev_archive_start="2026-10-01",
        dev_archive_end="2026-10-11",
        dev_end=date(2026, 10, 9),
        fold_starts=(date(2026, 10, 7), date(2026, 10, 8)),
        block_days=1,
        embargo_days=1,
        test_start=date(2026, 10, 12),
        test_end=date(2026, 10, 13),
        test_dataset_id="unit_test",
        test_archive_start="2026-10-12",
        test_archive_end="2026-10-14",
        holidays=(date(2026, 10, 8),),
        bootstrap_reps=200,
        seed=0,
        min_relative_improvement=0.05,
    )
    values.update(overrides)
    return StudyConfig(**values)


def _prepare(updates):
    return prepare_origin_visits(updates, route_id="97-KEL", direction_id=0)


def test_origin_fields_ignore_later_publications():
    updates = pd.DataFrame(
        _journey("a", "2026-10-01 15:00", origin_delay=30, middle_delay=900)
    )
    visits = _prepare(updates).visits.set_index("stop_sequence")
    assert visits.loc[[2, 3, 4], "origin_arrival_delay"].eq(30).all()
    assert visits.loc[1, "anchor_delay_seconds"] == 30
    # Final truth: actual minus schedule (on-time departure, slower/faster links).
    assert visits.loc[2, "truth_delay_seconds"] == -20
    assert visits.loc[3, "truth_delay_seconds"] == 20 + 330 - 300 - 20
    # Scheduled times come from the origin publication, not the middle one.
    expected = pd.Timestamp("2026-10-01 15:05", tz="UTC")
    assert visits.loc[2, "scheduled_arrival"] == expected


def test_chain_with_true_components_reproduces_final_delay():
    prepared = _prepare(pd.DataFrame(_journey("a", "2026-10-01 15:00")))
    links = prepared.links
    dwell = links["upstream_dwell_seconds"].fillna(0).to_numpy()
    chained = chain_delay_forecast(
        prepared.visits, links, links["traversal_seconds"].to_numpy(), dwell
    )
    scored = prepared.visits["score_exclusion_reason"].eq("included")
    np.testing.assert_allclose(
        chained[scored], prepared.visits.loc[scored, "truth_delay_seconds"]
    )
    assert (
        prepared.visits.loc[~scored, "score_exclusion_reason"].eq("anchor_stop").all()
    )


def test_chain_propagates_missing_link_downstream():
    prepared = _prepare(pd.DataFrame(_journey("a", "2026-10-01 15:00")))
    traversal = np.array([280.0, np.nan, 290.0])
    chained = chain_delay_forecast(
        prepared.visits, prepared.links, traversal, np.zeros(3)
    )
    by_stop = chained.groupby(prepared.visits["stop_sequence"]).first()
    assert np.isfinite(by_stop[2]) and np.isnan(by_stop[3]) and np.isnan(by_stop[4])


def test_stale_final_report_is_not_used_as_truth():
    updates = pd.DataFrame(_journey("a", "2026-10-01 15:00"))
    final = updates["feed_timestamp"].eq(updates["feed_timestamp"].max())
    updates = updates.loc[~(final & updates["stop_sequence"].eq(4))]
    prepared = _prepare(updates)
    # Stop 4's latest report is now its 150-minute-old origin prediction.
    reason = prepared.visits.set_index("stop_sequence")["score_exclusion_reason"]
    assert reason[4] == "stale_final_report"
    assert reason[3] == "included"
    links = prepared.links.set_index("stop_sequence")["train_exclusion_reason"]
    assert links[4] == "stale_final_report"


def test_arrivals_after_the_last_publication_are_unfinished():
    updates = pd.DataFrame(_journey("a", "2026-10-01 15:00", middle_delay=60))
    updates = updates.loc[updates["feed_timestamp"].lt(updates["feed_timestamp"].max())]
    prepared = _prepare(updates)
    reasons = prepared.visits.set_index("stop_sequence")["score_exclusion_reason"]
    assert reasons.loc[[2, 3, 4]].eq("unfinished_arrival").all()
    assert "unfinished_arrival" in set(prepared.audit["reason"])


def test_evaluate_block_trains_only_on_outcomes_before_cutoff():
    config = _config()
    prepared = _prepare(_updates())
    seen = []

    class Recorder(SegmentMedianTraversal):
        def fit(self, links):
            seen.append(links)
            return super().fit(links)

    rows = evaluate_block(
        prepared,
        model_name="calendar_median",
        block_start=date(2026, 10, 7),
        block_days=1,
        config=config,
        fold="f",
        model_factory=lambda name, tz: Recorder(),
    )
    cutoff = fold_cutoff(date(2026, 10, 7), config)
    assert cutoff == pd.Timestamp("2026-10-06 10:00", tz="UTC")
    assert all(links["available_at"].max() < cutoff for links in seen)
    assert not set(rows["trip_instance_key"]) & set(seen[0]["trip_instance_key"])
    assert rows["service_day"].eq(pd.Timestamp("2026-10-07")).all()
    assert {f"pred_{m}" for m in (B0, B1, B2, B3)} <= set(rows.columns)


def test_calendar_median_learns_slow_departure_hour():
    prepared = _prepare(_updates())
    links = prepared.links
    model = CalendarMedianTraversal(min_count=3).fit(links)
    prediction = model.predict(links)
    hours = links["scheduled_departure"].dt.hour.to_numpy()
    slow = prediction[hours == 15].mean() - prediction[hours == 19].mean()
    assert slow == pytest.approx(60)


def test_calendar_median_falls_back_for_unseen_cells():
    links = _prepare(_updates(days=2)).links
    model = CalendarMedianTraversal(min_count=100).fit(links)
    np.testing.assert_allclose(
        model.predict(links), SegmentMedianTraversal().fit(links).predict(links)
    )
    unseen = links.head(1).assign(segment_key="x->y")
    assert np.isnan(model.predict(unseen)).all()


def test_shuffled_control_keeps_segment_values():
    links = _prepare(_updates(days=3)).links
    control = ShuffledTargetTraversal(SegmentMedianTraversal(), seed=1).fit(links)
    np.testing.assert_allclose(
        control.predict(links), SegmentMedianTraversal().fit(links).predict(links)
    )


def test_dwell_rule_uses_stop_medians_and_zero_at_first_stop():
    links = _prepare(_updates(days=2)).links
    dwell = StopDwellMedian().fit(links).predict(links)
    by_stop = pd.Series(dwell).groupby(links["from_stop_id"].to_numpy()).first()
    assert by_stop["s1"] == 0 and by_stop["s2"] == 20 and by_stop["s3"] == 40


def test_score_uses_identical_rows_and_counts_unscorable():
    rows = pd.DataFrame(
        {
            "service_day": pd.to_datetime(["2026-10-01"] * 2 + ["2026-10-02"] * 2),
            "fold": "f",
            "stop_sequence": [2, 3, 2, 3],
            "lead_minutes": [130.0, 140.0, 130.0, 140.0],
            "scheduled_arrival": pd.to_datetime(["2026-10-01 15:00Z"] * 4),
            "truth_delay_seconds": [10.0, 20.0, 30.0, 40.0],
            f"pred_{B0}": [0.0, 0.0, 0.0, 0.0],
            f"pred_{B1}": [0.0, 0.0, 0.0, 0.0],
            f"pred_{B2}": [10.0, 20.0, np.nan, 40.0],
            f"pred_{B3}": [10.0, 20.0, 30.0, 40.0],
        }
    )
    metrics = score_predictions(rows, config=_config(holidays=()), candidate=None)
    coverage = metrics["coverage"].set_index("method")["unscorable_rows"]
    assert coverage[B2] == 1 and coverage[B0] == 0
    assert metrics["primary_rows"] == 3
    summary = metrics["summary"].set_index("method")["MAE_s"]
    assert summary[B0] == pytest.approx((10 + 20 + 40) / 3)
    assert summary[B3] == 0


def test_day_bootstrap_ci_contains_point_estimate():
    rng = np.random.default_rng(0)
    rows = pd.DataFrame(
        {
            "service_day": np.repeat(pd.date_range("2026-10-01", periods=20), 10),
            "truth_delay_seconds": rng.normal(0, 60, 200),
        }
    )
    rows["pred_a"] = rows["truth_delay_seconds"] + rng.normal(0, 30, 200)
    rows["pred_b"] = rows["truth_delay_seconds"] + rng.normal(0, 60, 200)
    point, low, high = day_bootstrap_delta(rows, "a", "b", reps=500, seed=0)
    assert low <= point <= high and high < 0


def test_locked_test_access_is_refused():
    config = _config()
    with pytest.raises(PermissionError, match="approval"):
        check_test_access(config, approved=False, today=date(2027, 1, 1))
    with pytest.raises(PermissionError, match="fully"):
        check_test_access(config, approved=True, today=date(2026, 10, 14))
    check_test_access(config, approved=True, today=date(2026, 10, 15))
    with pytest.raises(PermissionError):
        run_study(config, _updates(), model_name="harmonic_huber", split="test")


def test_config_rejects_folds_reaching_the_locked_test():
    with pytest.raises(ValueError, match="dev_end"):
        _config(fold_starts=(date(2026, 10, 9),), block_days=2)
    with pytest.raises(ValueError, match="locked test"):
        _config(test_start=date(2026, 10, 10))


def test_run_study_dev_end_to_end_with_control_and_criterion():
    run = run_study(
        _config(), _updates(days=14), model_name="harmonic_huber", split="dev"
    )
    metrics = run.metrics
    assert run.predictions["service_day"].max() <= pd.Timestamp("2026-10-09")
    assert set(run.predictions["fold"]) == {"dev1", "dev2"}
    assert metrics["holiday_rows"] > 0  # 2026-10-08 is scored separately
    assert metrics["control"] is not None and metrics["criterion"] is not None
    summary = metrics["summary"].set_index("method")["MAE_s"]
    # Tiny segments fall back to medians, so the candidate equals B2.
    assert summary["harmonic_huber"] == pytest.approx(summary[B2])
    assert summary[B3] < summary[B2]


def test_run_figures_cover_methods_and_are_written(tmp_path):
    run = run_study(
        _config(), _updates(days=14), model_name="harmonic_huber", split="dev"
    )
    figures = make_run_figures(
        run.predictions, candidate="harmonic_huber", timezone="America/Vancouver"
    )
    assert set(figures) == {
        "daily_error",
        "hourly_error",
        "actual_vs_predicted",
        "timeseries_stop_2",
        "timeseries_stop_4",
    }
    scatter_names = {trace.name for trace in figures["actual_vs_predicted"].data}
    assert "Transit BC earliest (B0)" in scatter_names
    assert "Candidate: harmonic_huber" in scatter_names
    observed = [
        t for t in figures["timeseries_stop_4"].data if t.name.startswith("Obs")
    ]
    assert len(observed) == 1 and len(observed[0].y) > 0
    write_run(run, tmp_path / "run", provenance={}, figures=figures)
    written = {path.name for path in (tmp_path / "run" / "figures").iterdir()}
    assert {"daily_error.html", "plotly.min.js"} <= written
    with pytest.raises(FileExistsError):
        write_run(run, tmp_path / "run", provenance={})


def test_unknown_model_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown"):
        make_traversal_model("nope", "UTC")
