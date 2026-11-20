from datetime import date

import numpy as np
import pytest
from test_delay_forecast_eval import _config, _updates

from forecasting.delay_forecast_eval import run_study, write_run
from forecasting.experiment_tracking import (
    flatten_metrics,
    flatten_params,
    log_study_run,
    tracking_uri,
)


@pytest.fixture(scope="module")
def harness_run():
    return run_study(
        _config(), _updates(days=14), model_name="harmonic_huber", split="dev"
    )


def test_flatten_metrics_is_scalar_and_finite(harness_run):
    flat = flatten_metrics(harness_run.metrics)
    assert flat["MAE_s/harmonic_huber"] > 0
    assert "delta_MAE_s/B3_calendar_median" in flat
    assert "fold_MAE_s/dev1/B0_transit_bc" in flat
    assert flat["criterion/meets_all"] in (0.0, 1.0)
    assert all(np.isfinite(value) for value in flat.values())
    # B1 ties B0 exactly here, so its undefined win rate is dropped, not logged as NaN.
    assert "win_rate_vs_B0/B1_constant_anchor" not in flat


def test_flatten_params_joins_lists_and_nests():
    params = flatten_params(
        {"model": "m", "config": {"fold_starts": [date(2026, 1, 1), date(2026, 1, 8)]}}
    )
    assert params == {"model": "m", "config.fold_starts": "2026-01-01,2026-01-08"}


def test_log_study_run_records_metrics_tags_and_artifacts(tmp_path, harness_run):
    mlflow = pytest.importorskip("mlflow")
    write_run(harness_run, tmp_path / "run", provenance={"git_hash": "abc"})
    run_id = log_study_run(
        tracking_dir=tmp_path / "store",
        experiment="unit_study",
        run_name="unit-run",
        metrics=harness_run.metrics,
        params={"model": "harmonic_huber", "config": _config().to_dict()},
        tags={"git_hash": "abc", "hypothesis": "H0"},
        artifact_dir=tmp_path / "run",
    )
    client = mlflow.MlflowClient(tracking_uri=tracking_uri(tmp_path / "store"))
    run = client.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.tags["hypothesis"] == "H0"
    assert run.data.params["config.route_id"] == "97-KEL"
    assert run.data.metrics["MAE_s/harmonic_huber"] == pytest.approx(
        flatten_metrics(harness_run.metrics)["MAE_s/harmonic_huber"]
    )
    artifacts = {item.path for item in client.list_artifacts(run_id)}
    assert {
        "metrics.json",
        "predictions.csv.gz",
        "audit.csv",
        "config.json",
    } <= artifacts
