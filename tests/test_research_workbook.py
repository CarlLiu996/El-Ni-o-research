from __future__ import annotations

import json
import re
from pathlib import Path
from zipfile import ZipFile

import openpyxl
import pandas as pd
import pytest
import xlsxwriter

from scripts.build_research_workbook import (
    EXPECTED_SHEETS,
    build_research_workbook,
    validate_workbook,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def fixture_repo(tmp_path: Path, external_status: str = "PARTIAL") -> Path:
    repo = tmp_path / "repo"
    fundamentals = repo / "data" / "processed" / "fundamentals"
    base_dates = ["2024-07-31", "2025-07-31", "2026-07-31"]
    definitions = {
        "mpob_production": (base_dates, [90, 100, 110], "棕榈油产量"),
        "ffb_yield": (base_dates, [0.9, 1.0, 1.1], "鲜果串单产"),
        "mpob_exports": (base_dates, [90, 100, 110], "棕榈油出口"),
        "mpob_inventory": (base_dates, [90, 100, 110], "棕榈油库存"),
        "india_sugar_output": (base_dates, [90, 100, 110], "印度食糖产量"),
        "unica_crush": (["2024-07-15", "2025-07-15", "2026-07-15"], [180, 200, 220], "巴西中南部甘蔗压榨量"),
        "unica_sugar_mix": (["2024-07-01", "2025-07-01", "2026-07-01"], [40, 45, 50], "巴西中南部制糖比"),
        "global_sugar_total_use_source": (["2024-12-31", "2025-12-31", "2026-12-31"], [180, 190, 200], "全球食糖消费"),
        "global_sugar_ending_stocks_source": (["2024-12-31", "2025-12-31", "2026-12-31"], [50, 45, 40], "全球食糖期末库存"),
        "global_sugar_stock_use": (["2024-12-31", "2025-12-31", "2026-12-31"], [0.28, 0.24, 0.20], "全球食糖库销比"),
    }
    for name, (dates, values, label) in definitions.items():
        _write_csv(fundamentals / f"{name}.csv", [
            {
                "date": date, "value": value, "unit": "tonnes", "source": "official",
                "published_at": "2026-08-01", "indicator_name": label,
                "note": "forecast" if "global_sugar" in name else "",
            }
            for date, value in zip(dates, values)
        ])
    palm_rows = []
    for date, ratio, stock, use in zip(
        ["2024-12-31", "2025-12-31", "2026-12-31"],
        [0.20, 0.22, 0.24], [15, 16, 17], [70, 73, 75],
    ):
        palm_rows.append({
            "date": date, "value": ratio, "unit": "ratio", "source": "USDA",
            "published_at": "2026-08-01", "indicator_name": "全球棕榈油库销比",
            "ending_stocks_1000t": stock, "total_use_1000t": use, "note": "forecast",
        })
    _write_csv(fundamentals / "global_palm_stock_use.csv", palm_rows)

    climate = repo / "data" / "processed" / "climate"
    for name, column, values in (
        ("noaa_oni", "oni", [0.1, 0.4, 0.8, 1.2]),
        ("noaa_roni", "roni", [-0.1, 0.2, 0.6, 1.0]),
        ("noaa_soi", "soi", [0.5, 0.1, -0.8, -1.4]),
        ("noaa_dmi", "dmi", [-0.2, 0.0, 0.3, 0.4]),
    ):
        _write_csv(climate / f"{name}.csv", [
            {"date": date, column: value, "source": "NOAA"}
            for date, value in zip(pd.date_range("2026-03-01", periods=4, freq="MS"), values)
        ])
    _write_csv(climate / "india_monsoon_cumulative.csv", [{
        "date": "2026-08-20", "season_year": 2026, "actual_cumulative_mm": 510,
        "normal_cumulative_mm": 530, "cumulative_departure_pct": -0.0377,
        "seasonal_percentile": 0.35, "valid_locations": 3, "status": "OK",
        "source": "NASA POWER",
    }])
    _write_csv(climate / "india_monsoon_events.csv", [{
        "event": "onset", "actual_date": "2026-05-28", "normal_date": "2026-06-01",
        "departure_days": -4, "status": "OK", "source": "IMD",
    }, {
        "event": "withdrawal", "actual_date": "", "normal_date": "2026-10-15",
        "departure_days": "", "status": "PENDING", "source": "IMD",
    }])
    _write_csv(climate / "india_sugar_ndvi.csv", [{
        "product_date": "2026-08-13", "ndvi": "", "same_period_mean": "", "anomaly": "",
        "z_score": "", "seasonal_percentile": "", "valid_pixel_ratio": "",
        "status": "AUTH_REQUIRED", "source": "MOD13Q1.061",
    }])
    external_manifest = {
        "status": external_status,
        "as_of": "2026-08-25",
        "datasets": {
            "monsoon_cumulative": {"status": "OK", "source": "NASA POWER", "rows": 1},
            "imd_events": {"status": "OK", "source": "IMD", "rows": 2},
            "ndvi": {
                "status": "AUTH_REQUIRED", "source": "MOD13Q1.061", "rows": 1,
                "reason": "Earthdata authentication required",
            },
        },
    }
    climate.mkdir(parents=True, exist_ok=True)
    (climate / "external_manifest.json").write_text(
        json.dumps(external_manifest, ensure_ascii=False), encoding="utf-8"
    )

    history = repo / "data" / "processed" / "history"
    _write_csv(history / "enso_events.csv", [{
        "event_id": "2009-2010", "start_date": "2009-07-01", "peak_date": "2009-12-01",
        "end_date": "2010-04-01", "peak_oni": 1.6, "duration_seasons": 10,
        "status": "COMPLETED", "source": "NOAA CPC",
    }])
    _write_csv(history / "event_evidence_panel.csv", [{
        "event_id": "2009-2010", "status": "COMPLETED", "commodity": "sugar",
        "layer": "L1", "metric_id": "oni_peak", "value": 1.6, "unit": "degC",
        "coverage_pct": 1.0, "source": "NOAA CPC", "limitation": "描述性",
    }])
    _write_csv(history / "event_price_paths.csv", [
        {"event_id": "2009-2010", "status": "COMPLETED", "commodity": commodity,
         "date": f"2010-{month:02d}-28", "relative_month": offset,
         "price": 100 + offset, "price_index": 100 + offset, "source": "World Bank"}
        for commodity in ("palm_oil", "sugar") for month, offset in ((1, 0), (4, 3), (7, 6))
    ])
    _write_csv(history / "event_cta_paths.csv", [
        {"event_id": "2009-2010", "status": "COMPLETED", "commodity": commodity,
         "speed": speed, "date": f"2010-{month:02d}-28", "relative_month": offset,
         "net_return": 0.01, "event_equity": 1.0 + offset * 0.01, "position": 0.5}
        for commodity in ("palm_oil", "sugar") for speed in ("medium", "slow")
        for month, offset in ((1, 0), (4, 3), (7, 6))
    ])
    _write_csv(history / "event_cta_summary.csv", [
        {"event_id": "2009-2010", "status": "COMPLETED", "commodity": commodity,
         "speed": speed, "horizon_months": horizon, "net_return": 0.05,
         "max_drawdown": -0.02, "turnover": 1.0}
        for commodity in ("palm_oil", "sugar") for speed in ("medium", "slow")
        for horizon in (3, 6, 12, 18)
    ])
    _write_csv(history / "existing_daily_cta_summary.csv", [{
        "event_id": "2009-2010", "status": "COMPLETED", "symbol": "SR", "speed": "fast",
        "coverage_status": "UNAVAILABLE", "net_return": "", "max_drawdown": "",
    }])
    _write_csv(history / "historical_patterns.csv", [{
        "commodity": "sugar", "metric_id": "price_return_6m", "sample_size": 4,
        "positive_count": 3, "negative_count": 1, "flat_count": 0,
        "same_direction_share": 0.75, "median": 0.08, "q25": -0.01, "q75": 0.15,
        "pattern_strength": "STABLE_HISTORY",
    }])
    _write_csv(history / "current_event_context.csv", [])
    history.mkdir(parents=True, exist_ok=True)
    (history / "manifest.json").write_text(json.dumps({
        "as_of": "2026-08-25", "status": "OK", "artifacts": {},
        "boundary": "历史复盘为描述性旁路，不导入当前CTA门槛、仓位或置信度修正",
    }, ensure_ascii=False), encoding="utf-8")

    monitor = repo / "reports" / "monitoring" / "latest.json"
    monitor.parent.mkdir(parents=True, exist_ok=True)
    monitor.write_text(json.dumps({
        "as_of": "2026-08-25",
        "research_gate": {
            "climate": "CONFIRMED", "fundamentals": "INCOMPLETE",
            "market_cta": "NOT_CONFIRMED", "overall": "KEEP_MONITORING",
        },
        "market_confirmation": {"status": "NOT_CONFIRMED"},
        "layers": {"climate": {}, "fundamentals": {}, "market_cta": {}},
        "critical_alerts": 1, "warning_alerts": 1, "scope_gaps": ["sample scope gap"],
        "alerts": [{
            "severity": "critical", "code": "P_PRICE_STALE",
            "message": "P 期货数据已过期",
        }],
        "external_observations": external_manifest,
    }, ensure_ascii=False), encoding="utf-8")

    futures = repo / "data" / "processed" / "futures"
    signals = repo / "data" / "processed" / "signals"
    price_dates = pd.bdate_range("2026-01-01", periods=12)
    for symbol, base in (("P", 8000), ("SR", 5000)):
        _write_csv(futures / f"{symbol}_dominant_tushare.csv", [
            {
                "date": date, "symbol": symbol, "contract": f"{symbol}2701",
                "close": base + index * 10, "provider": "tushare_contract",
            }
            for index, date in enumerate(price_dates)
        ])
        for speed in ("fast", "medium", "slow"):
            _write_csv(signals / f"{symbol}_{speed}_proxy_research.csv", [
                {
                    "date": date, "close": base + index * 10, "position": 1.0,
                    "target_position": 1.0, "realized_vol": 0.2,
                    "net_return": 0.001, "equity": 1 + index * 0.01, "speed": speed,
                }
                for index, date in enumerate(price_dates)
            ])
    return repo


def sheet_text(sheet: openpyxl.worksheet.worksheet.Worksheet) -> str:
    return " ".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None)


def test_workbook_has_eight_ordered_sheets_and_l2_six_rows(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)

    assert workbook.sheetnames == EXPECTED_SHEETS
    sheet = workbook["04_L2供给基本面"]
    categories = [sheet.cell(row=row, column=2).value for row in range(11, 17)]
    assert categories == ["供给", "需求", "库存", "供给", "需求", "库存"]


def test_l3_prefers_newer_wind_active_contract_over_stale_monitor_values(tmp_path) -> None:
    repo = fixture_repo(tmp_path)
    _write_csv(repo / "data" / "processed" / "futures" / "P_active_wind.csv", [
        {
            "date": date,
            "close": close,
            "indicator_code": "M0066353",
            "indicator_name": "期货收盘价(活跃合约):棕榈油",
            "source": "大商所",
            "provider": "Wind EDB",
        }
        for date, close in (("2026-08-24", 9800), ("2026-08-25", 9900), ("2026-08-26", 9999))
    ])

    path = build_research_workbook(repo, tmp_path / "out", "2026-08-26")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    sheet = workbook["05_L3市场定价"]

    assert "Wind活跃合约" in str(sheet["B6"].value)
    assert sheet["C6"].value == 9999
    assert str(sheet["K6"].value).startswith("2026-08-26")
    assert "Wind" in str(sheet["L6"].value)


def test_workbook_contains_three_external_observations_and_statuses(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    climate_text = sheet_text(workbook["03_L1气候天气"])
    audit_text = sheet_text(workbook["07_L5数据审计"])

    for label in ("印度季风累计降雨偏差", "IMD季风开始/退出", "印度糖蔗主产区NDVI代理"):
        assert label in climate_text
    assert "AUTH_REQUIRED" in audit_text
    assert "不单独触发CTA配置动作" in climate_text


def test_partial_external_manifest_does_not_block_workbook(tmp_path) -> None:
    path = build_research_workbook(
        fixture_repo(tmp_path, external_status="PARTIAL"), tmp_path / "out", "2026-08-25"
    )
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)

    assert "AUTH_REQUIRED" in sheet_text(workbook["03_L1气候天气"])
    assert "AUTH_REQUIRED" in sheet_text(workbook["07_L5数据审计"])
    assert workbook["00_结论-CTA建议"]["A1"].value is not None


def test_history_sheet_has_price_and_cta_sparklines_and_explicit_headers(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    with ZipFile(path) as archive:
        worksheet_xml = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet")
        )
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    text = sheet_text(workbook["02_H_历次厄尔尼诺复盘"])

    assert "sparklineGroup" in worksheet_xml
    for label in ("价格路径", "CTA代理净值", "商品收益", "CTA成本后收益"):
        assert label in text


def test_leadership_workbook_explains_climate_metrics_and_withholds_incomplete_conclusion(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)

    climate_text = sheet_text(workbook["03_L1气候天气"])
    conclusion_text = sheet_text(workbook["00_结论-CTA建议"])
    for label in (
        "海洋尼诺指数", "相对海洋尼诺指数", "南方涛动指数", "印度洋偶极子指数",
        "海水是否异常变暖", "大气是否配合", "印度洋区域修正",
    ):
        assert label in climate_text
    assert "证据不完整：暂不形成CTA增配结论" in conclusion_text
    assert "CTA是按规则跟随趋势的期货策略" in conclusion_text


def _sparkline_targets(path: Path, sheet_number: int) -> list[str]:
    with ZipFile(path) as archive:
        xml = archive.read(f"xl/worksheets/sheet{sheet_number}.xml").decode("utf-8")
    return re.findall(r"<xm:sqref>([^<]+)", xml)


def _sparkline_groups(path: Path, sheet_number: int) -> list[str]:
    with ZipFile(path) as archive:
        xml = archive.read(f"xl/worksheets/sheet{sheet_number}.xml").decode("utf-8")
    return re.findall(r"<x14:sparklineGroup\b[^>]*>", xml)


def test_leadership_sparklines_are_visible_near_primary_values(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")

    history_targets = _sparkline_targets(path, 3)
    climate_targets = _sparkline_targets(path, 4)
    l2_targets = _sparkline_targets(path, 5)
    market_targets = _sparkline_targets(path, 6)
    cta_targets = _sparkline_targets(path, 7)
    assert history_targets and {target[0] for target in history_targets} <= {"C", "D"}
    assert climate_targets and {target[0] for target in climate_targets} <= {"H"}
    assert l2_targets and {target[0] for target in l2_targets} <= {"E"}
    assert market_targets and {target[0] for target in market_targets} <= {"D"}
    assert cta_targets and {target[0] for target in cta_targets} <= {"D"}


def test_l2_six_summary_rows_have_sparklines_and_direction_audit_text(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    sheet = workbook["04_L2供给基本面"]
    targets = set(_sparkline_targets(path, 5))

    assert {f"E{row}" for row in range(11, 17)} <= targets
    text = sheet_text(sheet)
    assert "方向怎么算" in text
    assert "季节分位怎么算" in text
    assert "供给/库存低位→偏紧" in text


def test_l4_explains_target_vs_executed_and_cross_commodity_direction(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    text = sheet_text(workbook["06_L4_CTA环境"])

    assert "目标信号" in text
    assert "执行仓位" in text
    assert "P与SR方向不必相同" in text
    assert "同一品种Medium与Slow同号" in text


def test_l4_labels_wind_cta_cross_check_as_exploratory_and_newer(tmp_path) -> None:
    repo = fixture_repo(tmp_path)
    for symbol, target in (("P", 1.15), ("SR", 0.0)):
        for speed in ("medium", "slow"):
            _write_csv(
                repo / "data" / "processed" / "signals" / f"{symbol}_{speed}_proxy_wind_exploratory.csv",
                [{
                    "date": "2026-08-26", "target_position": target,
                    "position": target, "equity": 1.0, "speed": speed,
                }],
            )

    path = build_research_workbook(repo, tmp_path / "out", "2026-08-26")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    text = sheet_text(workbook["06_L4_CTA环境"])

    assert "Wind 2026-08-26仅方向复核" in text
    assert "活跃合约换月规则未公开" in text
    assert "SR最新复核已转中性" in text


def test_sparklines_plot_values_from_hidden_source_columns(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")

    for sheet_number in range(3, 8):
        groups = _sparkline_groups(path, sheet_number)
        assert groups
        assert all('displayHidden="1"' in group for group in groups)


def test_history_sparklines_stay_on_their_event_rows_when_earlier_event_has_no_path(tmp_path) -> None:
    repo = fixture_repo(tmp_path)
    events_path = repo / "data" / "processed" / "history" / "enso_events.csv"
    events = pd.read_csv(events_path)
    missing = pd.DataFrame([{
        "event_id": "1951-1952", "start_date": "1951-07-01",
        "peak_date": "1951-12-01", "end_date": "1952-04-01",
        "peak_oni": 1.2, "duration_seasons": 8, "status": "COMPLETED",
        "source": "NOAA CPC",
    }])
    pd.concat([missing, events], ignore_index=True).to_csv(events_path, index=False)

    path = build_research_workbook(repo, tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    history = workbook["02_H_历次厄尔尼诺复盘"]
    targets = set(_sparkline_targets(path, 3))

    assert history["C6"].value == "未覆盖"
    assert history["D6"].value == "未覆盖"
    assert history["C7"].value == "未覆盖"
    assert history["D7"].value == "未覆盖"
    assert {"C8", "D8", "C9", "D9"} <= targets


def test_fetch_error_is_rendered_with_reason_action_and_impact(tmp_path) -> None:
    repo = fixture_repo(tmp_path)
    manifest_path = repo / "data" / "processed" / "climate" / "external_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["datasets"]["ndvi"].update({
        "status": "FETCH_ERROR",
        "reason_cn": "官方数据服务返回 HTTP 500（服务器内部错误）",
        "next_action": "改走 Earthdata 官方备用接口",
        "conclusion_impact": "缺少甘蔗长势验证，L1证据不完整",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    path = build_research_workbook(repo, tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    audit_text = sheet_text(workbook["07_L5数据审计"])
    climate_text = sheet_text(workbook["03_L1气候天气"])
    assert "获取失败（FETCH_ERROR）" in audit_text
    assert "官方数据服务返回 HTTP 500" in audit_text
    assert "改走 Earthdata 官方备用接口" in audit_text
    assert "缺少甘蔗长势验证" in audit_text
    assert "为什么没有数据" in climate_text


def test_audit_sheet_lists_all_decision_blockers_and_remediation(tmp_path) -> None:
    path = build_research_workbook(fixture_repo(tmp_path), tmp_path / "out", "2026-08-25")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    audit = workbook["07_L5数据审计"]
    audit_text = sheet_text(audit)

    assert "当前阻断结论的数据缺口" in audit_text
    assert "P 期货数据已过期" in audit_text
    assert "sample scope gap" in audit_text
    assert "刷新 Tushare fut_daily/fut_mapping" in audit_text
    alert_row = next(
        row for row in range(1, audit.max_row + 1) if audit.cell(row, 4).value == "P 期货数据已过期"
    )
    assert str(audit.cell(alert_row, 8).value).startswith("刷新 Tushare")
    merged = {str(item) for item in audit.merged_cells.ranges}
    assert f"D{alert_row}:G{alert_row}" in merged
    assert f"H{alert_row}:K{alert_row}" in merged


def test_validate_workbook_rejects_bearer_secret(tmp_path) -> None:
    path = tmp_path / "unsafe.xlsx"
    with xlsxwriter.Workbook(path) as workbook:
        for name in EXPECTED_SHEETS:
            sheet = workbook.add_worksheet(name)
            sheet.write(0, 0, "Bearer secret" if name == EXPECTED_SHEETS[0] else "safe")

    with pytest.raises(ValueError, match="Invalid workbook content"):
        validate_workbook(path)
