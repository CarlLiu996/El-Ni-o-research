from __future__ import annotations

import pandas as pd
import pytest

from elnino_cta.external_observations import build_india_monsoon_cumulative


LOCATIONS = ("Maharashtra", "Uttar Pradesh", "Karnataka")


def monsoon_weather(
    *,
    current_complete_locations: int = 2,
    include_incomplete_location: bool = True,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in range(1991, 2021):
        for location in LOCATIONS:
            for date in pd.date_range(f"{year}-06-01", periods=10, freq="D"):
                rows.append({
                    "date": date,
                    "region": "india_sugar",
                    "location": location,
                    "precip_mm": 8.0,
                    "source": "NASA POWER PRECTOTCORR",
                })

    for location in LOCATIONS[:current_complete_locations]:
        for date in pd.date_range("2026-06-01", periods=10, freq="D"):
            rows.append({
                "date": date,
                "region": "india_sugar",
                "location": location,
                "precip_mm": 10.0,
                "source": "NASA POWER PRECTOTCORR",
            })

    if include_incomplete_location and current_complete_locations < len(LOCATIONS):
        for date in pd.date_range("2026-06-01", periods=9, freq="D"):
            rows.append({
                "date": date,
                "region": "india_sugar",
                "location": LOCATIONS[-1],
                "precip_mm": 10.0,
                "source": "NASA POWER PRECTOTCORR",
            })
    return pd.DataFrame(rows)


def test_monsoon_cumulative_equal_weights_only_complete_locations() -> None:
    result = build_india_monsoon_cumulative(
        monsoon_weather(),
        as_of="2026-06-10",
        downloaded_at="2026-06-11T00:00:00Z",
    )

    latest = result.query("season_year == 2026").iloc[-1]
    assert latest["season_day"] == 10
    assert latest["valid_locations"] == 2
    assert latest["current_cumulative_mm"] == pytest.approx(100.0)
    assert latest["climatology_cumulative_mm"] == pytest.approx(80.0)
    assert latest["cumulative_departure_pct"] == pytest.approx(0.25)
    assert latest["historical_percentile"] == pytest.approx(1.0)
    assert latest["status"] == "OK"
    assert latest["downloaded_at"] == "2026-06-11T00:00:00Z"


def test_monsoon_cumulative_requires_two_complete_locations() -> None:
    result = build_india_monsoon_cumulative(
        monsoon_weather(current_complete_locations=1),
        as_of="2026-06-10",
    )

    latest = result.query("season_year == 2026").iloc[-1]
    assert latest["status"] == "INSUFFICIENT_COVERAGE"
    assert pd.isna(latest["current_cumulative_mm"])
    assert pd.isna(latest["cumulative_departure_pct"])


def test_monsoon_before_june_is_not_in_season() -> None:
    result = build_india_monsoon_cumulative(
        monsoon_weather(),
        as_of="2026-05-20",
        downloaded_at="2026-05-20T12:00:00Z",
    )

    assert len(result) == 1
    assert result.iloc[0]["status"] == "NOT_IN_SEASON"
    assert result.iloc[0]["season_day"] == 0


def test_monsoon_rejects_duplicate_point_dates() -> None:
    weather = monsoon_weather()
    weather = pd.concat([weather, weather.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate"):
        build_india_monsoon_cumulative(weather, as_of="2026-06-10")
