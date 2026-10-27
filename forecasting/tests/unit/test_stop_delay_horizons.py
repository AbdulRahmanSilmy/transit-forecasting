from collections.abc import Sequence

import pandas as pd
import pandas.testing as pdt
import pytest

from forecasting import get_latest_stop_delays, get_stop_delay_horizons


def _updates() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "trip_route_id": ["97-KEL"] * 5,
            "trip_id": ["trip-1"] * 5,
            "trip_start_timestamp": ["2026-09-08 09:00:00"] * 5,
            "trip_direction_id": [0] * 5,
            "stop_id": ["140114"] * 5,
            "stop_sequence": [13] * 5,
            "feed_timestamp": [
                "2026-09-08 09:29:00",
                "2026-09-08 09:30:00",
                "2026-09-08 09:58:00",
                "2026-09-08 09:58:30",
                "2026-09-08 10:02:00",
            ],
            "arrival_time": ["2026-09-08 10:00:00"] * 5,
            "arrival_delay": [-120, 60, 120, 180, 240],
        }
    )


def _stops() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stop_id": [140114, 140113],
            "stop_name": ["Hwy 97 N at Sexsmith Stn"] * 2,
        }
    )


def _evaluate(
    updates: pd.DataFrame, horizons_minutes: Sequence[int] = range(1, 31)
) -> pd.DataFrame:
    return get_stop_delay_horizons(
        updates,
        _stops(),
        route_id="97-KEL",
        stop_name="Hwy 97 N at Sexsmith Stn",
        horizons_minutes=horizons_minutes,
    )


def test_selects_as_of_predictions_relative_to_final_arrival_not_publication() -> None:
    updates = _updates()
    original = updates.copy(deep=True)

    result = _evaluate(updates, horizons_minutes=[1, 2, 30, 31, 32])

    assert result["horizon_minutes"].tolist() == [1, 2, 30, 31, 32]
    assert result["true_delay_minutes"].eq(4).all()
    assert result["estimated_delay_minutes"].iloc[:4].tolist() == [3, 2, 1, -2]
    assert result["error_minutes"].iloc[:4].tolist() == [-1, -2, -3, -6]
    assert result["absolute_error_minutes"].iloc[:4].tolist() == [1, 2, 3, 6]
    assert result["estimate_age_minutes"].iloc[:4].tolist() == [0.5, 0, 0, 0]
    assert pd.isna(result["estimated_delay_minutes"].iloc[4])
    assert pd.isna(result["estimate_feed_timestamp"].iloc[4])
    assert (
        result["reference_arrival_time"]
        .eq(pd.Timestamp("2026-09-08 10:00:00", tz="UTC"))
        .all()
    )
    assert (
        result["estimate_feed_timestamp"].dropna()
        <= result.loc[result["estimate_feed_timestamp"].notna(), "cutoff_time"]
    ).all()
    pdt.assert_frame_equal(updates, original)


def test_uses_every_minute_from_one_through_thirty_by_default() -> None:
    result = _evaluate(_updates())

    assert result["horizon_minutes"].tolist() == list(range(1, 31))
    assert len(result) == 30


def test_matches_existing_latest_delay_selection_and_timestamp_ties() -> None:
    updates = _updates()
    tie = updates.iloc[[2]].assign(id=99, arrival_delay=300)
    updates = pd.concat([updates, tie], ignore_index=True).iloc[::-1]
    latest = get_latest_stop_delays(
        updates,
        _stops(),
        route_id="97-KEL",
        stop_name="Hwy 97 N at Sexsmith Stn",
    )

    result = _evaluate(updates, horizons_minutes=[2])

    assert result["estimated_delay_minutes"].tolist() == [5]
    assert result["true_delay_minutes"].tolist() == latest["delay_minutes"].tolist()
    pdt.assert_frame_equal(result, _evaluate(updates.iloc[::-1], horizons_minutes=[2]))


def test_timestamp_ties_without_ids_use_first_input_row() -> None:
    updates = _updates().drop(columns="id")
    tie = updates.iloc[[2]].assign(arrival_delay=300)
    updates = pd.concat([tie, updates], ignore_index=True)

    assert _evaluate(updates, horizons_minutes=[2])[
        "estimated_delay_minutes"
    ].tolist() == [5]


def test_missing_historical_delay_is_not_replaced_by_an_older_prediction() -> None:
    updates = _updates()
    updates.loc[2, "arrival_delay"] = float("nan")

    result = _evaluate(updates, horizons_minutes=[2])

    assert result["estimated_delay_minutes"].isna().all()
    assert result["error_minutes"].isna().all()
    assert result["estimate_feed_timestamp"].notna().all()


@pytest.mark.parametrize("column", ["arrival_delay", "arrival_time"])
def test_excludes_missing_final_references_without_falling_back(column: str) -> None:
    updates = _updates()
    updates.loc[4, column] = None

    with pytest.warns(UserWarning, match="1 with missing final delay/arrival"):
        with pytest.raises(ValueError, match="No stop visits"):
            _evaluate(updates)


def test_excludes_arrivals_beyond_recording_window() -> None:
    updates = _updates()
    updates.loc[4, "arrival_time"] = "2026-09-08 10:03:00"

    with pytest.warns(UserWarning, match="1 with final arrival after"):
        with pytest.raises(ValueError, match="No stop visits"):
            _evaluate(updates)


def test_keeps_platforms_daily_instances_and_repeated_visits_separate() -> None:
    updates = _updates()
    other_platform = updates.assign(stop_id="140113", arrival_delay=600)
    repeated_visit = updates.assign(stop_sequence=20, arrival_delay=900)
    next_day = updates.assign(
        trip_start_timestamp="2026-09-09 09:00:00",
        feed_timestamp=pd.to_datetime(updates["feed_timestamp"]) + pd.Timedelta(days=1),
        arrival_time="2026-09-09 10:00:00",
        arrival_delay=1200,
    )
    other_route = updates.assign(trip_route_id="other", arrival_delay=9999)
    unrelated_trip = updates.assign(trip_id="other-trip", arrival_delay=1800)
    all_updates = pd.concat(
        [
            updates,
            other_platform,
            repeated_visit,
            next_day,
            other_route,
            unrelated_trip,
        ],
        ignore_index=True,
    )

    result = _evaluate(all_updates, horizons_minutes=[2])

    assert len(result) == 5
    assert sorted(result["true_delay_minutes"]) == [4, 10, 15, 20, 30]
    assert sorted(result["estimated_delay_minutes"]) == [2, 10, 15, 20, 30]


def test_supports_missing_stop_sequence_and_nullable_stop_sequences() -> None:
    updates = _updates()
    without_sequence = _evaluate(updates.drop(columns="stop_sequence"))
    updates["stop_sequence"] = pd.Series([pd.NA] * len(updates), dtype="Int64")

    result = _evaluate(updates)

    assert len(result) == len(without_sequence) == 30
    assert result["true_delay_minutes"].eq(4).all()


def test_warns_once_for_missing_start_descriptors() -> None:
    updates = _updates().assign(trip_start_timestamp=None)

    with pytest.warns(UserWarning, match="publication date is approximate") as recorded:
        result = _evaluate(updates)

    assert len(recorded) == 1
    assert result["trip_instance_key"].str.contains("missing-start-").all()


@pytest.mark.parametrize(
    "horizons",
    [[], [0], [-1], [1.5], [True], [1, 1], ["1"]],
)
def test_rejects_invalid_horizons(horizons: Sequence[int]) -> None:
    with pytest.raises(ValueError, match="unique positive integers"):
        _evaluate(_updates(), horizons_minutes=horizons)


def test_requires_arrival_times() -> None:
    with pytest.raises(ValueError, match="missing required columns: arrival_time"):
        _evaluate(_updates().drop(columns="arrival_time"))
