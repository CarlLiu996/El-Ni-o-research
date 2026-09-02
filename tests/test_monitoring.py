import json

import pandas as pd
import pytest

from elnino_cta.debrief import write_debrief
from elnino_cta.monitoring import assess_cta_confirmation, build_snapshot, write_snapshot


def test_cta_confirmation_requires_medium_slow_same_sign_but_not_cross_symbol_same_sign():
    cta = {
        "P": {
            "medium": {"position": 1.0, "net_return_20d": 0.02, "position_persistence_days": 21},
            "slow": {"position": 0.8, "net_return_20d": 0.01, "position_persistence_days": 30},
        },
        "SR": {
            "medium": {"position": -1.0, "net_return_20d": 0.03, "position_persistence_days": 22},
            "slow": {"position": -0.7, "net_return_20d": 0.02, "position_persistence_days": 40},
        },
    }

    result = assess_cta_confirmation(cta, trend_confirmation_days=20)

    assert result["status"] == "CONFIRMED"
    assert result["symbols_confirmed"] == 2
    assert result["details"]["P"]["direction"] == "LONG"
    assert result["details"]["SR"]["direction"] == "SHORT"


def test_cta_confirmation_rejects_opposite_medium_and_slow_positions():
    cta = {
        "P": {
            "medium": {"position": 1.0, "net_return_20d": 0.02, "position_persistence_days": 21},
            "slow": {"position": -0.8, "net_return_20d": 0.01, "position_persistence_days": 30},
        }
    }

    result = assess_cta_confirmation(cta, trend_confirmation_days=20)

    assert result["status"] == "NOT_CONFIRMED"
    assert result["details"]["P"]["same_sign"] is False


def test_monitoring_builds_gate_history_and_debrief(tmp_path):
    data = tmp_path / "data"
    for name in ("climate", "futures", "signals"):
        (data / name).mkdir(parents=True, exist_ok=True)

    oni_dates = pd.date_range("2025-10-01", periods=4, freq="MS")
    pd.DataFrame({"date": oni_dates, "oni": [0.2, 0.4, 0.6, 0.9]}).to_csv(
        data / "climate" / "noaa_oni.csv", index=False
    )
    pd.DataFrame({"date": oni_dates, "roni": [0.1, 0.3, 0.5, 0.8]}).to_csv(
        data / "climate" / "noaa_roni.csv", index=False
    )
    pd.DataFrame({"date": oni_dates, "soi": [0.5, 0.1, -0.8, -1.5]}).to_csv(
        data / "climate" / "noaa_soi.csv", index=False
    )
    pd.DataFrame({"date": oni_dates, "dmi": [0.1, 0.2, 0.3, 0.4]}).to_csv(
        data / "climate" / "noaa_dmi.csv", index=False
    )
    rain_dates = pd.date_range("2025-01-01", periods=380, freq="D")
    rain = pd.concat([
        pd.DataFrame({
            "date": rain_dates,
            "region": region,
            "precip_mm": 2.0,
            "root_zone_soil_wetness": 0.6,
        })
        for region in ("india_sugar", "malaysia_palm")
    ])
    rain.to_csv(data / "climate" / "nasa_power_precipitation.csv", index=False)

    dates = pd.bdate_range("2025-01-01", periods=300)
    for symbol, exchange, base in (("P", "DCE", 8000), ("SR", "ZCE", 5000)):
        dominant = pd.DataFrame({
            "date": dates,
            "contract": f"{symbol}2605.{exchange}",
            "close": [base + index for index in range(len(dates))],
        })
        dominant.to_csv(data / "futures" / f"{symbol}_dominant_tushare.csv", index=False)
        pd.DataFrame({
            "date": dates,
            "close": [base + 1000 + index for index in range(len(dates))],
            "indicator_code": "WIND_TEST",
            "indicator_name": f"{symbol} active contract",
            "source": "exchange",
            "provider": "Wind EDB",
        }).to_csv(data / "futures" / f"{symbol}_active_wind.csv", index=False)
        pd.DataFrame({
            "date": [dates[-1], dates[-1]],
            "contract": [f"{symbol}2605.{exchange}", f"{symbol}2609.{exchange}"],
            "close": [base + 300, base + 320],
            "open_interest": [1000, 900],
        }).to_csv(data / "futures" / f"{symbol}_contracts_tushare.csv", index=False)
        for speed in ("fast", "medium", "slow"):
            signal = pd.DataFrame({
                "date": dates,
                "target_position": 1.0,
                "position": 1.0,
                "realized_vol": 0.1,
                "equity": 1.0 + pd.Series(range(len(dates))) * 0.001,
                "net_return": 0.001,
                "turnover": 0.0,
            })
            signal.to_csv(data / "signals" / f"{symbol}_{speed}_proxy_research.csv", index=False)

    config = tmp_path / "monitoring.json"
    config.write_text(json.dumps({
        "freshness_days": {"futures": 4, "precipitation": 7, "oni": 95, "fundamentals": 45},
        "dry_day_threshold_mm": 1.0,
        "rainfall_warning_pct": -0.1,
        "trend_confirmation_days": 20,
        "required_fundamentals": [
            {"dataset": "mpob_production", "label": "MPOB 产量", "symbol": "P", "frequency": "monthly"}
        ],
    }), encoding="utf-8")
    as_of = (dates[-1] + pd.Timedelta(days=1)).date().isoformat()
    snapshot = build_snapshot(data, config, as_of)

    assert snapshot["research_gate"]["climate"] == "CONFIRMED"
    assert snapshot["layers"]["climate"]["roni"]["roni"] == 0.8
    assert snapshot["layers"]["climate"]["soi"]["soi"] == -1.5
    assert snapshot["layers"]["climate"]["dmi"]["dmi"] == 0.4
    assert snapshot["layers"]["climate"]["rainfall"]["regions"]["india_sugar"]["root_zone_soil_wetness"] == pytest.approx(0.6)
    assert snapshot["research_gate"]["fundamentals"] == "INCOMPLETE"
    assert snapshot["research_gate"]["market_cta"] == "CONFIRMED"
    assert snapshot["layers"]["market"]["P"]["provider"] == "Wind EDB"
    assert snapshot["layers"]["market"]["P"]["close"] == 8000 + 1000 + len(dates) - 1
    assert snapshot["research_gate"]["overall"] == "KEEP_MONITORING"
    assert snapshot["critical_alerts"] >= 1
    assert any(alert["code"] == "FUND_MPOB_PRODUCTION" for alert in snapshot["alerts"])

    original_gate = snapshot["research_gate"]
    original_market_confirmation = snapshot["market_confirmation"]
    original_cta = snapshot["layers"]["cta"]
    pd.DataFrame({
        "date": ["2025-09-30"],
        "season_year": [2025],
        "season_day": [122],
        "current_cumulative_mm": [870.0],
        "climatology_cumulative_mm": [900.0],
        "cumulative_departure_pct": [-0.0333],
        "historical_percentile": [0.45],
        "valid_locations": [3],
        "required_locations": [2],
        "status": ["OK"],
        "data_as_of": ["2025-09-30"],
        "published_at": [pd.NA],
        "downloaded_at": ["2026-01-01T00:00:00Z"],
        "source": ["NASA POWER"],
    }).to_csv(data / "climate" / "india_monsoon_cumulative.csv", index=False)
    pd.DataFrame({
        "season_year": [2025],
        "event_type": ["ONSET_KERALA"],
        "official_date": ["2025-05-24"],
        "normal_date": ["2025-06-01"],
        "departure_days": [-8],
        "announcement_date": ["2025-05-24"],
        "source_url": ["https://mausam.imd.gov.in/onset.pdf"],
        "status": ["OK"],
    }).to_csv(data / "climate" / "india_monsoon_events.csv", index=False)
    pd.DataFrame({
        "product_date": ["2026-01-01"],
        "ndvi": [0.62],
        "same_period_difference": [0.04],
        "z_score": [1.1],
        "historical_percentile": [0.85],
        "valid_pixel_ratio": [0.8],
        "region_count": [3],
        "status": ["OK"],
        "published_at": ["2026-01-10"],
        "downloaded_at": ["2026-01-11T00:00:00Z"],
        "source": ["MOD13Q1.061"],
    }).to_csv(data / "climate" / "india_sugar_ndvi.csv", index=False)
    (data / "climate" / "external_manifest.json").write_text(json.dumps({
        "overall": "OK",
        "as_of": as_of,
        "refreshed_at": "2026-01-11T00:00:00Z",
        "datasets": {
            "monsoon_cumulative": {"status": "OK"},
            "imd_events": {"status": "OK"},
            "ndvi": {"status": "OK"},
        },
    }), encoding="utf-8")

    with_external = build_snapshot(data, config, as_of)
    assert with_external["research_gate"] == original_gate
    assert with_external["market_confirmation"] == original_market_confirmation
    assert with_external["layers"]["cta"] == original_cta
    assert set(with_external["external_observations"]) == {
        "monsoon_cumulative", "imd_events", "ndvi"
    }
    assert with_external["external_observations"]["ndvi"]["value"] == pytest.approx(0.62)
    assert with_external["layers"]["climate"]["external_observations"] == with_external["external_observations"]

    report_dir = tmp_path / "reports"
    latest, history = write_snapshot(snapshot, report_dir)
    debrief = write_debrief(snapshot, report_dir)
    assert latest.exists() and history.exists() and debrief.exists()
    assert "基本面=INCOMPLETE" in debrief.read_text(encoding="utf-8")
    assert "RONI 0.80" in debrief.read_text(encoding="utf-8")


def test_fundamental_freshness_uses_publication_date_and_per_dataset_threshold(tmp_path):
    from elnino_cta.monitoring import _fundamental_status

    data = tmp_path / "data"
    fundamentals = data / "fundamentals"
    fundamentals.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2025-12-31"],
        "value": [0.24],
        "published_at": ["2026-06-02"],
    }).to_csv(fundamentals / "global_sugar_stock_use.csv", index=False)
    config = {
        "freshness_days": {"fundamentals": 45},
        "required_fundamentals": [{
            "dataset": "global_sugar_stock_use",
            "label": "全球糖库销比",
            "symbol": "SR",
            "frequency": "yearly",
            "max_stale_days": 180,
        }],
    }
    status = _fundamental_status(data, config, pd.Timestamp("2026-08-18"))
    assert status["status"] == "OK"
    assert status["items"][0]["last_date"] == "2025-12-31"
    assert status["items"][0]["last_published_at"] == "2026-06-02"
    assert status["items"][0]["freshness_basis"] == "published_at"
