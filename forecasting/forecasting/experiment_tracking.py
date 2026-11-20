"""Log delay-study harness runs to a local MLflow tracking store.

MLflow stores run parameters, metrics and artifacts; the study's ``LOG.md``
keeps the narrative (hypothesis, prediction, verdict) and cites MLflow run IDs.
``mlflow`` is imported lazily so the harness itself does not depend on it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_UNSAFE = re.compile(r"[^0-9A-Za-z_\-. /]")


def tracking_uri(tracking_dir: str | Path) -> str:
    """Return the SQLite tracking URI for a local store directory.

    Parameters
    ----------
    tracking_dir : str or pathlib.Path
        Directory holding ``mlflow.db`` and ``artifacts/``.

    Returns
    -------
    str
        ``sqlite:///`` URI with an absolute path.
    """
    return f"sqlite:///{(Path(tracking_dir) / 'mlflow.db').resolve()}"


def _key(*parts: object) -> str:
    """Join parts into an MLflow-safe metric/param key."""
    return "/".join(_UNSAFE.sub("_", str(part)) for part in parts)


def _finite(value: Any) -> bool:
    """Whether a value is a finite real number."""
    return isinstance(value, (int, float, np.integer, np.floating)) and bool(
        np.isfinite(value)
    )


def flatten_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    """Flatten harness metrics into scalar MLflow metrics.

    Parameters
    ----------
    metrics : mapping
        Output of :func:`forecasting.delay_forecast_eval.score_predictions`.

    Returns
    -------
    dict of str to float
        Keys such as ``MAE_s/harmonic_huber``, ``delta_MAE_s/B3_calendar_median``,
        ``fold_MAE_s/dev6/B0_transit_bc``, ``period_MAE_s/am_peak/...`` and
        ``criterion/meets_all`` (booleans as 0/1). Non-finite values are dropped.
    """
    flat: dict[str, float] = {}
    for name in ("common_rows", "primary_rows", "primary_days", "holiday_rows"):
        flat[name] = float(metrics[name])
    for record in metrics["summary"].to_dict("records"):
        for column in ("MAE_s", "MedAE_s", "RMSE_s", "bias_s"):
            flat[_key(column, record["method"])] = record[column]
    for record in metrics["paired_vs_B0"].to_dict("records"):
        for column in (
            "delta_MAE_s",
            "ci_low_s",
            "ci_high_s",
            "relative_change",
            "win_rate_vs_B0",
        ):
            flat[_key(column, record["method"])] = record[column]
    for table, group, prefix in (
        ("by_fold", "fold", "fold_MAE_s"),
        ("by_period", "period", "period_MAE_s"),
        ("by_stop", "stop_sequence", "stop_MAE_s"),
        ("by_lead", "lead_minutes", "lead_MAE_s"),
    ):
        frame: pd.DataFrame = metrics[table]
        methods = [column for column in frame if column not in (group, "rows")]
        for record in frame.to_dict("records"):
            for method in methods:
                flat[_key(prefix, record[group], method)] = record[method]
    clock = metrics.get("schedule_clock")
    if clock is not None:
        for record in clock.to_dict("records"):
            for column in ("share_offset_60", "share_mismatch"):
                flat[_key("timetable", column, record["service_month"])] = record[
                    column
                ]
    traversal = metrics.get("traversal")
    if traversal is not None:
        for record in traversal["summary"].to_dict("records"):
            for column in ("MAE_s", "MedAE_s", "RMSE_s", "bias_s"):
                flat[_key("traversal_" + column, record["method"])] = record[column]
        for record in traversal["dwell_summary"].to_dict("records"):
            for column in ("MAE_s", "bias_s"):
                flat[_key("dwell_" + column, record["rule"])] = record[column]
        segments = traversal["by_segment"]
        methods = [c for c in segments if c not in ("segment_key", "segment", "rows")]
        for record in segments.to_dict("records"):
            name = record["segment"].replace(": ", "_").replace(" -> ", "-")
            for method in methods:
                flat[_key("traversal_segment_MAE_s", name, method)] = record[method]
    for section in ("control", "criterion"):
        for name, value in (metrics.get(section) or {}).items():
            flat[_key(section, name)] = float(value)
    return {key: float(value) for key, value in flat.items() if _finite(value)}


def flatten_params(config: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten nested configuration into string MLflow parameters.

    Parameters
    ----------
    config : mapping
        Nested configuration (lists are joined with commas).
    prefix : str, optional
        Key prefix.

    Returns
    -------
    dict of str to str
        Values truncated to MLflow's 6000-character parameter limit.
    """
    params: dict[str, str] = {}
    for name, value in config.items():
        key = f"{prefix}{name}"
        if isinstance(value, Mapping):
            params |= flatten_params(value, prefix=f"{key}.")
        elif isinstance(value, (list, tuple)):
            params[key] = ",".join(str(item) for item in value)[:6000]
        else:
            params[key] = str(value)[:6000]
    return params


def log_study_run(
    *,
    tracking_dir: str | Path,
    experiment: str,
    run_name: str,
    metrics: Mapping[str, Any],
    params: Mapping[str, Any],
    tags: Mapping[str, str],
    artifact_dir: str | Path,
) -> str:
    """Create one MLflow run with flattened metrics, params, tags and artifacts.

    Parameters
    ----------
    tracking_dir : str or pathlib.Path
        Local store directory; created if needed. Artifacts go to
        ``<tracking_dir>/artifacts/<experiment>``.
    experiment : str
        Experiment name (the study).
    run_name : str
        Human-readable run name.
    metrics : mapping
        Harness metrics, flattened with :func:`flatten_metrics`.
    params : mapping
        Nested configuration, flattened with :func:`flatten_params`.
    tags : mapping of str to str
        Run tags such as git hash, split, model and hypothesis.
    artifact_dir : str or pathlib.Path
        Directory whose contents are logged as artifacts.

    Returns
    -------
    str
        MLflow run ID.
    """
    import mlflow

    tracking_dir = Path(tracking_dir).resolve()
    tracking_dir.mkdir(parents=True, exist_ok=True)
    client = mlflow.MlflowClient(tracking_uri=tracking_uri(tracking_dir))
    found = client.get_experiment_by_name(experiment)
    if found is None:
        artifact_root = (tracking_dir / "artifacts" / experiment).as_uri()
        experiment_id = client.create_experiment(
            experiment, artifact_location=artifact_root
        )
    else:
        experiment_id = found.experiment_id
    run = client.create_run(experiment_id, run_name=run_name, tags=dict(tags))
    run_id = run.info.run_id
    try:
        for key, value in flatten_params(params).items():
            client.log_param(run_id, key, value)
        flat = flatten_metrics(metrics)
        timestamp = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        batch = [
            mlflow.entities.Metric(key, value, timestamp, 0)
            for key, value in flat.items()
        ]
        for start in range(0, len(batch), 900):
            client.log_batch(run_id, metrics=batch[start : start + 900])
        client.log_artifacts(run_id, str(artifact_dir))
    except BaseException:
        client.set_terminated(run_id, status="FAILED")
        raise
    client.set_terminated(run_id, status="FINISHED")
    return run_id
