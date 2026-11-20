from __future__ import annotations

from datetime import datetime, time, timezone

import pytest

from data_ingestion import nightly

# pylint: disable=missing-docstring

_ENV = {
    "MYSQL_HOST": "mysql",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "u",
    "MYSQL_PASSWORD": "p",
    "MYSQL_DATABASE": "d",
    "R2_ACCOUNT_ID": "a",
    "R2_BUCKET": "b",
    "R2_ACCESS_KEY_ID": "k",
    "R2_SECRET_ACCESS_KEY": "s",
}


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_utc(2026, 11, 19, 0, 10), _utc(2026, 11, 19, 0, 30)),
        (_utc(2026, 11, 19, 0, 30), _utc(2026, 11, 20, 0, 30)),
        (_utc(2026, 11, 19, 23, 59), _utc(2026, 11, 20, 0, 30)),
    ],
)
def test_next_run_is_strictly_after_now(now, expected):
    assert nightly.next_run(now, time(0, 30)) == expected


def test_parse_run_at_and_retain_days():
    assert nightly.parse_run_at("01:05") == time(1, 5)
    assert nightly.parse_retain_days(None) is None
    assert nightly.parse_retain_days(" ") is None
    assert nightly.parse_retain_days("14") == 14
    for bad in ("0", "-1", "x"):
        with pytest.raises(ValueError):
            nightly.parse_retain_days(bad)
    with pytest.raises(ValueError):
        nightly.parse_run_at("0030")


def test_run_once_prunes_only_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(nightly.archive, "run_export", lambda s: calls.append("export"))
    monkeypatch.setattr(
        nightly.retention, "prune", lambda s, retain_days: calls.append(retain_days)
    )
    nightly.run_once(object(), None)
    nightly.run_once(object(), 14)
    assert calls == ["export", "export", 14]


def test_main_sleeps_until_next_run_and_retries_after_failure(monkeypatch):
    outcomes = [RuntimeError("R2 down"), None]

    def run_once(settings, retain_days):
        assert retain_days == 7
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome

    monkeypatch.setattr(nightly, "run_once", run_once)
    sleeps = []
    nightly.main(
        {**_ENV, "ARCHIVE_RETAIN_DAYS": "7"},
        sleep=sleeps.append,
        now=lambda: _utc(2026, 11, 19, 0, 0),
        max_runs=2,
    )
    assert sleeps == [3600.0]
    assert not outcomes
