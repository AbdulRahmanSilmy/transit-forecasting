import pickle

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest
from sklearn.base import clone
from sklearn.exceptions import ConvergenceWarning, NotFittedError
from sklearn.linear_model import Lasso
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from forecasting import (
    CalendarHarmonics,
    HuberLassoRegressor,
    make_harmonic_time_splits,
    save_harmonic_experiment,
)


def test_harmonics_repeat_across_weeks_dst_and_preserve_input():
    X = pd.DataFrame(
        {
            "upstream_departure_time": pd.to_datetime(
                [
                    "2026-10-25 08:30Z",
                    "2026-11-01 08:30Z",
                    "2026-11-01 09:30Z",
                ]
            ),
            "ignored_target": [1, 1000, 3000],
        }
    )
    before = X.copy(deep=True)
    transformer = CalendarHarmonics(daily_order=2, weekly_order=1)
    features = transformer.fit_transform(X)
    np.testing.assert_allclose(features[0], features[1], atol=1e-14)
    np.testing.assert_allclose(features[1], features[2], atol=1e-14)
    assert features.shape == (3, 14)
    np.testing.assert_allclose(
        features[:, 6:],
        np.column_stack(
            [features[:, d] * features[:, w] for d in range(4) for w in range(4, 6)]
        ),
    )
    pdt.assert_frame_equal(X, before)
    clone(transformer).fit(X)


@pytest.mark.parametrize("order", [(0, 1), (2, 0)])
def test_daily_or_weekly_only_features(order):
    X = pd.DataFrame(
        {
            "upstream_departure_time": pd.date_range("2026-09-01", periods=2, tz="UTC"),
        }
    )
    features = CalendarHarmonics(
        daily_order=order[0], weekly_order=order[1]
    ).fit_transform(X)
    assert features.shape == (2, 2 * sum(order))


@pytest.mark.parametrize(
    "params",
    [
        {"daily_order": -1},
        {"weekly_order": 4},
        {"daily_order": True},
        {"daily_order": 0, "weekly_order": 0},
    ],
)
def test_invalid_harmonic_orders_raise(params):
    X = pd.DataFrame(
        {
            "upstream_departure_time": pd.date_range("2026-09-01", periods=2, tz="UTC"),
        }
    )
    with pytest.raises(ValueError):
        CalendarHarmonics(**params).fit(X)


@pytest.mark.parametrize(
    "timestamps",
    [
        pd.to_datetime(["2026-09-01"]),
        pd.to_datetime(["2026-09-01", None], utc=True),
    ],
)
def test_harmonics_reject_naive_or_missing_times(timestamps):
    with pytest.raises(ValueError, match="timezone-aware and nonmissing"):
        CalendarHarmonics().fit(pd.DataFrame({"upstream_departure_time": timestamps}))
    with pytest.raises(NotFittedError):
        CalendarHarmonics().transform(pd.DataFrame())


def test_huber_lasso_bounded_spike_influence_and_l1_stationarity():
    rng = np.random.default_rng(42)
    X = rng.normal(size=(500, 3))
    clean = 2 + X @ np.array([0.4, -0.2, 0]) + rng.normal(0, 0.02, len(X))
    contaminated = clean.copy()
    contaminated[::10] += 10
    robust = HuberLassoRegressor(alpha=0.001, delta=0.15).fit(X, contaminated)
    ordinary = Lasso(alpha=0.001).fit(X, contaminated)
    assert robust.converged_
    assert np.mean(abs(robust.predict(X) - clean)) < 0.04
    assert np.mean(abs(ordinary.predict(X) - clean)) > 0.8
    more_extreme = contaminated.copy()
    more_extreme[::10] += 90
    refit = clone(robust).fit(X, more_extreme)
    assert np.max(abs(refit.predict(X) - robust.predict(X))) < 0.001
    assert np.all(robust.sample_weight_[::10] < 0.03)
    assert np.all(robust.sample_weight_ > 0)
    # Check the requested Huber + L1 objective, including weight normalization.
    derivative = np.clip(contaminated - robust.predict(X), -0.15, 0.15)
    assert abs(derivative.mean()) < 1e-6
    gradient = X.T @ derivative / len(X)
    active = robust.coef_ != 0
    np.testing.assert_allclose(
        gradient[active],
        0.001 * np.sign(robust.coef_[active]),
        atol=1e-6,
    )
    assert np.max(abs(gradient[~active])) <= 0.001 + 1e-6


def test_huber_lasso_retains_repeated_peaks_in_pipeline():
    X = pd.DataFrame(
        {
            "upstream_departure_time": pd.date_range(
                "2026-09-01", periods=24 * 35, freq="h", tz="UTC"
            ),
        }
    )
    calendar = CalendarHarmonics(daily_order=2, weekly_order=1)
    features = calendar.fit_transform(X)
    clean = 2 + 0.4 * features[:, 0] + 0.2 * features[:, 4] + 0.1 * features[:, 6]
    observed = clean.copy()
    observed[::23] += 3
    pipeline = Pipeline(
        [
            ("calendar", calendar),
            ("scale", StandardScaler()),
            ("regressor", HuberLassoRegressor()),
        ]
    ).fit(X, observed)
    assert np.mean(abs(pipeline.predict(X) - clean)) < 0.02
    assert np.ptp(pipeline.predict(X)) > 0.9


@pytest.mark.parametrize(
    "params",
    [
        {"alpha": 0},
        {"delta": 0},
        {"delta": np.inf},
        {"tol": -1},
        {"max_iter": 0},
    ],
)
def test_huber_lasso_invalid_controls(params):
    with pytest.raises(ValueError):
        HuberLassoRegressor(**params).fit(np.ones((3, 1)), np.ones(3))


def test_huber_lasso_missing_values_shapes_and_nonconvergence_are_explicit():
    with pytest.raises(NotFittedError):
        HuberLassoRegressor().predict(np.ones((3, 1)))
    with pytest.raises(ValueError):
        HuberLassoRegressor().fit(np.ones((3, 1)), [1, np.nan, 2])
    rng = np.random.default_rng(7)
    X = rng.normal(size=(50, 2))
    y = X[:, 0] + rng.normal(size=50)
    with pytest.warns(ConvergenceWarning, match="did not converge"):
        model = HuberLassoRegressor(max_iter=1).fit(X, y)
    with pytest.raises(ValueError, match="feature count"):
        model.predict(np.ones((3, 1)))


def _visits():
    local = pd.date_range(
        "2026-09-01 12:00", periods=60, freq="D", tz="America/Vancouver"
    )
    departure = local.tz_convert("UTC")
    return pd.DataFrame(
        {
            "upstream_departure_time": departure,
            "arrival_time": departure + pd.Timedelta(minutes=12),
            "available_at": departure + pd.Timedelta(minutes=15),
            "service_day": local.tz_localize(None).normalize(),
            "trip_instance_key": [f"trip_{i}" for i in range(60)],
        }
    )


def test_splits_keep_calendar_gaps_and_purge_late_endpoint_labels():
    visits = _visits()
    before = visits.copy(deep=True)
    end = pd.Timestamp("2026-10-31")
    _, original_summary = make_harmonic_time_splits(visits, evaluation_end=end)
    cutoff = original_summary.iloc[0].fit_cutoff_utc
    visits.loc[0, "available_at"] = cutoff  # equality is not available before fitting
    splits, summary = make_harmonic_time_splits(visits, evaluation_end=end)
    assert len(splits) == 3
    for train, evaluation in splits:
        assert train.max() < evaluation.min()
    assert 0 not in splits[0][0]
    assert 0 in splits[1][0]
    assert summary.iloc[0].purged_train_rows == 1
    pdt.assert_frame_equal(visits.iloc[1:], before.iloc[1:])
    missing_day = visits.drop(index=50).reset_index(drop=True)
    with pytest.raises(ValueError, match="Insufficient coverage"):
        make_harmonic_time_splits(missing_day, evaluation_end=end)
    with pytest.raises(ValueError, match="Insufficient coverage"):
        make_harmonic_time_splits(visits, evaluation_end=end, min_train_days=50)


def test_splits_use_wall_clock_cutover_across_dst_and_reject_bad_dates():
    visits = _visits()
    local = pd.date_range(
        "2026-09-14 12:00", periods=60, freq="D", tz="America/Vancouver"
    )
    visits["upstream_departure_time"] = local.tz_convert("UTC")
    visits["arrival_time"] = visits.upstream_departure_time + pd.Timedelta(minutes=12)
    visits["available_at"] = visits.arrival_time + pd.Timedelta(minutes=1)
    visits["service_day"] = local.tz_localize(None).normalize()
    _, summary = make_harmonic_time_splits(
        visits, evaluation_end=pd.Timestamp("2026-11-13")
    )
    assert summary.iloc[-1].fit_cutoff_utc == pd.Timestamp("2026-11-05 11:00Z")
    visits.loc[0, "service_day"] += pd.Timedelta(days=1)
    with pytest.raises(ValueError, match="cutover"):
        make_harmonic_time_splits(visits, evaluation_end=pd.Timestamp("2026-11-13"))


def test_experiment_bundle_preserves_fitted_model_and_previous_runs(tmp_path):
    X = pd.DataFrame(
        {
            "upstream_departure_time": pd.date_range(
                "2026-09-01", periods=12, freq="h", tz="UTC"
            ),
        }
    )
    model = Pipeline(
        [
            ("calendar", CalendarHarmonics()),
            ("regressor", HuberLassoRegressor()),
        ]
    ).fit(X, np.ones(12))
    args = dict(
        models={"robust": model},
        predictions=pd.DataFrame({"prediction": model.predict(X)}),
        cv_results=pd.DataFrame({"score": [0.0]}),
        metadata={"dataset_id": "test"},
    )
    first = save_harmonic_experiment(tmp_path, **args)
    second = save_harmonic_experiment(tmp_path, **args)
    assert first != second
    with (first / "models.pkl").open("rb") as stream:
        restored = pickle.load(stream)
    np.testing.assert_allclose(restored["robust"].predict(X), model.predict(X))
    assert len(pd.read_csv(first / "predictions.csv")) == len(X)
