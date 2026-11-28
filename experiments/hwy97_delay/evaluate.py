"""Run the Hwy 97 delay study harness for one traversal model.

Protected file: see experiments/hwy97_delay/HARNESS.md.

Examples
--------
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model harmonic_huber
conda run -n transit_env python experiments/hwy97_delay/evaluate.py --model calendar_median
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

STUDY_DIR = Path(__file__).resolve().parent
REPO_ROOT = STUDY_DIR.parents[1]
MLFLOW_DIR = REPO_ROOT / "data" / "mlflow"
sys.path.insert(0, str(REPO_ROOT / "forecasting"))

from forecasting import get_backtest_dataset  # noqa: E402
from forecasting.delay_forecast_display import make_run_figures  # noqa: E402
from forecasting.delay_forecast_eval import (  # noqa: E402
    StudyConfig,
    check_test_access,
    log_row,
    run_study,
    write_run,
)
from forecasting.experiment_tracking import log_study_run, tracking_uri  # noqa: E402


def _git(*args: str) -> str:
    """Return stripped git output from the repository root."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _load(config: StudyConfig, split: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """Load trip updates and dataset checksums for the split."""
    windows = [
        (config.dev_dataset_id, config.dev_archive_start, config.dev_archive_end)
    ]
    if split == "test":
        windows.append(
            (config.test_dataset_id, config.test_archive_start, config.test_archive_end)
        )
    frames, checksums = [], {}
    for dataset_id, start, end in windows:
        dataset = get_backtest_dataset(
            REPO_ROOT / "data",
            source="r2",
            dataset_id=dataset_id,
            env_file=REPO_ROOT / ".env",
            archive_start=start,
            archive_end=end,
            route_id=config.route_id,
            direction_id=config.direction_id,
        )
        metadata = json.loads((dataset.directory / "metadata.json").read_text())
        checksums[dataset_id] = metadata["files"]["trip_updates.pkl"]
        frames.append(dataset.trip_updates)
    return pd.concat(frames, ignore_index=True), checksums


def main(argv: list[str] | None = None) -> None:
    """Parse arguments, run the harness, persist and print results.

    Parameters
    ----------
    argv : list of str or None, optional
        Command-line arguments; defaults to ``sys.argv``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--anchor",
        default="feed",
        help="Chain anchor for B2, B3 and the candidate: 'feed' (Transit BC's "
        "earliest estimate, default) or an anchor model, e.g. calendar_median.",
    )
    parser.add_argument(
        "--dwell",
        default="stop_median",
        help="Dwell model for B2, B3, the candidate and the control: "
        "'stop_median' (per-stop training median, default) or 'stop_hold' "
        "(per-stop hold floor on the predicted arrival delay).",
    )
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--i-have-owner-approval", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="Skip saving outputs.")
    parser.add_argument(
        "--tracking",
        choices=["mlflow", "files"],
        default="mlflow",
        help="Log to the local MLflow store (default) or a data/experiments folder.",
    )
    parser.add_argument("--hypothesis", default="", help="Backlog ID, e.g. H1.")
    parser.add_argument("--log-id", default="", help="LOG.md entry ID, e.g. E001.")
    args = parser.parse_args(argv)

    config = StudyConfig.from_json(STUDY_DIR / "config.json")
    if args.split == "test":
        check_test_access(config, approved=args.i_have_owner_approval)
    trip_updates, checksums = _load(config, args.split)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        run = run_study(
            config,
            trip_updates,
            model_name=args.model,
            split=args.split,
            approved=args.i_have_owner_approval,
            anchor=args.anchor,
            dwell=args.dwell,
        )

    git_hash = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    anchor_tag = "" if args.anchor == "feed" else f"_anchor-{args.anchor}"
    dwell_tag = "" if args.dwell == "stop_median" else f"_dwell-{args.dwell}"
    run_id = f"{stamp}_{args.model}{anchor_tag}{dwell_tag}_{args.split}"
    metrics = run.metrics
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(
            f"\nRun {run_id}  anchor {args.anchor}  dwell {args.dwell}  git {git_hash}"
            f"{' (DIRTY)' if dirty else ''}"
        )
        print(
            f"Scored rows: {metrics['primary_rows']:,} on {metrics['primary_days']} "
            f"service days (holiday rows scored separately: {metrics['holiday_rows']:,})"
        )
        print(
            "\nUnscorable rows per method:\n",
            metrics["coverage"].to_string(index=False),
        )
        print(
            "\nSummary (seconds):\n", metrics["summary"].round(1).to_string(index=False)
        )
        print(
            "\nPaired vs B0:\n", metrics["paired_vs_B0"].round(3).to_string(index=False)
        )
        print("\nMAE by fold:\n", metrics["by_fold"].round(1).to_string(index=False))
        print(
            "\nMAE by period:\n", metrics["by_period"].round(1).to_string(index=False)
        )
        traversal = metrics["traversal"]
        print(
            "\nTraversal (seconds):\n",
            traversal["summary"].round(1).to_string(index=False),
        )
        print(
            "\nDwell rules (seconds):\n",
            traversal["dwell_summary"].round(1).to_string(index=False),
        )
        print(
            "\nTraversal MAE by segment (s):\n",
            traversal["by_segment"]
            .drop(columns="segment_key")
            .round(1)
            .to_string(index=False),
        )
        anchor_free = metrics.get("anchor_free")
        if anchor_free is not None:
            print(
                f"\nAnchor-free (secondary; actual first-stop departure as anchor, "
                f"{anchor_free['rows']:,} rows, {anchor_free['excluded_rows']:,} "
                "without a departure):\n",
                anchor_free["summary"].round(1).to_string(index=False),
            )
            if len(anchor_free["paired_vs_B3"]):
                print(
                    "Anchor-free vs B3:\n",
                    anchor_free["paired_vs_B3"].round(1).to_string(index=False),
                )
            print(
                "Anchor-free MAE by fold:\n",
                anchor_free["by_fold"].round(1).to_string(index=False),
            )
        if metrics["control"] is not None:
            print("\nShuffled control:", metrics["control"])
            print("Criterion (development, indicative only):", metrics["criterion"])
    if dirty:
        print("\nWARNING: uncommitted changes; commit before logging this run.")
    run_ref = run_id
    if not args.no_write:
        figures = make_run_figures(
            run.predictions,
            candidate=None if metrics["criterion"] is None else args.model,
            timezone=config.timezone,
            holidays=[pd.Timestamp(day) for day in config.holidays],
            traversals=run.traversals,
        )
        provenance = {
            "args": vars(args),
            "config": config.to_dict(),
            "git_hash": git_hash,
            "git_dirty": dirty,
            "trip_updates_sha256": checksums,
        }
        if args.tracking == "files":
            run_dir = REPO_ROOT / "data" / "experiments" / config.study / run_id
            write_run(run, run_dir, provenance=provenance, figures=figures)
            print(f"\nSaved: {run_dir}")
        else:
            MLFLOW_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=MLFLOW_DIR) as staging:
                run_dir = Path(staging) / run_id
                # CDN Plotly keeps each figure small and renders inside the MLflow UI.
                write_run(
                    run, run_dir, provenance=provenance, figures=figures, plotlyjs="cdn"
                )
                run_ref = log_study_run(
                    tracking_dir=MLFLOW_DIR,
                    experiment=config.study,
                    run_name=run_id,
                    metrics=metrics,
                    params={
                        "model": args.model,
                        "anchor": args.anchor,
                        "dwell": args.dwell,
                        "split": args.split,
                        "config": config.to_dict(),
                    },
                    tags={
                        "git_hash": git_hash,
                        "git_dirty": str(dirty),
                        "split": args.split,
                        "model": args.model,
                        "anchor": args.anchor,
                        "dwell": args.dwell,
                        "hypothesis": args.hypothesis,
                        "log_id": args.log_id,
                        "locked_test": str(args.split == "test"),
                    },
                    artifact_dir=run_dir,
                )
            print(f"\nMLflow run: {run_ref}  (experiment {config.study!r})")
            print(
                f"View: conda run -n transit_env mlflow ui --backend-store-uri {tracking_uri(MLFLOW_DIR)}"
            )
    row = log_row(
        run,
        run_id=run_ref,
        git_hash=git_hash,
        log_id=args.log_id,
        hypothesis=args.hypothesis,
    )
    print("\nLOG.md row:\n" + row)


if __name__ == "__main__":
    main()
