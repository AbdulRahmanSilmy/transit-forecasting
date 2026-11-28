import numpy as np
import pandas as pd
import pytest

from forecasting.dwell_models import (
    StopHoldDwell,
    StopMedianDwell,
    fit_hold_hinge,
    make_dwell_model,
)

OFFSETS = np.arange(-120.0, 181.0, 1.0)
FLOORS = np.arange(-60.0, 61.0, 5.0)


def _links(stop, arrival, dwell, scheduled=0.0, first=False):
    arrival = np.asarray(arrival, float)
    return pd.DataFrame(
        {
            "from_stop_id": stop,
            "upstream_is_first_stop": first,
            "upstream_arrival_delay_seconds": arrival,
            "upstream_dwell_seconds": np.asarray(dwell, float),
            "scheduled_dwell_seconds": scheduled,
        }
    )


def _holding_stop(n=400, seed=0):
    """Early buses wait for the scheduled departure; others dwell 20 s."""
    rng = np.random.default_rng(seed)
    arrival = rng.uniform(-180, 300, n)
    departure = np.maximum(arrival + 20, 0) + rng.normal(0, 2, n)
    return arrival, departure


def test_hinge_recovers_offset_and_floor_at_a_holding_stop():
    arrival, departure = _holding_stop()
    c, h = fit_hold_hinge(
        arrival, departure, offsets=OFFSETS, floors=FLOORS, min_gain=0.02
    )
    assert c == pytest.approx(20, abs=1)
    assert h == pytest.approx(0, abs=5)


def test_hinge_keeps_the_median_rule_where_buses_do_not_hold():
    rng = np.random.default_rng(1)
    arrival = rng.uniform(-180, 300, 400)
    departure = arrival + 15 + rng.normal(0, 3, 400)
    c, h = fit_hold_hinge(
        arrival, departure, offsets=OFFSETS, floors=FLOORS, min_gain=0.02
    )
    assert h == -np.inf
    assert c == pytest.approx(np.median(departure - arrival))


def test_hold_dwell_waits_out_the_schedule_only_when_early():
    arrival, departure = _holding_stop()
    hold = _links("s6", arrival, departure - arrival + 60, scheduled=60.0)
    plain = _links("s7", arrival, np.full(len(arrival), 25.0))
    model = StopHoldDwell().fit(pd.concat([hold, plain], ignore_index=True))
    assert model.params_.loc["s7", "h"] == -np.inf
    requests = pd.DataFrame(
        {
            "from_stop_id": ["s6", "s6", "s6", "s7", "s7", "s1", "new"],
            "scheduled_dwell_seconds": [60.0, 60.0, 60.0, 0.0, 0.0, 0.0, 0.0],
            "upstream_is_first_stop": [False] * 5 + [True, False],
        }
    )
    dwell = model.predict(
        requests,
        arrival_delay_seconds=np.array([-120.0, 200.0, np.nan, -120.0, 200.0, -50, 0]),
    )
    # Early at a holding stop: wait until on schedule (scheduled 60 s dwell
    # plus the 120 s it is early). Late: the usual 20 s change over the dwell.
    assert dwell[0] == pytest.approx(60 + 120, abs=5)
    assert dwell[1] == pytest.approx(60 + 20, abs=1)
    # Without a predicted arrival there is no hold.
    assert dwell[2] == pytest.approx(dwell[1])
    # A stop that does not hold ignores the delay.
    assert dwell[3] == dwell[4] == pytest.approx(25)
    # First stop is always zero; unseen stops use the pooled median dwell.
    assert dwell[5] == 0
    assert dwell[6] == pytest.approx(model.pooled_)


def test_hold_dwell_ignores_first_stop_and_unusable_training_links():
    arrival, departure = _holding_stop(n=60)
    links = pd.concat(
        [
            _links("s2", arrival, departure - arrival),
            _links("s1", arrival, np.full(60, 999.0), first=True),
            _links("s3", [np.nan] * 60, np.full(60, 30.0)),
        ],
        ignore_index=True,
    )
    model = StopHoldDwell().fit(links)
    assert list(model.params_.index) == ["s2"]
    model = StopHoldDwell(min_links=100).fit(links)
    assert model.params_.empty


def test_stop_median_dwell_ignores_arrival_delay():
    links = _links("s2", [0, 0, 0], [10, 20, 90])
    model = StopMedianDwell().fit(links)
    requests = links.assign(upstream_is_first_stop=[False, False, True])
    np.testing.assert_allclose(
        model.predict(requests, arrival_delay_seconds=np.array([-300.0, 0, 300])),
        [20, 20, 0],
    )
    assert not model.uses_arrival_delay


def test_registry_builds_models_and_rejects_unknown_names():
    assert isinstance(make_dwell_model("stop_median"), StopMedianDwell)
    assert make_dwell_model("stop_hold").uses_arrival_delay
    with pytest.raises(ValueError):
        make_dwell_model("nope")
