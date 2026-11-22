import json
from pathlib import Path

import mlflow
import pytest

from forecasting.experiment_tracking import tracking_uri
from forecasting.exploration_tracking import ExplorationSession


class FakePlotlyFigure:
    """Stands in for a Plotly figure without needing kaleido."""

    def write_image(self, path, **kwargs):
        Path(path).write_bytes(b"png")

    def write_html(self, path, **kwargs):
        Path(path).write_text("<html></html>")


class FakeMatplotlibFigure:
    def savefig(self, path, **kwargs):
        Path(path).write_bytes(b"png")


def _start(tmp_path):
    return ExplorationSession.start(
        tmp_path / "work",
        tracking_dir=tmp_path / "mlflow",
        experiment="unit_study",
        log_id="E099",
        hypothesis="H9",
        purpose="Check the unit test.",
    )


def _artifacts(session):
    client = mlflow.MlflowClient(tracking_uri=tracking_uri(session.tracking_dir))
    top = {a.path for a in client.list_artifacts(session.run_id)}
    figures = {a.path for a in client.list_artifacts(session.run_id, "figures")}
    return client, top, figures


def test_session_logs_figures_journal_and_tags(tmp_path):
    session = _start(tmp_path)
    png = session.log_figure(FakePlotlyFigure(), "Bias by Stop", "Where is bias?")
    assert png.name == "01_bias_by_stop.png" and png.exists()
    session.note("bias by stop", "Grows along the route.", "Split by dwell.", key=True)
    session.log_figure(FakeMatplotlibFigure(), "dwell", "Is dwell skewed?")
    client, top, figures = _artifacts(session)
    assert {"journal.md", "key_figures.json"} <= top
    assert figures == {
        "figures/01_bias_by_stop.png",
        "figures/01_bias_by_stop.html",
        "figures/02_dwell.png",
    }
    tags = client.get_run(session.run_id).data.tags
    assert tags["run_type"] == "exploration"
    assert tags["log_id"] == "E099" and tags["hypothesis"] == "H9"
    journal = (session.workdir / "journal.md").read_text()
    assert "Grows along the route." in journal and "(key figure)" in journal
    assert "(not written yet)" in journal  # figure 2 has no observation yet


def test_session_resumes_across_processes_and_exports_key_figures(tmp_path):
    session = _start(tmp_path)
    session.log_figure(FakePlotlyFigure(), "a", "Q1")
    session.log_figure(FakePlotlyFigure(), "b", "Q2")
    reopened = ExplorationSession.load(session.workdir)
    assert reopened.run_id == session.run_id
    reopened.note("b", "Shows it.", key=True)
    copied = reopened.export_key_figures(tmp_path / "repo_figures")
    assert [path.name for path in copied] == ["02_b.png"]
    keys = json.loads((reopened.workdir / "key_figures.json").read_text())
    assert [record["name"] for record in keys] == ["b"]
    assert reopened.finish() == session.run_id
    client, _, _ = _artifacts(reopened)
    assert client.get_run(session.run_id).info.status == "FINISHED"


def test_session_rejects_duplicates_unknown_names_and_reuse(tmp_path):
    session = _start(tmp_path)
    session.log_figure(FakePlotlyFigure(), "a", "Q1")
    with pytest.raises(ValueError):
        session.log_figure(FakePlotlyFigure(), "A", "again")
    with pytest.raises(KeyError):
        session.note("missing", "nothing")
    with pytest.raises(FileExistsError):
        _start(tmp_path)
