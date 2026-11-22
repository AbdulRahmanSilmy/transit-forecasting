"""Log an iterative exploratory-plot session to the local MLflow store.

Exploration is a loop: ask one question, make one figure, look at it, write
down what it shows and what to plot next. Each step usually runs in its own
process, so an :class:`ExplorationSession` keeps its state (the MLflow run ID
and every figure's question, observation and next step) in a JSON file in a
working directory and reopens the same MLflow run each time.

Every figure is logged as a PNG and, for Plotly figures, an interactive HTML
under ``figures/``. ``journal.md`` (the question -> observation -> next-step
trail) and ``key_figures.json`` (figures selected for reviewers) are
re-logged after each step. ``mlflow`` is imported lazily.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .experiment_tracking import tracking_uri

STATE_FILE = "exploration_state.json"
_SLUG = re.compile(r"[^0-9a-z]+")


@dataclass
class FigureRecord:
    """One exploratory figure and what was learned from it.

    Attributes
    ----------
    index : int
        Order in the session, starting at 1.
    name : str
        Short slug used in file names.
    question : str
        What the figure was made to answer.
    png : str
        File name of the PNG in the working directory.
    html : str or None
        File name of the interactive HTML, if any.
    observation : str or None
        What the figure showed, written after looking at it.
    next_step : str or None
        The next question or decision it led to.
    key : bool
        Whether the figure is selected for reviewers.
    """

    index: int
    name: str
    question: str
    png: str
    html: str | None = None
    observation: str | None = None
    next_step: str | None = None
    key: bool = False


@dataclass
class ExplorationSession:
    """A resumable exploratory-plot session backed by one MLflow run.

    Create one with :meth:`start` and reopen it in later processes with
    :meth:`load`.

    Attributes
    ----------
    workdir : pathlib.Path
        Directory holding the state file, figures and journal.
    tracking_dir : pathlib.Path
        Local MLflow store directory (``mlflow.db`` plus ``artifacts/``).
    run_id : str
        MLflow run ID of the session.
    log_id : str
        LOG.md entry the session belongs to, e.g. ``"E011"``.
    hypothesis : str
        Backlog ID, e.g. ``"H8"``.
    figures : list of FigureRecord
        Figures in the order they were made.
    """

    workdir: Path
    tracking_dir: Path
    run_id: str
    log_id: str
    hypothesis: str
    figures: list[FigureRecord] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        workdir: str | Path,
        *,
        tracking_dir: str | Path,
        experiment: str,
        log_id: str,
        hypothesis: str,
        purpose: str,
        tags: dict[str, str] | None = None,
    ) -> ExplorationSession:
        """Create an exploration run and its working directory.

        Parameters
        ----------
        workdir : str or pathlib.Path
            New or empty directory for this session's files.
        tracking_dir : str or pathlib.Path
            Local MLflow store directory; created if needed.
        experiment : str
            MLflow experiment name (the study), e.g. ``"hwy97_delay"``.
        log_id : str
            LOG.md entry ID the exploration supports.
        hypothesis : str
            Backlog ID.
        purpose : str
            One sentence on what the exploration should establish; logged
            as the run description and at the top of the journal.
        tags : dict of str to str, optional
            Extra run tags.

        Returns
        -------
        ExplorationSession
            The new session, already saved to ``workdir``.

        Raises
        ------
        FileExistsError
            If ``workdir`` already holds a session.
        """
        import mlflow

        workdir = Path(workdir).resolve()
        if (workdir / STATE_FILE).exists():
            raise FileExistsError(f"{workdir} already holds a session; use load().")
        workdir.mkdir(parents=True, exist_ok=True)
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
        run_tags = {
            "run_type": "exploration",
            "log_id": log_id,
            "hypothesis": hypothesis,
            "git_hash": _git_hash(),
            "mlflow.note.content": purpose,
            **(tags or {}),
        }
        run = client.create_run(
            experiment_id, run_name=f"{log_id}_explore_{hypothesis}", tags=run_tags
        )
        session = cls(
            workdir=workdir,
            tracking_dir=tracking_dir,
            run_id=run.info.run_id,
            log_id=log_id,
            hypothesis=hypothesis,
        )
        (workdir / "purpose.txt").write_text(purpose + "\n")
        session._save()
        return session

    @classmethod
    def load(cls, workdir: str | Path) -> ExplorationSession:
        """Reopen a session saved by :meth:`start`.

        Parameters
        ----------
        workdir : str or pathlib.Path
            The session's working directory.

        Returns
        -------
        ExplorationSession
            The saved session.
        """
        workdir = Path(workdir).resolve()
        raw = json.loads((workdir / STATE_FILE).read_text())
        raw["figures"] = [FigureRecord(**record) for record in raw["figures"]]
        raw["workdir"] = workdir
        raw["tracking_dir"] = Path(raw["tracking_dir"])
        return cls(**raw)

    def log_figure(self, fig: Any, name: str, question: str) -> Path:
        """Save a figure, log it to the run and record its question.

        Parameters
        ----------
        fig : plotly.graph_objects.Figure or matplotlib.figure.Figure
            Figure to save. Plotly figures need ``write_image`` (kaleido) and
            also get an HTML copy; other figures need ``savefig``.
        name : str
            Short name; slugified for file names.
        question : str
            What the figure is meant to answer.

        Returns
        -------
        pathlib.Path
            The PNG, for viewing before writing the observation.

        Raises
        ------
        ValueError
            If a figure with the same name was already logged.
        """
        slug = _SLUG.sub("_", name.lower()).strip("_")
        if any(record.name == slug for record in self.figures):
            raise ValueError(f"Figure {slug!r} already logged; use a new name.")
        index = len(self.figures) + 1
        stem = f"{index:02d}_{slug}"
        png = self.workdir / f"{stem}.png"
        html = None
        if hasattr(fig, "write_image"):
            fig.write_image(png, width=1100, height=600, scale=2)
            html = self.workdir / f"{stem}.html"
            fig.write_html(html, include_plotlyjs="cdn")
        else:
            fig.savefig(png, dpi=150, bbox_inches="tight")
        client = self._client()
        client.log_artifact(self.run_id, str(png), "figures")
        if html is not None:
            client.log_artifact(self.run_id, str(html), "figures")
        self.figures.append(
            FigureRecord(
                index=index,
                name=slug,
                question=question,
                png=png.name,
                html=None if html is None else html.name,
            )
        )
        self._save()
        self._log_journal(client)
        return png

    def note(
        self,
        name: str,
        observation: str,
        next_step: str | None = None,
        *,
        key: bool = False,
    ) -> None:
        """Record what a figure showed and what it leads to.

        Parameters
        ----------
        name : str
            Name given to :meth:`log_figure`.
        observation : str
            What the figure showed.
        next_step : str, optional
            The next question, plot or decision.
        key : bool, default False
            Select the figure for reviewers (issue comment, LOG entry).

        Raises
        ------
        KeyError
            If no figure has that name.
        """
        record = self._record(name)
        record.observation = observation
        record.next_step = next_step
        record.key = key
        self._save()
        self._log_journal(self._client())

    def export_key_figures(self, dest_dir: str | Path) -> list[Path]:
        """Copy the selected figures' PNGs to a directory (e.g. in the repo).

        Parameters
        ----------
        dest_dir : str or pathlib.Path
            Destination, created if needed.

        Returns
        -------
        list of pathlib.Path
            Copied files in session order.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for record in self.figures:
            if record.key:
                target = dest_dir / record.png
                shutil.copy2(self.workdir / record.png, target)
                copied.append(target)
        return copied

    def finish(self) -> str:
        """Log the final journal and mark the run finished.

        Returns
        -------
        str
            MLflow run ID.
        """
        client = self._client()
        self._log_journal(client)
        client.set_terminated(self.run_id, status="FINISHED")
        return self.run_id

    def journal(self) -> str:
        """Render the question -> observation -> next-step trail as Markdown.

        Returns
        -------
        str
            Markdown with one section per figure; selected figures are marked.
        """
        purpose_file = self.workdir / "purpose.txt"
        purpose = purpose_file.read_text().strip() if purpose_file.exists() else ""
        lines = [f"# {self.log_id} exploration ({self.hypothesis})", "", purpose, ""]
        for record in self.figures:
            mark = " (key figure)" if record.key else ""
            lines += [
                f"## {record.index:02d}. {record.name}{mark}",
                "",
                f"- **Question:** {record.question}",
                f"- **Observation:** {record.observation or '(not written yet)'}",
                f"- **Next:** {record.next_step or '-'}",
                f"- Files: `figures/{record.png}`"
                + (f", `figures/{record.html}`" if record.html else ""),
                "",
            ]
        return "\n".join(lines)

    def _record(self, name: str) -> FigureRecord:
        slug = _SLUG.sub("_", name.lower()).strip("_")
        for record in self.figures:
            if record.name == slug:
                return record
        raise KeyError(f"No figure named {slug!r}.")

    def _client(self) -> Any:
        import mlflow

        return mlflow.MlflowClient(tracking_uri=tracking_uri(self.tracking_dir))

    def _save(self) -> None:
        state = asdict(self)
        state["workdir"] = str(self.workdir)
        state["tracking_dir"] = str(self.tracking_dir)
        (self.workdir / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n")

    def _log_journal(self, client: Any) -> None:
        journal = self.workdir / "journal.md"
        journal.write_text(self.journal())
        keys = self.workdir / "key_figures.json"
        keys.write_text(
            json.dumps([asdict(r) for r in self.figures if r.key], indent=2) + "\n"
        )
        client.log_artifact(self.run_id, str(journal))
        client.log_artifact(self.run_id, str(keys))


def _git_hash() -> str:
    """Return the short HEAD hash, or ``'unknown'`` outside a repository."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
