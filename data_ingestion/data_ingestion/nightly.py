"""Export yesterday to R2 every night and optionally prune MySQL.

Run as a long-lived service (the ``archive`` Compose service) with
``python -m data_ingestion.nightly``. Each run:

1. archives every complete UTC day not yet in R2, so a missed night catches
   up automatically;
2. if ``ARCHIVE_RETAIN_DAYS`` is set, deletes MySQL days older than that
   window whose R2 copies are verified (see :mod:`data_ingestion.retention`).

A run happens at start-up and then daily at ``ARCHIVE_RUN_AT_UTC``
(``HH:MM``, default ``10:00``, the feed's quietest time). A failed run is logged and retried after
``RETRY_DELAY``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from datetime import time as clock_time
from datetime import timedelta, timezone
from typing import Callable, Mapping

from . import archive, retention

logger = logging.getLogger(__name__)

# The quietest time of the feed: Aug-Nov 2026 averaged under 100 trip rows
# and 40 vehicle rows per half hour from 10:00 to 10:30 UTC (2-3 a.m. in
# Kelowna), against up to 77,000 and 5,400 at peak, so the export and prune
# compete least with ingestion for the Pi's CPU, disk, and network.
DEFAULT_RUN_AT_UTC = "10:00"
RETRY_DELAY = timedelta(hours=1)


def parse_run_at(value: str) -> clock_time:
    """Parse an ``HH:MM`` UTC time of day.

    Parameters
    ----------
    value : str
        Time such as ``"00:30"``.

    Returns
    -------
    datetime.time
        Parsed time.

    Raises
    ------
    ValueError
        If ``value`` is not a valid ``HH:MM`` time.
    """
    hour, separator, minute = value.strip().partition(":")
    if not separator or not hour.isdigit() or not minute.isdigit():
        raise ValueError(f"Expected HH:MM for ARCHIVE_RUN_AT_UTC, got {value!r}.")
    return clock_time(int(hour), int(minute))


def parse_retain_days(value: str | None) -> int | None:
    """Parse ``ARCHIVE_RETAIN_DAYS``; unset or empty disables pruning.

    Parameters
    ----------
    value : str or None
        Raw setting.

    Returns
    -------
    int or None
        Days to keep, or None to skip pruning.

    Raises
    ------
    ValueError
        If the value is not a positive integer.
    """
    if value is None or not value.strip():
        return None
    if not value.strip().isdigit() or int(value) < 1:
        raise ValueError(f"ARCHIVE_RETAIN_DAYS must be a positive integer: {value!r}.")
    return int(value)


def next_run(now: datetime, run_at: clock_time) -> datetime:
    """Return the first ``run_at`` UTC moment strictly after ``now``.

    Parameters
    ----------
    now : datetime.datetime
        Current timezone-aware time.
    run_at : datetime.time
        Daily UTC time of day.

    Returns
    -------
    datetime.datetime
        Next scheduled run, in UTC.
    """
    now = now.astimezone(timezone.utc)
    candidate = datetime.combine(now.date(), run_at, tzinfo=timezone.utc)
    return candidate if candidate > now else candidate + timedelta(days=1)


def run_once(settings: archive.ArchiveSettings, retain_days: int | None) -> None:
    """Export all complete unarchived days, then prune if enabled.

    Parameters
    ----------
    settings : archive.ArchiveSettings
        Source and archive settings.
    retain_days : int or None
        Retention window for :func:`data_ingestion.retention.prune`, or None
        to keep everything in MySQL.
    """
    archive.run_export(settings)
    if retain_days is None:
        logger.info("ARCHIVE_RETAIN_DAYS is not set; MySQL rows are kept.")
    else:
        retention.prune(settings, retain_days=retain_days)


def main(
    environ: Mapping[str, str] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_runs: int | None = None,
) -> None:
    """Run the nightly export loop.

    Parameters
    ----------
    environ : collections.abc.Mapping or None, optional
        Settings source; defaults to ``os.environ``.
    sleep : collections.abc.Callable, optional
        Sleep function, replaceable in tests.
    now : collections.abc.Callable, optional
        Clock returning an aware UTC time, replaceable in tests.
    max_runs : int or None, optional
        Stop after this many runs; None runs forever.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    environ = os.environ if environ is None else environ
    run_at = parse_run_at(environ.get("ARCHIVE_RUN_AT_UTC", DEFAULT_RUN_AT_UTC))
    retain_days = parse_retain_days(environ.get("ARCHIVE_RETAIN_DAYS"))
    settings = archive.load_archive_settings(environ=environ)
    runs = 0
    while max_runs is None or runs < max_runs:
        runs += 1
        try:
            run_once(settings, retain_days)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Nightly archive run failed; retrying in %s", RETRY_DELAY)
            wake = now() + RETRY_DELAY
        else:
            wake = next_run(now(), run_at)
            logger.info("Nightly archive run finished; next run at %s", wake)
        if max_runs is None or runs < max_runs:
            sleep(max((wake - now()).total_seconds(), 0))


if __name__ == "__main__":
    main()
