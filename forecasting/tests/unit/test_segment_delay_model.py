import pickle
from dataclasses import replace

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest
from shapely.geometry import LineString

from forecasting import (
    DelayModelConfig,
    SegmentDelayData,
    make_segment_delay_splits,
    prepare_segment_delay_data,
    run_segment_delay_experiment,
    save_segment_delay_experiment,
    segment_delay_metrics,
    summarize_segment_delay_uplift,
)
from forecasting.road_segment_delay_congestion import DelayCongestionResult
from forecasting.segment_delay_model import (
    FEATURES,
    TARGET,
    CalendarAverage,
    ResidualBands,
)


def _engine_and_schedule():
    schedule = pd.DataFrame(
        {
            "trip_id": ["normal", "normal", "night", "night"],
            "stop_id": ["a", "b", "a", "b"],
            "stop_sequence": [10, 30, 10, 30],
            "arrival_time": ["08:00:00", "08:05:00", "25:00:00", "25:05:00"],
            "departure_time": ["08:00:00", "08:05:00", "25:00:00", "25:05:00"],
        }
    )
    traversals = pd.DataFrame(
        {
            "trip_id": ["normal", "night", "normal", "normal"],
            "trip_instance_key": ["normal|1", "night|1", "normal|2", "normal|3"],
            "trip_start_timestamp": [
                "2026-10-31 08:00",
                "2026-11-02 01:00",
                "2026-11-01 08:00",
                "2026-11-03 08:00",
            ],
            "segment_id": [0, 0, 0, 0],
            "from_stop_id": ["a"] * 4,
            "from_stop_sequence": [10] * 4,
            "to_stop_sequence": [30] * 4,
            "delta_delay_seconds": [-30, 0, 120, 10],
            "available_at": pd.to_datetime(
                [
                    "2026-10-31 15:06Z",
                    "2026-11-02 09:06Z",
                    "2026-11-01 16:06Z",
                    "2026-11-03 16:06Z",
                ]
            ),
            "exclusion_reason": ["included"] * 4,
        }
    )
    segments = pd.DataFrame(
        {"segment_id": [0], "from_stop_name": ["A"], "to_stop_name": ["B"]}
    )
    result = DelayCongestionResult(
        "97-KEL",
        "normal",
        LineString([(0, 0), (1, 0)]),
        segments,
        pd.DataFrame(),
        traversals,
        pd.DataFrame(),
        pd.DataFrame(),
        pd.Timestamp("2026-11-03 23:00Z"),
    )
    return result, schedule


def _data(days=63):
    rows = []
    for day_number, day in enumerate(pd.date_range("2026-08-03", periods=days)):
        for segment in range(3):
            for hour in (7, 8, 12, 16, 17, 20):
                timestamp = (
                    (day + pd.Timedelta(hours=hour))
                    .tz_localize("America/Vancouver")
                    .tz_convert("UTC")
                )
                minute = hour * 60
                angle = 2 * np.pi * minute / 1440
                week_angle = 2 * np.pi * (day.dayofweek + minute / 1440) / 7
                rows.append(
                    {
                        "trip_instance_key": f"{day.date()}|{hour}",
                        "service_date": day,
                        "segment_id": segment,
                        "scheduled_at": timestamp,
                        "available_at": timestamp + pd.Timedelta(minutes=2),
                        "day_of_week": day.dayofweek,
                        "minute_of_day": minute,
                        "time_bin": minute // 30,
                        "time_sin": np.sin(angle),
                        "time_cos": np.cos(angle),
                        "week_sin": np.sin(week_angle),
                        "week_cos": np.cos(week_angle),
                        TARGET: segment * 10
                        + (50 if hour in (8, 17) else -10)
                        + day.dayofweek * 2
                        + day_number % 3
                        - 1,
                    }
                )
    frame = pd.DataFrame(rows)
    coverage = frame.groupby("service_date").size().rename("traversals").reset_index()
    return SegmentDelayData(
        frame,
        pd.DataFrame(),
        coverage,
        pd.DataFrame({"segment_id": [0, 1, 2]}),
        "America/Vancouver",
    )


def _config(**kwargs):
    return DelayModelConfig(
        candidates=({"n_estimators": 30, "max_depth": 3, "learning_rate": 0.2},),
        **kwargs,
    )


def test_scheduled_features_signed_targets_extended_times_dst_and_immutability():
    result, schedule = _engine_and_schedule()
    original = result.traversals.copy(deep=True)
    original_schedule = schedule.copy(deep=True)
    with pytest.warns(UserWarning, match="Excluded 1"):
        data = prepare_segment_delay_data(
            result, schedule, observation_start="2026-10-30T00:00Z"
        )
    assert data.examples[TARGET].tolist() == [-30, 120, 0]
    assert data.examples["service_date"].tolist() == [
        pd.Timestamp("2026-10-31"),
        pd.Timestamp("2026-11-01"),
        pd.Timestamp("2026-11-01"),
    ]
    assert data.examples["scheduled_at"].tolist() == list(
        pd.to_datetime(
            [
                "2026-10-31 15:00Z",
                "2026-11-01 16:00Z",
                "2026-11-02 09:00Z",
            ]
        )
    )
    assert data.examples["minute_of_day"].tolist() == [480, 480, 60]
    pdt.assert_frame_equal(original, result.traversals)
    pdt.assert_frame_equal(original_schedule, schedule)
    changed = result.traversals.assign(
        from_arrival_time="2099-01-01",
        crossed_at="2099-01-02",
        from_arrival_delay=999999,
        to_arrival_delay=-99999,
    )
    with pytest.warns(UserWarning):
        other = prepare_segment_delay_data(
            replace(result, traversals=changed),
            schedule,
            observation_start="2026-10-30T00:00Z",
        )
    pdt.assert_frame_equal(data.examples[FEATURES], other.examples[FEATURES])


def test_schedule_errors_and_modelling_exclusion_audit():
    result, schedule = _engine_and_schedule()
    with pytest.raises(ValueError, match="unique"):
        prepare_segment_delay_data(
            result,
            pd.concat([schedule, schedule.iloc[[0]]]),
            observation_start="2026-10-30T00:00Z",
        )
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_segment_delay_data(
            replace(
                result,
                traversals=pd.concat([result.traversals, result.traversals.iloc[[0]]]),
            ),
            schedule,
            observation_start="2026-10-30T00:00Z",
        )
    changed = result.traversals.astype({TARGET: float}).copy()
    changed.loc[0, TARGET] = np.inf
    changed.loc[1, "trip_start_timestamp"] = "2026-11-02 01:01"
    with pytest.warns(UserWarning):
        data = prepare_segment_delay_data(
            replace(result, traversals=changed),
            schedule,
            observation_start="2026-10-30T00:00Z",
        )
    counts = data.audit.set_index("reason")["count"]
    assert counts["nonfinite_target"] == counts["misaligned_trip_start"] == 1
    assert np.isfinite(data.examples[TARGET]).all()
    with pytest.raises(ValueError, match="divisor"):
        prepare_segment_delay_data(
            result, schedule, observation_start="2026-10-30T00:00Z", time_bin_minutes=17
        )


def test_exact_repeated_stop_sequence_join():
    result, schedule = _engine_and_schedule()
    extra = schedule.iloc[[0]].assign(
        stop_sequence=20, arrival_time="08:02:00", departure_time="08:02:00"
    )
    schedule = pd.concat([schedule, extra])
    changed = result.traversals.copy()
    changed.loc[0, "from_stop_sequence"] = 20
    with pytest.warns(UserWarning):
        data = prepare_segment_delay_data(
            replace(result, traversals=changed),
            schedule,
            observation_start="2026-10-30T00:00Z",
        )
    assert data.examples.iloc[0]["minute_of_day"] == 482


def test_nullable_missing_target_is_audited_not_retained():
    result, schedule = _engine_and_schedule()
    changed = result.traversals.astype({TARGET: "Float64"})
    changed.loc[0, TARGET] = pd.NA
    with pytest.warns(UserWarning):
        data = prepare_segment_delay_data(
            replace(result, traversals=changed),
            schedule,
            observation_start="2026-10-30T00:00Z",
        )
    assert not data.examples[TARGET].isna().any()
    assert data.audit.set_index("reason").loc["nonfinite_target", "count"] == 1


def test_blocked_splits_keep_days_trips_gap_and_available_labels():
    data = _data()
    config = _config()
    splits, summary = make_segment_delay_splits(data, config)
    assert len(splits) == 3
    assert summary["evaluation_days"].eq(7).all()
    assert summary["train_days"].min() >= 21
    for split in splits:
        train = data.examples.loc[split.train_index]
        evaluation = data.examples.loc[split.evaluation_index]
        assert train["available_at"].lt(split.fit_cutoff).all()
        assert train["service_date"].max() < split.start - pd.Timedelta(days=1)
        assert not set(train["trip_instance_key"]) & set(
            evaluation["trip_instance_key"]
        )
        assert not set(train["service_date"]) & set(evaluation["service_date"])
    changed = data.examples.copy()
    key = changed.iloc[0]["trip_instance_key"]
    changed.loc[0, "available_at"] = splits[0].fit_cutoff
    purged, table = make_segment_delay_splits(replace(data, examples=changed), config)
    assert key not in set(changed.loc[purged[0].train_index, "trip_instance_key"])
    assert table.iloc[0]["purged_train_rows"] == 3
    changed = data.examples.copy()
    changed.loc[0, "scheduled_at"] = splits[0].start.tz_localize(data.timezone)
    purged, _ = make_segment_delay_splits(replace(data, examples=changed), config)
    assert key not in set(changed.loc[purged[0].train_index, "trip_instance_key"])


def test_insufficient_or_missing_daily_coverage_fails_explicitly():
    with pytest.raises(ValueError, match="Insufficient coverage"):
        make_segment_delay_splits(_data(30), _config())
    data = _data()
    split = make_segment_delay_splits(data, _config())[0][0]
    missing = data.examples.loc[
        data.examples["service_date"].ne(split.start)
    ].reset_index(drop=True)
    with pytest.raises(ValueError, match="6/7 evaluation days"):
        make_segment_delay_splits(replace(data, examples=missing), _config())
    with pytest.raises(ValueError, match="positive integer"):
        _config(gap_days=0)


def test_arithmetic_calendar_means_support_fallbacks_and_unseen_segments():
    frame = _data().examples
    model = CalendarAverage.fit(frame, frame[TARGET].to_numpy(), _config())
    example = frame.iloc[[0]].copy()
    key = example.iloc[0]
    cohort = frame.loc[
        frame["segment_id"].eq(key.segment_id)
        & frame["time_bin"].eq(key.time_bin)
        & frame["day_of_week"].eq(key.day_of_week),
        TARGET,
    ]
    prediction, level = model.predict(example)
    assert prediction[0] == pytest.approx(cohort.mean())
    assert level[0] == "calendar"
    example["day_of_week"] = 10
    assert model.predict(example)[1][0] == "segment_time"
    example["time_bin"] = 9999
    assert model.predict(example)[1][0] == "segment"
    example["segment_id"] = 9999
    assert model.predict(example)[1][0] == "global"
    assert model.predict(example)[0][0] == pytest.approx(frame[TARGET].mean())
    with pytest.raises(ValueError, match="finite"):
        CalendarAverage.fit(frame, np.full(len(frame), np.nan), _config())


def test_residual_bands_protect_repeated_peaks_and_report_degenerate_support():
    frame = _data().examples
    changed = frame.copy()
    changed.loc[0, TARGET] = 100000
    bands = ResidualBands.fit(changed, _config())
    center, radius, levels = bands.limits(changed, 0.95)
    assert abs(changed[TARGET].iloc[0] - center[0]) > radius[0]
    recurring_peak = frame["minute_of_day"].eq(8 * 60)
    assert (
        np.mean(
            np.abs(frame.loc[recurring_peak, TARGET] - center[recurring_peak])
            <= radius[recurring_peak]
        )
        > 0.95
    )
    assert (levels == "segment").all()
    assert (bands.diagnostics["threshold_seconds"] < 1000).all()
    constant = frame.assign(**{TARGET: 0})
    assert ResidualBands.fit(constant, _config()).diagnostics["degenerate_band"].all()
    unseen = frame.iloc[[0]].assign(segment_id=999)
    assert bands.limits(unseen, 0.95)[2][0] == "global"
    with pytest.raises(ValueError, match="two training"):
        ResidualBands.fit(frame.iloc[:18], _config())


def _predictions():
    return pd.DataFrame(
        {
            TARGET: [-10.0, 0.0, 10.0],
            "segment_id": [0, 0, 1],
            "xgboost": [-8.0, 1.0, 6.0],
            "zero": [0.0, 0.0, 0.0],
            "calendar_mean": [-8.0, 1.0, 6.0],
            "robust_mean_q0.95": [-8.0, 1.0, 6.0],
            "segment_mean_q0.95": [-8.0, 1.0, 6.0],
            "typical_q0.95": [True, True, False],
            "cold_start": [False] * 3,
            "phase": ["test"] * 3,
            "split": ["final_test"] * 3,
            "service_date": pd.to_datetime(["2026-10-01"] * 3),
        }
    )


def test_regression_metric_formulas_cohorts_and_macro_weighting():
    frame = _predictions()
    result = segment_delay_metrics(frame)
    all_xgb = result.loc[
        result["cohort"].eq("all") & result["model"].eq("xgboost")
    ].iloc[0]
    assert all_xgb.mae_seconds == pytest.approx(7 / 3)
    assert all_xgb.median_ae_seconds == 2
    assert all_xgb.rmse_seconds == pytest.approx(np.sqrt(7))
    assert all_xgb.r2 == pytest.approx(1 - 21 / 200)
    assert all_xgb.macro_segment_mae_seconds == pytest.approx((1.5 + 4) / 2)
    tail = result.loc[
        result["cohort"].eq("tail_q0.95") & result["model"].eq("xgboost")
    ].iloc[0]
    assert tail.n == 1 and tail.coverage == pytest.approx(1 / 3)
    assert tail.r2_undefined_reason == "fewer_than_two_outcomes"
    empty = result.loc[result["cohort"].eq("cold_start")].iloc[0]
    assert empty.n == 0 and empty.r2_undefined_reason == "empty_cohort"
    constant = segment_delay_metrics(frame.assign(**{TARGET: 0}))
    assert constant.loc[constant["cohort"].eq("all"), "r2"].isna().all()
    bad = frame.copy()
    bad.loc[0, "xgboost"] = np.inf
    with pytest.raises(ValueError, match="finite"):
        segment_delay_metrics(bad)


def test_paired_uplift_zero_denominator_is_explicit():
    frame = _predictions()
    frame["calendar_mean"] = frame[TARGET]
    segment, daily = summarize_segment_delay_uplift(frame)
    assert segment["improvement_vs_calendar_mean_percent"].isna().all()
    assert (
        daily["improvement_vs_calendar_mean_undefined_reason"]
        .eq("zero_baseline_mae")
        .all()
    )


@pytest.fixture(scope="module")
def fitted_experiment():
    data = _data()
    changed = data.examples.copy()
    changed.loc[2, TARGET] = 100000
    data = replace(data, examples=changed)
    return data, run_segment_delay_experiment(data, _config())


def test_xgboost_experiment_predictions_shared_masks_robust_averages_and_determinism(
    fitted_experiment,
):
    data, experiment = fitted_experiment
    assert (
        experiment.model.pipeline.named_steps["model"].objective == "reg:absoluteerror"
    )
    assert experiment.splits["evaluation_days"].eq(7).all()
    predictions = experiment.predictions
    assert len(predictions) == 21 * 18
    assert set(predictions["split"]) == {"cv_1", "cv_2", "final_test"}
    for _, scores in experiment.metrics.groupby(["split", "cohort"]):
        assert scores["n"].nunique() == 1
    final = predictions.loc[predictions["phase"].eq("test")]
    assert (
        np.abs(final["xgboost"] - final[TARGET]).mean()
        < np.abs(final[TARGET]).mean() * 0.5
    )
    assert not np.array_equal(
        experiment.model.averages["calendar_mean"].tables[-1]["value"],
        experiment.model.averages["robust_mean_q0.95"].tables[-1]["value"],
    )
    pdt.assert_series_equal(
        predictions.set_index(["trip_instance_key", "segment_id"])[TARGET].sort_index(),
        data.examples.set_index(["trip_instance_key", "segment_id"])
        .loc[predictions.set_index(["trip_instance_key", "segment_id"]).index, TARGET]
        .sort_index(),
    )
    test_start = experiment.splits.iloc[-1]["evaluation_start"]
    changed = data.examples.copy()
    changed.loc[changed["service_date"].ge(test_start), TARGET] += 999999
    second = run_segment_delay_experiment(replace(data, examples=changed), _config())
    assert second.selected_candidate == experiment.selected_candidate
    pdt.assert_frame_equal(second.tuning, experiment.tuning)
    np.testing.assert_array_equal(second.predictions["xgboost"], predictions["xgboost"])
    train = data.examples.loc[
        data.examples["service_date"].lt(test_start - pd.Timedelta(days=1))
    ]
    assert (
        experiment.model.pipeline.named_steps["features"].feature_names_in_.tolist()
        == FEATURES
    )
    unseen = train.iloc[[0]].drop(columns=TARGET).assign(segment_id=999)
    assert np.isfinite(experiment.model.predict(unseen)["xgboost"]).all()
    assert experiment.model.predict(unseen)["calendar_mean_level"].iloc[0] == "global"


def test_model_bundle_and_reports_round_trip_non_overwrite(tmp_path, fitted_experiment):
    data, experiment = fitted_experiment
    destination = save_segment_delay_experiment(
        experiment,
        data,
        tmp_path,
        dataset_metadata={"dataset_id": "synthetic"},
        run_id="test_run",
    )
    with (destination / "model_bundle.pkl").open("rb") as stream:
        loaded = pickle.load(stream)
    pdt.assert_frame_equal(
        loaded.predict(data.examples.iloc[:10]),
        experiment.model.predict(data.examples.iloc[:10]),
    )
    assert (destination / "metrics.csv").is_file()
    assert (destination / "manifest.json").is_file()
    with pytest.raises(FileExistsError):
        save_segment_delay_experiment(
            experiment, data, tmp_path, dataset_metadata={}, run_id="test_run"
        )
    with pytest.raises(ValueError, match="safe"):
        save_segment_delay_experiment(
            experiment, data, tmp_path, dataset_metadata={}, run_id="../unsafe"
        )
    with pytest.raises(ValueError, match="safe"):
        save_segment_delay_experiment(
            experiment,
            data,
            tmp_path,
            dataset_metadata={},
            run_id="",
        )
