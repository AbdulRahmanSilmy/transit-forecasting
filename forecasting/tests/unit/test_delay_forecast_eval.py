from datetime import date

import numpy as np
import pandas as pd
import pytest

from forecasting.anchor_models import CalendarMedianAnchor, make_anchor_model
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
    first_stop_departures,
    fold_cutoff,
    log_row,
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
    departure_lag=0,
):
    """Origin, optional middle and final publications for a 4-stop journey.

    ``departure_lag`` makes the bus leave the first stop that many seconds
    late while the origin publication still reports ``origin_delay``.
    """
    first_departure = pd.Timestamp(first_departure, tz="UTC")
    scheduled = [first_departure + pd.Timedelta(seconds=s) for s in SCHEDULE]
    actual_arrival = [pd.NaT]
    actual_departure = [first_departure + pd.Timedelta(seconds=departure_lag)]
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
                    # The agency descriptor is local wall time, like the real feed.
                    "trip_start_timestamp": str(
                        first_departure.tz_convert("America/Vancouver").tz_localize(
                            None
                        )
                    ),
                }
            )
    return rows


def _updates(days=10, start="2026-10-01", departure_lags=None):
    """Three journeys per service day; the 15:00 UTC trip is 60 s slower per link.

    ``departure_lags`` optionally maps a UTC hour to its first-stop departure lag.
    """
    rows = []
    lags = departure_lags or {}
    for day in pd.date_range(start, periods=days, freq="D"):
        for hour, extra in ((15, 60), (19, 0), (22, 0)):
            rows += _journey(
                f"t{hour}",
                day + pd.Timedelta(hours=hour),
                traversals=tuple(t + extra for t in (280, 330, 290)),
                first_id=len(rows),
                departure_lag=lags.get(hour, 0),
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


def test_stale_first_stop_departure_still_trains_the_first_link():
    updates = pd.DataFrame(_journey("a", "2026-10-01 15:00"))
    final = updates["feed_timestamp"].eq(updates["feed_timestamp"].max())
    # Stop 1's latest report is now its origin estimate: an on-time departure.
    prepared = _prepare(updates.loc[~(final & updates["stop_sequence"].eq(1))])
    links = prepared.links.set_index("stop_sequence")
    assert links.loc[2, "train_exclusion_reason"] == "included"
    assert links.loc[2, "traversal_seconds"] == 280
    # A stale departure at any later stop still excludes its downstream link.
    prepared = _prepare(updates.loc[~(final & updates["stop_sequence"].eq(2))])
    reasons = prepared.links.set_index("stop_sequence")["train_exclusion_reason"]
    assert reasons[2] == "stale_final_report"
    assert reasons[3] == "stale_final_report"


def test_journey_with_every_final_delay_zero_is_untracked():
    # Journey "a" runs exactly to schedule, as a schedule-only feed reports it.
    untracked = _journey(
        "a", "2026-10-01 15:00", traversals=(300, 300, 300), dwells=(0, 0)
    )
    tracked = _journey("b", "2026-10-01 19:00", first_id=len(untracked))
    prepared = _prepare(pd.DataFrame(untracked + tracked))
    visits = prepared.visits
    reasons = visits.set_index(["trip_id", "stop_sequence"])["score_exclusion_reason"]
    assert reasons.loc["a"].loc[[2, 3, 4]].eq("untracked_journey").all()
    assert reasons.loc["b"].loc[[2, 3, 4]].eq("included").all()
    links = prepared.links
    is_a = links["trip_instance_key"].str.startswith("a|")
    assert links.loc[is_a, "train_exclusion_reason"].eq("untracked_journey").all()
    assert links.loc[~is_a, "train_exclusion_reason"].eq("included").all()
    audit = prepared.audit.set_index(["stage", "reason"])["count"]
    assert audit[("scored_visits", "untracked_journey")] == 3
    assert audit[("training_links", "untracked_journey")] == 3


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

    rows, links = evaluate_block(
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
    assert {f"trav_{m}" for m in (B0, B2, B3)} <= set(links.columns)
    assert links["trip_instance_key"].isin(rows["trip_instance_key"]).all()
    # Transit BC's implied traversal is the timetable run time (300 s here,
    # with zero scheduled dwell), and our dwell rule never applies at stop 1.
    assert links[f"trav_{B0}"].eq(300).all()
    assert links.loc[links["upstream_is_first_stop"], "dwell_model"].eq(0).all()


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


def test_anchor_free_score_removes_only_the_anchor_error():
    # Buses leave 90 s late; the chain starts from the feed's 0 s anchor.
    truth = [100.0, 150.0, 120.0, 130.0, 110.0]
    rows = pd.DataFrame(
        {
            "service_day": pd.to_datetime(["2026-10-01"] * 3 + ["2026-10-02"] * 2),
            "fold": "f",
            "stop_sequence": [2, 3, 2, 3, 2],
            "lead_minutes": 130.0,
            "scheduled_arrival": pd.to_datetime(["2026-10-01 15:00Z"] * 5),
            "truth_delay_seconds": truth,
            f"pred_{B0}": 0.0,
            f"pred_{B1}": 0.0,
            # Right about everything after departure, wrong by the anchor only.
            f"pred_{B2}": [value - 90 for value in truth],
            f"pred_{B3}": [value - 80 for value in truth],
            "actual_departure_delay_s": [90.0, 90.0, 90.0, 90.0, np.nan],
            "chain_anchor_delay_s": 0.0,
        }
    )
    metrics = score_predictions(rows, config=_config(holidays=()), candidate=None)
    anchor_free = metrics["anchor_free"]
    assert anchor_free["rows"] == 4 and anchor_free["excluded_rows"] == 1
    summary = anchor_free["summary"].set_index("method")
    assert B0 not in summary.index
    assert summary.loc[B2, "MAE_s"] == pytest.approx(0)
    assert summary.loc[B3, "bias_s"] == pytest.approx(10)
    # B1 from the actual departure: delay stays at 90 s.
    expected_b1 = np.mean([abs(90 - value) for value in truth[:4]])
    assert summary.loc[B1, "MAE_s"] == pytest.approx(expected_b1)
    paired = anchor_free["paired_vs_B3"].set_index("method")
    assert paired.loc[B2, "delta_MAE_s"] == pytest.approx(-10)
    # The primary score is unchanged by the secondary one.
    assert metrics["summary"].set_index("method").loc[B2, "MAE_s"] == pytest.approx(90)


def test_actual_departure_uses_the_reported_delay_like_truth():
    config = _config()
    updates = _updates(days=10, departure_lags={15: 120, 19: 30, 22: 30})
    # One journey's final stop-1 report carries a second clock: its time is
    # 1 h early while its reported delay is the real one (Nov 2 restart case).
    final = updates.groupby(["trip_id", "trip_start_timestamp"])[
        "feed_timestamp"
    ].transform("max")
    target = (
        updates["trip_id"].eq("t19")
        & updates["trip_start_timestamp"].str.startswith("2026-10-08")
        & updates["stop_sequence"].eq(1)
        & updates["feed_timestamp"].eq(final)
    )
    assert target.sum() == 1
    updates.loc[target, "departure_time"] -= pd.Timedelta(hours=1)
    prepared = _prepare(updates)
    rows, _ = evaluate_block(
        prepared,
        model_name="segment_median",
        block_start=date(2026, 10, 8),
        block_days=1,
        config=config,
        fold="f",
    )
    departures = rows.groupby("trip_instance_key")["actual_departure_delay_s"].first()
    assert sorted(departures.round().tolist()) == [30.0, 30.0, 120.0]
    assert rows["chain_anchor_delay_s"].eq(0).all()


def test_run_study_reports_anchor_free_scores_and_log_row():
    run = run_study(
        _config(),
        _updates(days=14, departure_lags={15: 120, 19: 30, 22: 30}),
        model_name="harmonic_huber",
        split="dev",
    )
    anchor_free = run.metrics["anchor_free"]
    assert anchor_free["rows"] == run.metrics["primary_rows"]
    assert anchor_free["excluded_rows"] == 0
    scored = run.metrics["summary"].set_index("method")["MAE_s"]
    free = anchor_free["summary"].set_index("method")["MAE_s"]
    assert free["harmonic_huber"] < scored["harmonic_huber"]
    assert set(anchor_free["paired_vs_B3"]["method"]) >= {"harmonic_huber", B2}
    assert "anchor-free" in log_row(run, run_id="r", git_hash="abc")


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
        run.predictions,
        candidate="harmonic_huber",
        timezone="America/Vancouver",
        traversals=run.traversals,
    )
    assert set(figures) == {
        "daily_error",
        "hourly_error",
        "actual_vs_predicted",
        "timeseries_stop_2",
        "timeseries_stop_4",
        "segment_error_overview",
        *(
            f"segment_{seq:02d}_s{seq - 1}-s{seq}_{kind}"
            for seq in (2, 3, 4)
            for kind in ("timeseries", "traversal_scatter")
        ),
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


def test_traversal_scores_match_known_errors():
    run = run_study(
        _config(), _updates(days=14), model_name="harmonic_huber", split="dev"
    )
    traversal = run.metrics["traversal"]
    summary = traversal["summary"].set_index("method")
    links = run.traversals.loc[
        ~run.traversals["service_day"].isin([pd.Timestamp("2026-10-08")])
    ]
    expected = (links[f"trav_{B0}"] - links["actual_traversal_s"]).abs().mean()
    assert summary.loc[B0, "MAE_s"] == pytest.approx(expected)
    # The slow 15:00 UTC trips make segment medians wrong; calendar medians are not.
    assert summary.loc[B3, "MAE_s"] < summary.loc[B2, "MAE_s"]
    segments = traversal["by_segment"]
    assert segments["segment"].tolist() == [
        "seq 2: s1 -> s2",
        "seq 3: s2 -> s3",
        "seq 4: s3 -> s4",
    ]
    dwell = traversal["dwell_summary"].set_index("rule")
    assert dwell.loc["model", "MAE_s"] == pytest.approx(0)
    assert dwell.loc[B0, "MAE_s"] == pytest.approx(30)  # scheduled 0 vs 20/40 s


def test_first_stop_departures_measure_lag_and_drop_stale_reports():
    updates = pd.DataFrame(_journey("a", "2026-10-01 15:00", departure_lag=90))
    departures = first_stop_departures(_prepare(updates).visits, 10)
    assert departures["departure_delay_seconds"].tolist() == [90.0]
    # Drop stop 1 from the final publication: its last report is now the
    # 150-minute-old origin prediction, which is stale.
    final = updates["feed_timestamp"].eq(updates["feed_timestamp"].max())
    stale = updates.loc[~(final & updates["stop_sequence"].eq(1))]
    assert first_stop_departures(_prepare(stale).visits, 10).empty


def test_first_stop_departures_skip_untracked_journeys():
    # Journey "a" reports exactly 0 s everywhere (#21); its 0 s departure is
    # not an observation and must not train the anchor.
    untracked = _journey(
        "a", "2026-10-01 15:00", traversals=(300, 300, 300), dwells=(0, 0)
    )
    tracked = _journey("b", "2026-10-01 19:00", departure_lag=90, first_id=1000)
    departures = first_stop_departures(
        _prepare(pd.DataFrame(untracked + tracked)).visits, 10
    )
    assert departures["trip_instance_key"].str.startswith("b|").all()
    assert departures["departure_delay_seconds"].tolist() == [90.0]


def test_learned_anchor_shifts_chained_methods_only():
    config = _config()
    prepared = _prepare(_updates(departure_lags={15: 120, 19: 30, 22: 30}))
    kwargs = dict(block_start=date(2026, 10, 7), block_days=1, config=config, fold="f")
    feed_rows, _ = evaluate_block(prepared, model_name="calendar_median", **kwargs)
    learned_rows, _ = evaluate_block(
        prepared, model_name="calendar_median", anchor_name="calendar_median", **kwargs
    )
    # B0 and B1 always use Transit BC's feed; the feed said 0 s at the origin.
    for method in (B0, B1):
        pd.testing.assert_series_equal(
            feed_rows[f"pred_{method}"], learned_rows[f"pred_{method}"]
        )
    assert feed_rows[f"pred_{B1}"].eq(0).all()
    # The calendar anchor learns each hour's lag (pooled fallback for thin cells)
    # and shifts every chained forecast of the journey by exactly that amount.
    shift = learned_rows["anchor_model_s"]
    assert set(shift.round()) <= {120.0, 30.0}
    for method in (B2, B3):
        np.testing.assert_allclose(
            learned_rows[f"pred_{method}"] - feed_rows[f"pred_{method}"], shift
        )

    def b3_mae(rows):
        return (rows[f"pred_{B3}"] - rows["truth_delay_seconds"]).abs().mean()

    assert b3_mae(learned_rows) < b3_mae(feed_rows)


def test_learned_anchor_trains_only_on_departures_before_cutoff():
    config = _config()
    prepared = _prepare(_updates(departure_lags={15: 120}))
    seen = []

    class Recorder(CalendarMedianAnchor):
        def fit(self, departures):
            seen.append(departures)
            return super().fit(departures)

    rows, _ = evaluate_block(
        prepared,
        model_name="calendar_median",
        block_start=date(2026, 10, 7),
        block_days=1,
        config=config,
        fold="f",
        anchor_name="calendar_median",
        anchor_factory=lambda name, tz: Recorder(timezone=tz, min_count=1),
    )
    cutoff = fold_cutoff(date(2026, 10, 7), config)
    assert seen[0]["available_at"].max() < cutoff
    assert not set(rows["trip_instance_key"]) & set(seen[0]["trip_instance_key"])


def test_run_study_records_anchor_and_log_row_names_it():
    config = _config()
    run = run_study(
        config,
        _updates(departure_lags={15: 120}),
        model_name="calendar_median",
        split="dev",
        anchor="calendar_median",
    )
    assert run.info["anchor"] == "calendar_median"
    assert "calendar_median anchor" in log_row(run, run_id="r", git_hash="abc")
    default = run_study(config, _updates(), model_name="calendar_median", split="dev")
    assert default.info["anchor"] == "feed"
    assert "anchor_model_s" not in default.predictions


def test_calendar_anchor_learns_hour_and_falls_back_to_pooled():
    times = pd.to_datetime(
        ["2026-10-05 15:00", "2026-10-06 15:00", "2026-10-05 22:00"], utc=True
    )
    departures = pd.DataFrame(
        {
            "stop_id": ["s1", "s1", "s1"],
            "calendar_departure": times,
            "departure_delay_seconds": [100.0, 120.0, 20.0],
        }
    )
    model = CalendarMedianAnchor(min_count=1).fit(departures)
    requests = pd.DataFrame(
        {
            "stop_id": ["s1", "s1", "other"],
            "calendar_departure": pd.to_datetime(
                ["2026-10-14 15:00", "2026-10-14 22:00", "2026-10-14 15:00"], utc=True
            ),
        }
    )
    # Wednesday has no weekday cell: stop x hour (110 s), then pooled for "other".
    np.testing.assert_allclose(model.predict(requests), [110.0, 20.0, 100.0])
    with pytest.raises(ValueError):
        make_anchor_model("nope", "America/Vancouver")
