from __future__ import annotations

import json

import pandas as pd
import pytest

from elnino_cta.historical_review import (
    build_event_evidence_panel,
    build_external_climate_evidence,
    build_index_evidence,
    build_weather_evidence,
    build_yield_evidence,
    identify_enso_events,
)
from elnino_cta.pipeline import refresh_historical_climate


def test_identify_enso_events_requires_five_consecutive_seasons_and_marks_open_run() -> None:
    dates = pd.date_range("2009-09-01", periods=30, freq="MS")
    values = [0.0, 0.6, 0.7, 0.8, 0.9, 0.8, 0.6, 0.0]
    values += [0.0] * 5 + [0.6, 0.7, 0.8, 0.9] + [0.0] * 8
    values += [0.5, 0.6, 0.7, 0.8, 0.9]
    oni = pd.DataFrame({"date": dates, "oni": values, "source": "NOAA CPC"})

    events = identify_enso_events(oni)

    assert events["status"].tolist() == ["COMPLETED", "ONGOING"]
    assert events.iloc[0]["duration_seasons"] == 6
    assert events.iloc[0]["event_id"] == "2009-2010"
    assert not events["event_id"].duplicated().any()


def test_identify_enso_events_breaks_run_when_month_is_missing() -> None:
    oni = pd.DataFrame({
        "date": pd.to_datetime([
            "2009-10-01", "2009-11-01", "2009-12-01",
            "2010-02-01", "2010-03-01", "2010-04-01", "2010-05-01",
        ]),
        "oni": [0.6] * 7,
        "source": ["NOAA CPC"] * 7,
    })

    assert identify_enso_events(oni).empty


def events() -> pd.DataFrame:
    return pd.DataFrame({
        "event_id": ["2009-2010"],
        "start_date": pd.to_datetime(["2009-07-01"]),
        "peak_date": pd.to_datetime(["2009-12-01"]),
        "end_date": pd.to_datetime(["2010-04-01"]),
        "peak_oni": [1.6],
        "duration_seasons": [10],
        "status": ["COMPLETED"],
        "source": ["NOAA CPC"],
    })


def test_index_evidence_uses_fixed_t0_t12_window_for_soi_and_dmi() -> None:
    dates = pd.date_range("2009-07-01", "2010-07-01", freq="MS")
    oni = pd.DataFrame({"date": dates, "oni": 1.0, "source": "NOAA CPC"})
    soi = pd.DataFrame({
        "date": list(dates) + [pd.Timestamp("2010-08-01")],
        "soi": [-8.0] * len(dates) + [999.0],
        "source": "NOAA CPC",
    })
    dmi = pd.DataFrame({"date": dates, "dmi": 0.4, "source": "NOAA PSL"})

    evidence = build_index_evidence(events(), oni, soi, dmi)

    assert evidence["observation_date"].max() <= pd.Timestamp("2010-07-01")
    assert set(evidence["metric_id"]) == {
        "oni_peak", "oni_duration_seasons", "soi_mean_t0_t12", "dmi_mean_t0_t12"
    }
    assert evidence.query("metric_id == 'soi_mean_t0_t12'")["value"].iloc[0] == pytest.approx(-8.0)


def nasa_daily_history() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in range(1991, 2021):
        for month in range(1, 13):
            for region in ("india_sugar", "malaysia_palm"):
                rows.append({
                    "date": pd.Timestamp(year, month, 1),
                    "region": region,
                    "precip_mm": 2.0,
                    "root_zone_soil_wetness": 0.6,
                    "source": "NASA POWER",
                })
    for date in pd.date_range("2009-07-01", "2010-07-01", freq="MS"):
        for region in ("india_sugar", "malaysia_palm"):
            rows.append({
                "date": date,
                "region": region,
                "precip_mm": 1.0,
                "root_zone_soil_wetness": 0.5,
                "source": "NASA POWER",
            })
    return pd.DataFrame(rows).drop_duplicates(["date", "region"], keep="last")


def test_weather_evidence_keeps_region_and_event_window_grain() -> None:
    evidence = build_weather_evidence(events(), nasa_daily_history())

    assert not evidence.duplicated(["event_id", "region"]).any()
    assert {"precip_anomaly_pct", "soil_wetness_anomaly", "coverage_pct"}.issubset(evidence)
    assert evidence["observation_date"].max() <= pd.Timestamp("2010-07-01")


def faostat_with_future_outlier() -> pd.DataFrame:
    values = {2004: 100, 2005: 100, 2006: 100, 2007: 100, 2008: 100, 2009: 110, 2010: 120, 2011: 9999}
    return pd.DataFrame([{
        "area": "India",
        "item": "Sugar cane",
        "element": "Yield",
        "year": year,
        "unit": "100 mg/ha",
        "value": value,
        "flag": "A",
        "source": "FAOSTAT",
    } for year, value in values.items()])


def test_yield_evidence_uses_only_prior_five_years() -> None:
    evidence = build_yield_evidence(events(), faostat_with_future_outlier())
    row = evidence.query("event_id == '2009-2010' and year_offset == 0").iloc[0]

    assert row["baseline_end_year"] == 2008
    assert row["baseline_years"] == 5
    assert row["deviation_vs_prior5_median"] == pytest.approx(0.10)


def test_external_climate_evidence_uses_only_event_window() -> None:
    monsoon = pd.DataFrame({
        "date": pd.to_datetime(["2009-09-30", "2010-07-01", "2011-07-01"]),
        "season_year": [2009, 2010, 2011],
        "cumulative_departure_pct": [-0.1, 0.05, 9.9],
        "valid_locations": [3, 3, 3],
        "status": ["OK", "OK", "OK"],
        "source": ["NASA POWER"] * 3,
    })
    ndvi = pd.DataFrame({
        "product_date": pd.to_datetime(["2009-07-01", "2010-07-01", "2010-08-01"]),
        "z_score": [-0.5, 0.2, 999.0],
        "valid_pixel_ratio": [0.8, 0.9, 1.0],
        "status": ["OK", "OK", "OK"],
        "source": ["MOD13Q1.061"] * 3,
    })

    evidence = build_external_climate_evidence(events(), monsoon, ndvi)

    assert evidence["observation_date"].max() <= pd.Timestamp("2010-07-01")
    assert evidence["metric_id"].str.startswith("india_monsoon_departure_y").any()
    assert evidence["metric_id"].eq("india_sugar_ndvi_zscore").any()
    assert 999.0 not in evidence["value"].tolist()


def test_event_evidence_panel_has_unique_long_grain() -> None:
    index = pd.DataFrame({
        "event_id": ["2009-2010"], "status": ["COMPLETED"], "commodity": ["both"],
        "metric_id": ["oni_peak"], "value": [1.6], "unit": ["degC"],
        "coverage_pct": [1.0], "observation_date": [pd.Timestamp("2009-12-01")],
        "source": ["NOAA CPC"], "limitation": ["ex post"],
    })
    weather = build_weather_evidence(events(), nasa_daily_history())
    yields = build_yield_evidence(events(), faostat_with_future_outlier())
    external = build_external_climate_evidence(
        events(),
        pd.DataFrame(columns=["date", "season_year", "status"]),
        pd.DataFrame(columns=["product_date", "status"]),
    )

    panel = build_event_evidence_panel(events(), index, weather, yields, external)

    assert not panel.duplicated(["event_id", "commodity", "metric_id"]).any()
    assert set(panel["layer"]) == {"L1", "L2"}


def test_refresh_historical_climate_writes_index_and_weather_files(tmp_path, monkeypatch) -> None:
    monthly = pd.DataFrame({
        "date": pd.to_datetime(["2000-01-01", "2000-02-01"]),
        "source": ["official"] * 2,
    })
    monkeypatch.setattr(
        "elnino_cta.pipeline.fetch_oni",
        lambda: monthly.assign(oni=[0.5, 0.6]),
    )
    monkeypatch.setattr(
        "elnino_cta.pipeline.fetch_soi",
        lambda: monthly.assign(soi=[-1.0, -1.2]),
    )
    monkeypatch.setattr(
        "elnino_cta.pipeline.fetch_dmi",
        lambda: monthly.assign(dmi=[0.1, 0.2]),
    )

    def fake_weather(location, start, end):
        assert start == "1981-01-01"
        assert end == "2026-08-25"
        return pd.DataFrame({
            "date": pd.to_datetime(["2000-01-01"]),
            "location": [location.name],
            "precip_mm": [1.0],
            "root_zone_soil_wetness": [0.5],
            "source": ["NASA POWER"],
        })

    monkeypatch.setattr("elnino_cta.pipeline.fetch_daily_agroclimate", fake_weather)
    regions = tmp_path / "regions.json"
    regions.write_text(json.dumps({
        "india_sugar": [{"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71}]
    }), encoding="utf-8")

    manifest = refresh_historical_climate(
        tmp_path / "processed",
        start="1981-01-01",
        end="2026-08-25",
        regions_path=regions,
    )

    climate = tmp_path / "processed" / "climate"
    assert (climate / "noaa_oni.csv").exists()
    assert (climate / "noaa_soi.csv").exists()
    assert (climate / "noaa_dmi.csv").exists()
    assert (climate / "nasa_power_precipitation.csv").exists()
    assert manifest["rows"]["weather"] == 1
    assert manifest["coverage"]["weather"]["max_date"] == "2000-01-01"
