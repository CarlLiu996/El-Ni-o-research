from __future__ import annotations

import json

import pandas as pd
import pytest

from elnino_cta.cli import build_parser
from elnino_cta.external_observations import (
    build_india_monsoon_cumulative,
    refresh_external_observations,
)


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


def _write_external_config(tmp_path):
    regions_path = tmp_path / "regions.json"
    regions_path.write_text(json.dumps({
        "india_sugar": [
            {"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71},
            {"name": "Uttar Pradesh", "latitude": 26.85, "longitude": 80.91},
            {"name": "Karnataka", "latitude": 15.32, "longitude": 75.71},
        ]
    }), encoding="utf-8")
    normals_path = tmp_path / "normals.json"
    normals_path.write_text(json.dumps({
        "version": "IMD revised normal dates 2020",
        "source": "https://mausam.imd.gov.in/normal.pdf",
        "onset_kerala": "06-01",
        "withdrawal_northwest_start": "09-17",
    }), encoding="utf-8")
    return regions_path, normals_path


def _imd_events_ok() -> pd.DataFrame:
    return pd.DataFrame({
        "season_year": [2026, 2026],
        "event_type": ["ONSET_KERALA", "WITHDRAWAL_NORTHWEST_START"],
        "official_date": ["2026-06-04", pd.NaT],
        "normal_date": ["2026-06-01", "2026-09-17"],
        "departure_days": [3, pd.NA],
        "announcement_date": ["2026-06-04", pd.NaT],
        "source_url": ["https://mausam.imd.gov.in/onset.pdf"] * 2,
        "normal_source": ["https://mausam.imd.gov.in/normal.pdf"] * 2,
        "normal_version": ["2020"] * 2,
        "status": ["OK", "NOT_ANNOUNCED"],
        "review_status": ["AUTO_ACCEPTED", "NO_EXPLICIT_DECLARATION"],
    })


def test_refresh_external_observations_degrades_one_source_and_writes_manifest(tmp_path) -> None:
    regions_path, normals_path = _write_external_config(tmp_path)
    output_dir = tmp_path / "data" / "processed"

    result = refresh_external_observations(
        output_dir=output_dir,
        as_of="2026-06-10",
        regions_path=regions_path,
        normals_path=normals_path,
        source_bundle={
            "weather": monsoon_weather(),
            "imd": _imd_events_ok(),
            "ndvi_error": PermissionError("AUTH_REQUIRED"),
        },
        refreshed_at="2026-06-11T00:00:00Z",
    )

    climate_dir = output_dir / "climate"
    assert result["overall"] == "PARTIAL"
    assert result["datasets"]["monsoon_cumulative"]["status"] == "OK"
    assert result["datasets"]["imd_events"]["status"] == "OK"
    assert result["datasets"]["ndvi"]["status"] == "AUTH_REQUIRED"
    assert (climate_dir / "india_monsoon_cumulative.csv").exists()
    assert (climate_dir / "india_monsoon_events.csv").exists()
    assert (climate_dir / "india_sugar_ndvi.csv").exists()
    assert (climate_dir / "external_manifest.json").exists()
    serialized = (climate_dir / "external_manifest.json").read_text(encoding="utf-8")
    assert "Authorization" not in serialized
    assert "Bearer " not in serialized


def test_refresh_external_observations_keeps_last_success_file_on_failure(tmp_path) -> None:
    regions_path, normals_path = _write_external_config(tmp_path)
    climate_dir = tmp_path / "data" / "processed" / "climate"
    climate_dir.mkdir(parents=True)
    target = climate_dir / "india_sugar_ndvi.csv"
    target.write_text("product_date,ndvi,status\n2026-05-25,0.55,OK\n", encoding="utf-8")
    before = target.read_bytes()

    result = refresh_external_observations(
        output_dir=tmp_path / "data" / "processed",
        as_of="2026-06-10",
        regions_path=regions_path,
        normals_path=normals_path,
        source_bundle={
            "weather": monsoon_weather(),
            "imd": _imd_events_ok(),
            "ndvi_error": PermissionError("secret-value-must-not-leak"),
        },
    )

    assert target.read_bytes() == before
    assert result["datasets"]["ndvi"]["status"] == "AUTH_REQUIRED"
    assert "secret-value-must-not-leak" not in json.dumps(result)


def test_cli_exposes_external_refresh_arguments() -> None:
    args = build_parser().parse_args([
        "external-refresh",
        "--as-of", "2026-06-10",
        "--ndvi-start", "2001-01-01",
    ])

    assert args.command == "external-refresh"
    assert args.output == "data/processed"
    assert args.regions == "config/regions.json"
    assert args.normals == "config/india_monsoon_normals.json"
