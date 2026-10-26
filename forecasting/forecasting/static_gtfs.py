"""Fetch current static GTFS without overwriting historical snapshots."""

import errno
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

_DEFAULT_GTFS_URL = "https://bct.tmix.se/Tmix.Cap.TdExport.WebApi/gtfs/?operatorIds=47"
_REQUIRED_FILES = ("stops.txt", "stop_times.txt", "trips.txt", "shapes.txt")
_OPTIONAL_FILES = (
    "agency.txt",
    "routes.txt",
    "feed_info.txt",
    "calendar.txt",
    "calendar_dates.txt",
)


def download_static_gtfs(
    cache_dir: str | Path,
    *,
    url: str = _DEFAULT_GTFS_URL,
) -> Path:
    """Fetch current GTFS into a content-addressed snapshot directory.

    Every call contacts the publisher so an existing cache cannot silently
    substitute stale trip IDs for a live feed. Identical downloaded archives
    reuse a snapshot; changed archives create a new one. Existing historical
    snapshots are never overwritten.

    Only explicitly named GTFS tables are extracted, not arbitrary archive
    paths. Required tables and the original archive are written to a staging
    directory before the complete snapshot is published.

    Parameters
    ----------
    cache_dir : str or pathlib.Path
        Parent directory for downloaded snapshots.
    url : str, optional
        Static GTFS ZIP URL. Defaults to BC Transit's Kelowna feed for
        operator 47, matching the project's realtime ingestion operator.

    Returns
    -------
    pathlib.Path
        Directory containing the GTFS tables and ``gtfs.zip``. The directory
        name is the SHA-256 digest of the downloaded archive.

    Raises
    ------
    urllib.error.URLError
        If downloading fails. No older cached snapshot is substituted.
    zipfile.BadZipFile
        If the response is not a valid ZIP archive.
    ValueError
        If required tables are missing or an existing snapshot is incomplete
        or has been modified.
    OSError
        If the cache cannot be written.
    """
    with urlopen(url, timeout=60) as response:
        payload = response.read()
    with ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        missing = [name for name in _REQUIRED_FILES if name not in names]
        if missing:
            raise ValueError(
                "Static GTFS archive is missing required tables: " + ", ".join(missing)
            )
        files = {
            name: archive.read(name)
            for name in (*_REQUIRED_FILES, *_OPTIONAL_FILES)
            if name in names
        }
    files["gtfs.zip"] = payload
    cache_root = Path(cache_dir)
    snapshot = cache_root / sha256(payload).hexdigest()
    cache_root.mkdir(parents=True, exist_ok=True)
    if not snapshot.exists():
        with TemporaryDirectory(prefix=".gtfs-download-", dir=cache_root) as staging:
            staging_path = Path(staging)
            for name, content in files.items():
                (staging_path / name).write_bytes(content)
            try:
                staging_path.rename(snapshot)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                # Another caller may have published the same archive.
    for name, content in files.items():
        cached = snapshot / name
        if not cached.is_file() or cached.read_bytes() != content:
            raise ValueError(f"GTFS cache snapshot is incomplete or modified: {cached}")
    return snapshot
