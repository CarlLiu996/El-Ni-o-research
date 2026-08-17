import json

import pandas as pd

from elnino_cta.debrief import write_debrief
from elnino_cta.monitoring import build_snapshot, write_snapshot


def test_monitoring_builds_gate_history_and_debrief(tmp_path):
    data = tmp_path / "data"
    for name in ("climate", "futures", "signals"):
        (data / name).mkdir(parents=True, exist_ok=True)

    oni_dates = pd.date_range("2025-10-01", periods=4, freq="MS")
    pd.DataFrame({"date": oni_dates, "oni": [0.2, 0.4, 0.6, 0.9]}).to_csv(
        data / "climate" / "noaa_oni.csv", index=False
    )
    rain_dates = pd.date_range("2025-01-01", periods=380, freq="D")
    rain = pd.concat([
        pd.DataFrame({"date": rain_dates, "region": region, "precip_mm": 2.0})
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
    assert snapshot["research_gate"]["fundamentals"] == "INCOMPLETE"
    assert snapshot["research_gate"]["market_cta"] == "CONFIRMED"
    assert snapshot["research_gate"]["overall"] == "KEEP_MONITORING"
    assert snapshot["critical_alerts"] >= 1
    assert any(alert["code"] == "FUND_MPOB_PRODUCTION" for alert in snapshot["alerts"])

    report_dir = tmp_path / "reports"
    latest, history = write_snapshot(snapshot, report_dir)
    debrief = write_debrief(snapshot, report_dir)
    assert latest.exists() and history.exists() and debrief.exists()
    assert "基本面=INCOMPLETE" in debrief.read_text(encoding="utf-8")


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
