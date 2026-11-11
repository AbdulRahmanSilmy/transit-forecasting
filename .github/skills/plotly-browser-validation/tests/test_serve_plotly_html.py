import select
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_plotly_html.py"


@pytest.fixture
def preview(tmp_path):
    html = tmp_path / "figure.html"
    payload = b"<!doctype html><html><body>Plotly preview</body></html>"
    html.write_bytes(payload)
    process = subprocess.Popen(
        [sys.executable, "-u", str(SCRIPT), str(html)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 10)
        assert ready, "Preview server did not announce readiness."
        url = process.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("/plot.html")
        yield url, html, payload
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_serves_only_the_selected_html_snapshot(preview):
    url, html, payload = preview
    with urlopen(url, timeout=5) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert int(response.headers["Content-Length"]) == len(payload)
        assert response.read() == payload
    html.write_bytes(b"Changed after startup")
    with urlopen(url, timeout=5) as response:
        assert response.read() == payload


@pytest.mark.parametrize(
    "path", ["/", "/figure.html", "/../figure.html", "/favicon.ico"]
)
def test_other_paths_return_explicit_errors(preview, path):
    url, _, _ = preview
    origin = url.removesuffix("/plot.html")
    with pytest.raises(HTTPError) as error:
        urlopen(origin + path, timeout=5)
    assert error.value.code == 404


@pytest.mark.parametrize("kind", ["missing", "directory", "non_html"])
def test_invalid_input_files_fail_before_starting_server(tmp_path, kind):
    path = tmp_path / "figure.html"
    if kind == "directory":
        path.mkdir()
    elif kind == "non_html":
        path = tmp_path / "figure.txt"
        path.write_text("Not an HTML export")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "must be an existing .html or .htm file" in result.stderr
    assert "http://" not in result.stdout
