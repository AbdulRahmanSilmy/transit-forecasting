import errno
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from zipfile import BadZipFile, ZipFile

import pytest

from forecasting import download_static_gtfs, static_gtfs


def _archive(files: dict[str, bytes] | None = None) -> bytes:
    if files is None:
        files = {
            "stops.txt": b"stop_id,stop_name\n1,Example\n",
            "stop_times.txt": b"trip_id,stop_id,stop_sequence\ntrip,1,1\n",
            "trips.txt": b"trip_id,route_id,direction_id,shape_id\ntrip,97-KEL,0,shape\n",
            "shapes.txt": b"shape_id,shape_pt_sequence\nshape,1\n",
            "feed_info.txt": b"feed_version\ncurrent\n",
            "../escape.txt": b"not a GTFS table",
        }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_downloads_complete_snapshot_without_changing_historical_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive()
    calls = []

    def fake_urlopen(url: str, timeout: int) -> BytesIO:
        calls.append((url, timeout))
        return BytesIO(payload)

    monkeypatch.setattr(static_gtfs, "urlopen", fake_urlopen)
    historical = tmp_path / "static_data"
    historical.mkdir()
    (historical / "trips.txt").write_text("historical trips\n")

    snapshot = download_static_gtfs(tmp_path / "live-cache")

    assert snapshot.parent == tmp_path / "live-cache"
    assert (snapshot / "gtfs.zip").read_bytes() == payload
    assert (snapshot / "feed_info.txt").read_text() == "feed_version\ncurrent\n"
    assert all((snapshot / name).is_file() for name in static_gtfs._REQUIRED_FILES)
    assert (historical / "trips.txt").read_text() == "historical trips\n"
    assert not (tmp_path / "escape.txt").exists()
    assert not (snapshot.parent / "escape.txt").exists()
    assert list(snapshot.parent.iterdir()) == [snapshot]
    assert calls == [(static_gtfs._DEFAULT_GTFS_URL, 60)]


def test_contacts_publisher_even_when_the_same_archive_is_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive()
    calls = []

    def fake_urlopen(url: str, timeout: int) -> BytesIO:
        calls.append(url)
        return BytesIO(payload)

    monkeypatch.setattr(static_gtfs, "urlopen", fake_urlopen)

    first = download_static_gtfs(tmp_path)
    second = download_static_gtfs(tmp_path)

    assert first == second
    assert len(calls) == 2
    assert list(tmp_path.iterdir()) == [first]


def test_changed_feed_creates_a_new_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _archive()
    changed = _archive({name: b"changed\n" for name in static_gtfs._REQUIRED_FILES})
    payloads = iter([original, changed])
    monkeypatch.setattr(
        static_gtfs, "urlopen", lambda url, timeout: BytesIO(next(payloads))
    )

    first = download_static_gtfs(tmp_path)
    second = download_static_gtfs(tmp_path)

    assert first != second
    assert (first / "gtfs.zip").read_bytes() == original
    assert (second / "gtfs.zip").read_bytes() == changed


@pytest.mark.parametrize("missing", static_gtfs._REQUIRED_FILES)
def test_rejects_incomplete_archives_before_creating_cache_files(
    missing: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive(
        {name: b"table\n" for name in static_gtfs._REQUIRED_FILES if name != missing}
    )
    monkeypatch.setattr(static_gtfs, "urlopen", lambda url, timeout: BytesIO(payload))

    with pytest.raises(ValueError, match=missing):
        download_static_gtfs(tmp_path / "cache")

    assert not (tmp_path / "cache").exists()


def test_rejects_non_zip_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        static_gtfs, "urlopen", lambda url, timeout: BytesIO(b"not a zip")
    )

    with pytest.raises(BadZipFile):
        download_static_gtfs(tmp_path / "cache")

    assert not (tmp_path / "cache").exists()


def test_does_not_fall_back_to_old_cache_when_download_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive()
    monkeypatch.setattr(static_gtfs, "urlopen", lambda url, timeout: BytesIO(payload))
    snapshot = download_static_gtfs(tmp_path)

    def offline(url: str, timeout: int) -> BytesIO:
        raise URLError("publisher unavailable")

    monkeypatch.setattr(static_gtfs, "urlopen", offline)
    with pytest.raises(URLError, match="publisher unavailable"):
        download_static_gtfs(tmp_path)

    assert (snapshot / "gtfs.zip").read_bytes() == payload


def test_reports_corrupt_cache_instead_of_loading_partial_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive()
    monkeypatch.setattr(static_gtfs, "urlopen", lambda url, timeout: BytesIO(payload))
    snapshot = download_static_gtfs(tmp_path)
    (snapshot / "trips.txt").unlink()

    with pytest.raises(ValueError, match="incomplete or modified"):
        download_static_gtfs(tmp_path)


def test_validates_snapshot_published_by_a_concurrent_downloader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _archive()
    monkeypatch.setattr(static_gtfs, "urlopen", lambda url, timeout: BytesIO(payload))

    def publish_concurrently(staging: Path, destination: Path) -> None:
        destination.mkdir()
        for file in staging.iterdir():
            (destination / file.name).write_bytes(file.read_bytes())
        raise FileExistsError(errno.EEXIST, "already published")

    monkeypatch.setattr(Path, "rename", publish_concurrently)

    snapshot = download_static_gtfs(tmp_path)

    assert (snapshot / "gtfs.zip").read_bytes() == payload
    assert list(tmp_path.iterdir()) == [snapshot]
