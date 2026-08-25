from __future__ import annotations

import json

import pandas as pd
import pytest

from elnino_cta.historical_review import (
    build_event_evidence_panel,
    build_event_cta_paths,
    build_event_price_paths,
    build_external_climate_evidence,
    build_index_evidence,
    build_monthly_cta_proxy,
    build_weather_evidence,
    build_yield_evidence,
    identify_enso_events,
    make_pattern_input,
    summarize_event_cta,
    summarize_existing_daily_cta,
    summarize_patterns,
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


def monthly_prices() -> pd.DataFrame:
    dates = pd.date_range("2009-01-31", "2012-01-31", freq="ME")
    return pd.concat([
        pd.DataFrame({
            "date": dates,
            "commodity": commodity,
            "price": [base + index * step for index in range(len(dates))],
            "source": "World Bank Pink Sheet",
        })
        for commodity, base, step in (("palm_oil", 500.0, 5.0), ("sugar", 0.20, 0.002))
    ], ignore_index=True)


def test_event_price_paths_are_t0_normalized_and_span_minus6_plus18() -> None:
    paths = build_event_price_paths(events(), monthly_prices())
    one = paths.query("event_id == '2009-2010' and commodity == 'palm_oil'")

    assert one["relative_month"].min() == -6
    assert one["relative_month"].max() == 18
    assert one.loc[one["relative_month"] == 0, "price_index"].iloc[0] == pytest.approx(100.0)
    assert (one["price"] > 0).all()


@pytest.mark.parametrize("speed", ["medium", "slow"])
def test_monthly_cta_is_lagged_scaled_and_costed(speed) -> None:
    dates = pd.date_range("2018-01-31", periods=36, freq="ME")
    prices = pd.DataFrame({
        "date": dates,
        "price": [100 + index * 2 + (index % 3) for index in range(len(dates))],
    })

    result = build_monthly_cta_proxy(prices, speed)

    assert result.loc[0, "position"] == 0
    assert (result["position"].abs() <= 2.0).all()
    assert result.loc[result["turnover"] > 0, "cost"].gt(0).all()
    pd.testing.assert_series_equal(
        result["position"], result["target_position"].shift(1).fillna(0.0), check_names=False
    )
    expected = (1 + result["net_return"]).cumprod()
    pd.testing.assert_series_equal(result["equity"], expected, check_names=False)


def test_event_cta_paths_and_summary_keep_price_and_strategy_returns_separate() -> None:
    cta_frames = []
    for commodity, group in monthly_prices().groupby("commodity"):
        frame = build_monthly_cta_proxy(group, "medium")
        frame["commodity"] = commodity
        cta_frames.append(frame)
    paths = build_event_cta_paths(events(), pd.concat(cta_frames, ignore_index=True))
    summary = summarize_event_cta(paths)

    assert not paths.duplicated(["event_id", "commodity", "speed", "date"]).any()
    assert paths["relative_month"].min() == -6
    assert paths["relative_month"].max() == 18
    assert set(summary["horizon_months"]) == {3, 6, 12, 18}
    assert {"net_return", "max_drawdown", "turnover"}.issubset(summary)


def test_existing_daily_cta_marks_old_gap_and_compounds_available_returns() -> None:
    event_frame = pd.concat([
        events(),
        pd.DataFrame({
            "event_id": ["2015-2016"],
            "start_date": pd.to_datetime(["2015-06-01"]),
            "peak_date": pd.to_datetime(["2015-12-01"]),
            "end_date": pd.to_datetime(["2016-04-01"]),
            "peak_oni": [2.6],
            "duration_seasons": [11],
            "status": ["COMPLETED"],
            "source": ["NOAA CPC"],
        }),
    ], ignore_index=True)
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2015-06-01", "2015-06-02", "2015-06-03"]),
        "net_return": [-0.10, 0.0, 0.20],
    })

    result = summarize_existing_daily_cta(
        event_frame, {("P", "fast"): daily}
    )

    old = result.query("event_id == '2009-2010'").iloc[0]
    recent = result.query("event_id == '2015-2016'").iloc[0]
    assert old["coverage_status"] == "UNAVAILABLE"
    assert recent["coverage_status"] == "AVAILABLE"
    assert recent["net_return"] == pytest.approx((0.90 * 1.0 * 1.20) - 1)
    assert recent["max_drawdown"] == pytest.approx(-0.10)


def test_pattern_labels_respect_minimum_sample_and_emit_no_allocation_fields() -> None:
    event_results = pd.DataFrame({
        "event_id": [f"event-{index}" for index in range(9)],
        "status": ["COMPLETED"] * 9,
        "commodity": ["sugar"] * 9,
        "metric_id": ["stable"] * 4 + ["limited"] * 3 + ["insufficient"] * 2,
        "value": [0.1, 0.2, 0.3, -0.1, 0.1, -0.2, 0.3, 0.1, -0.1],
    })

    patterns = summarize_patterns(event_results).set_index("metric_id")

    assert patterns.loc["stable", "pattern_strength"] == "STABLE_HISTORY"
    assert patterns.loc["limited", "pattern_strength"] == "LIMITED_SAMPLE"
    assert patterns.loc["insufficient", "pattern_strength"] == "INSUFFICIENT_EVIDENCE"
    assert not any(
        "allocation" in column or "confidence_adjustment" in column
        for column in patterns.columns
    )


def test_make_pattern_input_excludes_ongoing_and_combines_price_and_cta() -> None:
    evidence = pd.DataFrame({
        "event_id": ["old", "current"],
        "status": ["COMPLETED", "ONGOING"],
        "commodity": ["sugar", "sugar"],
        "metric_id": ["yield", "yield"],
        "value": [-0.1, 9.9],
    })
    price_paths = pd.DataFrame({
        "event_id": ["old", "old", "current", "current"],
        "status": ["COMPLETED", "COMPLETED", "ONGOING", "ONGOING"],
        "commodity": ["sugar"] * 4,
        "relative_month": [0, 3, 0, 3],
        "price_index": [100.0, 110.0, 100.0, 999.0],
    })
    cta_summary = pd.DataFrame({
        "event_id": ["old", "current"],
        "status": ["COMPLETED", "ONGOING"],
        "commodity": ["sugar", "sugar"],
        "speed": ["medium", "medium"],
        "horizon_months": [3, 3],
        "net_return": [0.05, 9.9],
    })

    result = make_pattern_input(evidence, price_paths, cta_summary)

    assert set(result["event_id"]) == {"old"}
    assert set(result["metric_id"]) == {
        "yield", "price_return_3m", "cta_medium_3m_net_return"
    }
