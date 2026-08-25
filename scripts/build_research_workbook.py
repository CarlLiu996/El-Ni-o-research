from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
import xlsxwriter

from elnino_cta.fundamental_summary import build_l2_details, build_l2_summary


EXPECTED_SHEETS = [
    "00_结论-CTA建议",
    "01_总逻辑",
    "02_H_历次厄尔尼诺复盘",
    "03_L1气候天气",
    "04_L2供给基本面",
    "05_L3市场定价",
    "06_L4_CTA环境",
    "07_L5数据审计",
]
CATEGORY_CN = {"supply": "供给", "demand": "需求", "inventory": "库存"}
COMMODITY_CN = {"palm_oil": "棕榈油", "sugar": "白糖"}


def load_csv_directory(path: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for item in sorted(path.glob("*.csv")):
        try:
            frames[item.stem] = pd.read_csv(item)
        except pd.errors.EmptyDataError:
            frames[item.stem] = pd.DataFrame()
    return frames


def _read_json(path: Path, fallback: dict[str, object] | None = None) -> dict[str, object]:
    if not path.exists():
        return fallback or {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_workbook_inputs(repo_dir: Path) -> dict[str, object]:
    processed = repo_dir / "data" / "processed"
    history = load_csv_directory(processed / "history")
    fundamentals = load_csv_directory(processed / "fundamentals")
    climate = load_csv_directory(processed / "climate")
    market = load_csv_directory(processed / "market")
    signals = load_csv_directory(processed / "signals")
    monitor = _read_json(repo_dir / "reports" / "monitoring" / "latest.json")
    manifest = _read_json(processed / "history" / "manifest.json", {"as_of": monitor.get("as_of")})
    external_manifest = _read_json(
        processed / "climate" / "external_manifest.json",
        {"status": "MISSING", "datasets": {}},
    )
    empty = pd.DataFrame()
    return {
        "monitor": monitor,
        "fundamentals": fundamentals,
        "climate": climate,
        "market": market,
        "signals": signals,
        "history_manifest": manifest,
        "external_manifest": external_manifest,
        "monsoon_cumulative": climate.get("india_monsoon_cumulative", empty),
        "monsoon_events": climate.get("india_monsoon_events", empty),
        "sugar_ndvi": climate.get("india_sugar_ndvi", empty),
        "events": history.get("enso_events", empty),
        "evidence": history.get("event_evidence_panel", empty),
        "price_paths": history.get("event_price_paths", empty),
        "cta_paths": history.get("event_cta_paths", empty),
        "cta_summary": history.get("event_cta_summary", empty),
        "daily_cta_summary": history.get("existing_daily_cta_summary", empty),
        "patterns": history.get("historical_patterns", empty),
        "l2_details": build_l2_details(fundamentals),
        "l2_summary": build_l2_summary(fundamentals, manifest.get("as_of") or monitor.get("as_of")),
    }


def create_formats(workbook: xlsxwriter.Workbook) -> dict[str, Any]:
    border = {"border": 1, "border_color": "#D9E2F3", "valign": "top"}
    return {
        "title": workbook.add_format({
            "bold": True, "font_size": 18, "font_color": "#FFFFFF", "bg_color": "#17365D",
            "align": "left", "valign": "vcenter",
        }),
        "subtitle": workbook.add_format({
            "font_color": "#666666", "italic": True, "bg_color": "#D9EAF7", "text_wrap": True,
        }),
        "section": workbook.add_format({
            "bold": True, "font_size": 12, "font_color": "#17365D", "bg_color": "#D9EAF7",
            "bottom": 2, "bottom_color": "#4472C4",
        }),
        "header": workbook.add_format({
            "bold": True, "font_color": "#FFFFFF", "bg_color": "#4472C4",
            "border": 1, "border_color": "#D9E2F3", "align": "center", "valign": "vcenter",
            "text_wrap": True,
        }),
        "body": workbook.add_format({**border, "text_wrap": True}),
        "center": workbook.add_format({**border, "align": "center", "text_wrap": True}),
        "percent": workbook.add_format({**border, "num_format": "0.0%"}),
        "number": workbook.add_format({**border, "num_format": "0.00"}),
        "date": workbook.add_format({**border, "num_format": "yyyy-mm-dd"}),
        "note": workbook.add_format({
            "font_color": "#7F6000", "bg_color": "#FFF2CC", "text_wrap": True,
            "border": 1, "border_color": "#E6B800", "valign": "top",
        }),
        "good": workbook.add_format({**border, "font_color": "#006100", "bg_color": "#C6EFCE", "bold": True}),
        "warn": workbook.add_format({**border, "font_color": "#9C6500", "bg_color": "#FFEB9C", "bold": True}),
        "bad": workbook.add_format({**border, "font_color": "#9C0006", "bg_color": "#FFC7CE", "bold": True}),
        "link": workbook.add_format({**border, "font_color": "#0563C1", "underline": True, "text_wrap": True}),
    }


def write_title(sheet: Any, title: str, subtitle: str, formats: dict[str, Any]) -> None:
    sheet.merge_range(0, 0, 0, 11, title, formats["title"])
    sheet.merge_range(1, 0, 1, 11, subtitle, formats["subtitle"])
    sheet.set_row(0, 28)
    sheet.set_row(1, 30)
    sheet.hide_gridlines(2)
    sheet.set_landscape()
    sheet.set_paper(8)
    sheet.fit_to_pages(1, 0)
    sheet.set_margins(left=0.25, right=0.25, top=0.5, bottom=0.5)
    sheet.center_horizontally()


def _write_table(
    sheet: Any,
    start_row: int,
    headers: list[str],
    rows: list[list[object]],
    formats: dict[str, Any],
) -> None:
    for column, value in enumerate(headers):
        sheet.write(start_row, column, value, formats["header"])
    for offset, values in enumerate(rows, start=1):
        for column, value in enumerate(values):
            fmt = formats["body"]
            if isinstance(value, (pd.Timestamp,)):
                fmt = formats["date"]
                value = value.to_pydatetime()
            sheet.write(start_row + offset, column, "" if pd.isna(value) else value, fmt)


def _status_format(status: object, formats: dict[str, Any]) -> Any:
    text = str(status)
    if text in {"OK", "CONFIRMED", "AVAILABLE", "STABLE_HISTORY"}:
        return formats["good"]
    if text in {"AUTH_REQUIRED", "FETCH_ERROR", "PARSE_ERROR", "STALE", "INCOMPLETE"}:
        return formats["bad"]
    return formats["warn"]


def add_current_conclusion_sheet(
    workbook: xlsxwriter.Workbook,
    frames: dict[str, object],
    formats: dict[str, Any],
) -> None:
    monitor = frames["monitor"]
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[0])
    sheet.set_tab_color("#C00000")
    write_title(sheet, "00｜当前结论与 CTA 建议", "仅来自当前监控快照；历史复盘和外部观察不改写本页配置", formats)
    gate = monitor.get("research_gate", {})
    overall = gate.get("overall", "UNKNOWN")
    sheet.merge_range(3, 0, 4, 3, overall, _status_format(overall, formats))
    sheet.merge_range(
        3, 4, 4, 11,
        "当前建议：维持基准或小额分批观察；只有 L2 基本面收紧与 L3/L4 中慢速跨品种确认后，才复核主题加仓。",
        formats["note"],
    )
    headers = ["层级", "状态", "回答的问题", "当前含义"]
    questions = {
        "climate": ("L1 气候天气", "厄尔尼诺是否耦合并落到主产区"),
        "fundamentals": ("L2 供给基本面", "天气是否转化为供需库存收紧"),
        "market_cta": ("L3/L4 定价与CTA", "价格与中慢速趋势是否共同确认"),
    }
    rows = []
    for key, (label, question) in questions.items():
        status = gate.get(key, "UNKNOWN")
        rows.append([label, status, question, "已确认" if status == "CONFIRMED" else "尚不能升级配置"])
    _write_table(sheet, 6, headers, rows, formats)
    alerts: list[str] = []
    for label, value in (
        ("关键告警", monitor.get("critical_alerts", [])),
        ("一般告警", monitor.get("warning_alerts", [])),
    ):
        if isinstance(value, list):
            alerts.extend(str(item) for item in value)
        elif isinstance(value, int) and value:
            alerts.append(f"{label}：{value}项（详见 L5 数据审计与监控复盘）")
    alerts.extend(str(item) for item in monitor.get("scope_gaps", []))
    sheet.merge_range(12, 0, 12, 11, "当前限制与复核点", formats["section"])
    if alerts:
        for index, alert in enumerate(alerts[:8], start=13):
            sheet.merge_range(index, 0, index, 11, f"• {alert}", formats["note"])
    else:
        sheet.merge_range(13, 0, 13, 11, "当前快照未列出额外告警。", formats["body"])
    sheet.set_column("A:A", 20)
    sheet.set_column("B:B", 18)
    sheet.set_column("C:D", 34)
    sheet.set_column("E:L", 12)
    sheet.freeze_panes(6, 0)


def add_logic_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[1])
    sheet.set_tab_color("#4472C4")
    write_title(sheet, "01｜总逻辑", "主链逐层验证；H 历史复盘是独立旁路，只用于认识规律", formats)
    headers = ["序号", "层级", "核心问题", "关键指标", "通过后才看", "否决边界"]
    rows = [
        [1, "L1 气候天气", "ENSO是否海气耦合并落到产区", "RONI/ONI、SOI、DMI、产区雨量/土壤、三项印度外部观察", "L2", "气候异常不能替代供给兑现"],
        [2, "L2 供给基本面", "天气是否转化为供给、需求、库存变化", "棕榈油/白糖各三格，共六类结论", "L3", "预测值只作背景，不能冒充实际值评分"],
        [3, "L3 市场定价", "价格、基差、期限结构是否已经定价", "P/SR行情、基差、曲线结构", "L4", "数据过期或换月不透明则不确认"],
        [4, "L4 CTA环境", "趋势能否被系统策略捕捉", "Fast/Medium/Slow；统一20%目标波动", "L5", "必须次日执行、成本后、多品种确认"],
        [5, "L5 数据审计", "来源、时效、覆盖与方法是否可信", "发布日期、缺失、哈希、限制", "配置结论", "关键数据不完整则保持观察"],
        ["H", "历史复盘旁路", "历次事件中气候、基本面、价格、CTA如何共同演化", "统一T0窗口、价格收益、Medium/Slow CTA代理", "研究理解", "仅描述，不进入配置或置信度修正"],
    ]
    _write_table(sheet, 4, headers, rows, formats)
    sheet.merge_range(13, 0, 14, 11, "主链：L1 → L2 → L3 → L4 → L5 → 当前配置结论\n旁路：H 历史复盘 → 先总结规律，待人工确认后再讨论是否设计置信度修正。", formats["note"])
    sheet.set_column("A:A", 8)
    sheet.set_column("B:B", 20)
    sheet.set_column("C:F", 32)
    sheet.freeze_panes(5, 0)


def _lookup_metric(frame: pd.DataFrame, **conditions: object) -> pd.Series | None:
    if frame.empty:
        return None
    mask = pd.Series(True, index=frame.index)
    for column, value in conditions.items():
        if column not in frame:
            return None
        mask &= frame[column].eq(value)
    found = frame[mask]
    return found.iloc[-1] if not found.empty else None


def write_event_summary_table(
    sheet: Any,
    events: pd.DataFrame,
    evidence: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
    daily_cta_summary: pd.DataFrame,
    formats: dict[str, Any],
) -> int:
    headers = [
        "事件", "品种", "状态", "起始", "峰值ONI", "证据数", "证据覆盖",
        "商品收益3M", "商品收益6M", "商品收益12M", "商品收益18M",
        "CTA成本后收益 Medium12M", "CTA成本后收益 Slow12M", "现有日频代理",
        "价格路径", "CTA代理净值",
    ]
    rows: list[list[object]] = []
    for event in events.itertuples(index=False):
        for commodity in ("palm_oil", "sugar"):
            event_evidence = evidence[
                evidence.get("event_id", pd.Series(dtype=str)).eq(event.event_id)
                & evidence.get("commodity", pd.Series(dtype=str)).eq(commodity)
            ]
            price_group = price_paths[
                price_paths.get("event_id", pd.Series(dtype=str)).eq(event.event_id)
                & price_paths.get("commodity", pd.Series(dtype=str)).eq(commodity)
            ]
            price_index = price_group.drop_duplicates("relative_month").set_index("relative_month").get("price_index", pd.Series(dtype=float))
            def price_return(horizon: int) -> object:
                return price_index.get(horizon, pd.NA) / price_index.get(0, pd.NA) - 1 if 0 in price_index.index and horizon in price_index.index else pd.NA
            cta_values = []
            for speed in ("medium", "slow"):
                row = _lookup_metric(
                    cta_summary, event_id=event.event_id, commodity=commodity,
                    speed=speed, horizon_months=12,
                )
                cta_values.append(row.get("net_return", pd.NA) if row is not None else pd.NA)
            symbol = "P" if commodity == "palm_oil" else "SR"
            daily = daily_cta_summary[
                daily_cta_summary.get("event_id", pd.Series(dtype=str)).eq(event.event_id)
                & daily_cta_summary.get("symbol", pd.Series(dtype=str)).eq(symbol)
            ]
            if daily.empty or daily.get("coverage_status", pd.Series(dtype=str)).eq("UNAVAILABLE").all():
                daily_text = "UNAVAILABLE"
            else:
                daily_text = "; ".join(
                    f"{item.speed}:{item.coverage_status}"
                    + (f"/{float(item.net_return):.1%}" if pd.notna(item.net_return) else "")
                    for item in daily.itertuples(index=False)
                )
            rows.append([
                event.event_id, COMMODITY_CN[commodity], event.status,
                pd.to_datetime(event.start_date), event.peak_oni, len(event_evidence),
                pd.to_numeric(event_evidence.get("coverage_pct"), errors="coerce").mean() if not event_evidence.empty else pd.NA,
                *(price_return(horizon) for horizon in (3, 6, 12, 18)),
                *cta_values, daily_text,
            ])
    _write_table(sheet, 4, headers, rows, formats)
    return 5 + len(rows)


def write_event_path_data_and_sparklines(
    sheet: Any,
    price_paths: pd.DataFrame,
    cta_paths: pd.DataFrame,
    formats: dict[str, Any],
) -> None:
    source_col = 26
    source_row = 0
    visible_row = 5
    for keys, group in price_paths.groupby(["event_id", "commodity"], sort=False):
        ordered = group.sort_values("relative_month")
        values = pd.to_numeric(ordered["price_index"], errors="coerce").tolist()
        if len(values) >= 2:
            sheet.write_row(source_row, source_col, values)
            sheet.add_sparkline(visible_row, 14, {
                "range": xlsxwriter.utility.xl_range(source_row, source_col, source_row, source_col + len(values) - 1),
                "type": "line", "series_color": "#4472C4",
            })
        cta_group = cta_paths[
            cta_paths.get("event_id", pd.Series(dtype=str)).eq(keys[0])
            & cta_paths.get("commodity", pd.Series(dtype=str)).eq(keys[1])
        ]
        medium = cta_group[cta_group.get("speed", pd.Series(dtype=str)).eq("medium")]
        chosen = medium if not medium.empty else cta_group
        cta_values = pd.to_numeric(
            chosen.sort_values("relative_month").get("event_equity"), errors="coerce"
        ).dropna().tolist()
        if len(cta_values) >= 2:
            cta_source_row = source_row + 200
            sheet.write_row(cta_source_row, source_col, cta_values)
            sheet.add_sparkline(visible_row, 15, {
                "range": xlsxwriter.utility.xl_range(
                    cta_source_row, source_col, cta_source_row, source_col + len(cta_values) - 1
                ),
                "type": "line", "series_color": "#C55A11",
            })
        source_row += 1
        visible_row += 1
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})


def write_pattern_table(sheet: Any, patterns: pd.DataFrame, start_row: int, formats: dict[str, Any]) -> None:
    headers = ["品种", "指标", "样本", "正/负/平", "同向占比", "中位数", "Q25", "Q75", "规律强度", "解释边界"]
    rows = []
    for item in patterns.itertuples(index=False):
        rows.append([
            COMMODITY_CN.get(item.commodity, item.commodity), item.metric_id, item.sample_size,
            f"{item.positive_count}/{item.negative_count}/{item.flat_count}",
            item.same_direction_share, item.median, item.q25, item.q75,
            item.pattern_strength, "仅描述已完成事件；不生成配置或置信度修正",
        ])
    _write_table(sheet, start_row, headers, rows, formats)


def add_history_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[2])
    sheet.set_tab_color("#8064A2")
    write_title(sheet, "02｜历次厄尔尼诺复盘", "独立研究旁路；同时复盘气候/基本面、品种价格和 CTA 代理，不进入当前配置结论", formats)
    end_row = write_event_summary_table(
        sheet, frames["events"], frames["evidence"], frames["price_paths"],
        frames["cta_summary"], frames["daily_cta_summary"], formats,
    )
    write_event_path_data_and_sparklines(sheet, frames["price_paths"], frames["cta_paths"], formats)
    pattern_row = max(end_row + 3, 11)
    sheet.merge_range(pattern_row, 0, pattern_row, 11, "历史规律统计", formats["section"])
    write_pattern_table(sheet, frames["patterns"], pattern_row + 1, formats)
    sheet.set_column("A:A", 16)
    sheet.set_column("B:B", 12)
    sheet.set_column("C:N", 15)
    sheet.set_column("O:O", 18)
    sheet.freeze_panes(5, 0)


def _last_record(frame: pd.DataFrame) -> pd.Series | None:
    return frame.iloc[-1] if not frame.empty else None


def write_external_observation_rows(
    sheet: Any,
    frames: dict[str, object],
    start_row: int,
    formats: dict[str, Any],
) -> None:
    headers = ["外部观测", "当前状态", "当前值/事件", "同期正常/基准", "位置含义", "数据截至", "来源", "配置边界"]
    monsoon = _last_record(frames["monsoon_cumulative"])
    events = frames["monsoon_events"]
    ndvi = _last_record(frames["sugar_ndvi"])
    datasets = frames["external_manifest"].get("datasets", {})
    monsoon_meta = datasets.get("monsoon_cumulative", {})
    imd_meta = datasets.get("imd_events", {})
    ndvi_meta = datasets.get("ndvi", {})
    monsoon_status = monsoon_meta.get(
        "status", monsoon.get("status", "MISSING") if monsoon is not None else "MISSING"
    )
    monsoon_value = (
        f"累计{monsoon.get('actual_cumulative_mm', '')}mm；偏差{float(monsoon.get('cumulative_departure_pct')):.1%}"
        if monsoon is not None and monsoon_status == "OK" and pd.notna(monsoon.get("cumulative_departure_pct"))
        else str(monsoon.get("reason", monsoon_status)) if monsoon is not None else "无数据"
    )
    event_status = imd_meta.get(
        "status",
        "OK" if not events.empty and events.get("status", pd.Series(dtype=str)).eq("OK").any() else "PENDING",
    )
    if event_status in {"OK", "NOT_ANNOUNCED"}:
        event_text = "; ".join(
            f"{getattr(item, 'event', getattr(item, 'event_type', ''))}:"
            f"{getattr(item, 'actual_date', getattr(item, 'official_date', '')) or getattr(item, 'status', '')}"
            for item in events.itertuples(index=False)
        ) or event_status
    else:
        event_text = imd_meta.get("reason", event_status)
    ndvi_status = ndvi_meta.get(
        "status", ndvi.get("status", "MISSING") if ndvi is not None else "MISSING"
    )
    ndvi_value = (
        f"NDVI {ndvi.get('ndvi')}；z={ndvi.get('z_score')}；有效像元{float(ndvi.get('valid_pixel_ratio')):.1%}"
        if ndvi is not None and ndvi_status == "OK" and pd.notna(ndvi.get("ndvi"))
        else str(ndvi.get("reason", ndvi_status)) if ndvi is not None else "无数据"
    )
    rows = [
        ["印度季风累计降雨偏差", monsoon_status, monsoon_value,
         monsoon.get("normal_cumulative_mm", "") if monsoon is not None else "",
         "负偏差代表季风累计偏弱；需结合空间覆盖与进度", monsoon.get("date", "") if monsoon is not None else "",
         monsoon_meta.get("source", monsoon.get("source", "") if monsoon is not None else ""), "不单独触发CTA配置动作"],
        ["IMD季风开始/退出", event_status, event_text, "IMD正常开始/退出日期",
         "偏早/偏晚影响有效雨季长度，但宣告具有事后确认属性",
         (
             events.get("actual_date", events.get("official_date", pd.Series(dtype=str))).dropna().max()
             if not events.empty else ""
         ),
         imd_meta.get("source", "IMD"), "不单独触发CTA配置动作"],
        ["印度糖蔗主产区NDVI代理", ndvi_status, ndvi_value,
         ndvi.get("same_period_mean", "") if ndvi is not None else "",
         "低分位/负z值代表长势偏弱；主产区代表点邻域不是糖蔗专属掩膜",
         ndvi.get("product_date", "") if ndvi is not None else "",
         ndvi_meta.get("source", ndvi.get("source", "") if ndvi is not None else ""), "不单独触发CTA配置动作"],
    ]
    _write_table(sheet, start_row, headers, rows, formats)
    for offset, row in enumerate(rows, start=1):
        sheet.write(start_row + offset, 1, row[1], _status_format(row[1], formats))


def add_climate_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[3])
    sheet.set_tab_color("#00A6A6")
    write_title(sheet, "03｜L1 气候天气", "先确认海温与大气耦合，再确认异常是否落到产区；外部观测只补证据", formats)
    climate = frames["monitor"].get("layers", {}).get("climate", {})
    headers = ["指标", "日期", "当前值", "状态/方向", "三个月变化", "含义", "限制"]
    rows = []
    for key, label in (("oni", "ONI"), ("roni", "RONI"), ("soi", "SOI"), ("dmi", "DMI")):
        item = climate.get(key, {})
        rows.append([
            label, item.get("date"), item.get(key), item.get("state", item.get("phase", "UNKNOWN")),
            item.get("change_3m"), "海温/大气耦合与IOD修正", "单一指数不能直接推导作物供给",
        ])
    _write_table(sheet, 4, headers, rows, formats)
    sheet.merge_range(10, 0, 10, 11, "三项持续外部观测", formats["section"])
    write_external_observation_rows(sheet, frames, 11, formats)
    sheet.set_column("A:A", 28)
    sheet.set_column("B:B", 18)
    sheet.set_column("C:H", 28)
    sheet.freeze_panes(5, 0)


def write_l2_summary_rows(sheet: Any, summary: pd.DataFrame, start_row: int, formats: dict[str, Any]) -> None:
    headers = ["品种", "类别", "主指标", "当前值", "单位", "实际/预测", "季节分位", "方向", "数据截至", "背景驱动", "含义"]
    rows = []
    for item in summary.itertuples(index=False):
        rows.append([
            COMMODITY_CN[item.commodity], CATEGORY_CN[item.category], item.primary_metric,
            item.primary_value, item.unit, item.value_type, item.seasonal_percentile,
            item.direction, item.date, item.drivers,
            "TIGHT=偏紧；LOOSE=偏松；预测评分或数据缺失则INSUFFICIENT",
        ])
    _write_table(sheet, start_row, headers, rows, formats)
    for offset, row in enumerate(rows, start=1):
        sheet.write(start_row + offset, 7, row[7], _status_format(row[7], formats))


def write_l2_detail_rows(
    sheet: Any,
    details: pd.DataFrame,
    fundamental_history: dict[str, pd.DataFrame],
    start_row: int,
    formats: dict[str, Any],
) -> None:
    headers = ["指标", "名称", "含义", "当前值", "单位", "观测日", "发布日期", "实际/预测", "季节分位", "当前位置含义", "来源", "局限", "迷你趋势"]
    rows = []
    for item in details.itertuples(index=False):
        rows.append([
            item.metric_id, item.label, item.meaning, item.value, item.unit, item.date,
            item.published_at, item.value_type, item.seasonal_percentile,
            item.position_meaning, item.source, item.limitation, "",
        ])
    _write_table(sheet, start_row, headers, rows, formats)
    source_col = 52
    for index, item in enumerate(details.itertuples(index=False), start=1):
        values = _l2_history_values(item.metric_id, fundamental_history)
        if len(values) < 2:
            continue
        source_row = index - 1
        sheet.write_row(source_row, source_col, values)
        sheet.add_sparkline(start_row + index, 12, {
            "range": xlsxwriter.utility.xl_range(source_row, source_col, source_row, source_col + len(values) - 1),
            "type": "line", "series_color": "#70AD47",
        })
    sheet.set_column(source_col, source_col + 300, None, None, {"hidden": True})


def _l2_history_values(
    metric_id: str,
    fundamental_history: dict[str, pd.DataFrame],
) -> list[float]:
    if metric_id in {"global_palm_total_use", "global_palm_ending_stocks"}:
        frame = fundamental_history.get("global_palm_stock_use", pd.DataFrame()).copy()
        column = (
            "total_use_1000t" if metric_id == "global_palm_total_use"
            else "ending_stocks_1000t"
        )
        if column not in frame:
            return []
        return pd.to_numeric(frame.sort_values("date")[column], errors="coerce").dropna().tolist()
    if metric_id == "brazil_cane_for_sugar_proxy":
        crush = fundamental_history.get("unica_crush", pd.DataFrame()).copy()
        mix = fundamental_history.get("unica_sugar_mix", pd.DataFrame()).copy()
        if crush.empty or mix.empty:
            return []
        for frame in (crush, mix):
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame.sort_values("date", inplace=True)
        mix = mix.rename(columns={"date": "mix_date"})
        aligned = pd.merge_asof(
            crush, mix, left_on="date", right_on="mix_date",
            direction="backward", suffixes=("_crush", "_mix")
        )
        proxy = pd.to_numeric(aligned["value_crush"], errors="coerce") * pd.to_numeric(
            aligned["value_mix"], errors="coerce"
        ) / 100.0
        return proxy.dropna().tolist()
    frame = fundamental_history.get(metric_id)
    if frame is None or "value" not in frame:
        return []
    return pd.to_numeric(frame.sort_values("date")["value"], errors="coerce").dropna().tolist()


def add_l2_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[4])
    sheet.set_tab_color("#70AD47")
    write_title(sheet, "04｜L2 供给基本面", "顶部严格六格：棕榈油/白糖 × 供给/需求/库存；下方为全部驱动和审计信息", formats)
    sheet.merge_range(3, 0, 3, 11, "判断顺序：实际主指标 → 同季节分位 → 方向；预测只作背景，白糖供给需印度产量与巴西制糖甘蔗代理共同验证。", formats["note"])
    sheet.merge_range(8, 0, 8, 11, "六格结论", formats["section"])
    write_l2_summary_rows(sheet, frames["l2_summary"], 9, formats)
    sheet.merge_range(17, 0, 17, 11, "指标明细", formats["section"])
    write_l2_detail_rows(sheet, frames["l2_details"], frames["fundamentals"], 18, formats)
    sheet.set_column("A:B", 14)
    sheet.set_column("C:C", 28)
    sheet.set_column("D:I", 16)
    sheet.set_column("J:L", 30)
    sheet.set_column("M:M", 14)
    sheet.freeze_panes(10, 0)


def _flatten(prefix: str, value: object, output: list[tuple[str, object]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, output)
    elif not isinstance(value, list):
        output.append((prefix, value))


def add_market_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[5])
    sheet.set_tab_color("#ED7D31")
    write_title(sheet, "05｜L3 市场定价", "验证价格、基差与期限结构是否已经共同定价；不以主题叙事代替行情", formats)
    market = frames["monitor"].get("layers", {}).get("market_cta", {})
    items: list[tuple[str, object]] = []
    _flatten("market_cta", market, items)
    rows = [[key, value, "当前监控快照", "行情过期、连续合约换月不透明时不确认"] for key, value in items] or [["状态", "NO_DATA", "当前监控快照", "需刷新 P/SR 行情"]]
    _write_table(sheet, 4, ["指标", "当前值", "来源", "限制"], rows, formats)
    sheet.set_column("A:A", 36)
    sheet.set_column("B:D", 34)
    sheet.freeze_panes(5, 0)


def add_cta_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[6])
    sheet.set_tab_color("#FFC000")
    write_title(sheet, "06｜L4 CTA 环境", "Fast / Medium / Slow 均统一 20% 年化目标波动；信号次日执行、杠杆上限2倍、成本2bp", formats)
    headers = ["速度", "方向回看", "波动率窗口", "用途", "确认边界"]
    rows = [
        ["Fast", "20/60交易日", "20交易日", "快速风险变化；噪声较高", "不单独触发主题配置"],
        ["Medium", "60/120交易日", "60交易日", "中期趋势确认", "需与Slow及跨品种证据共同判断"],
        ["Slow", "120/250交易日", "120交易日", "长期趋势确认", "起效慢，但更接近配置视角"],
    ]
    _write_table(sheet, 4, headers, rows, formats)
    sheet.merge_range(10, 0, 10, 11, "当前信号文件", formats["section"])
    signal_rows = []
    for name, frame in frames["signals"].items():
        latest = frame.iloc[-1] if not frame.empty else pd.Series()
        signal_rows.append([
            name, len(frame), latest.get("date", ""), latest.get("position", ""),
            latest.get("net_return", ""), "研究代理；须审计换月和成本",
        ])
    _write_table(sheet, 11, ["文件", "行数", "截至", "仓位", "最近净收益", "限制"], signal_rows, formats)
    sheet.set_column("A:A", 32)
    sheet.set_column("B:F", 24)
    sheet.freeze_panes(5, 0)


def add_audit_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[7])
    sheet.set_tab_color("#7F7F7F")
    write_title(sheet, "07｜L5 数据审计", "逐项披露来源、状态、覆盖、刷新时间和是否进入结论；Token 不写入工作簿", formats)
    external = frames["external_manifest"]
    sheet.write(3, 0, "外部观测总状态", formats["header"])
    overall = external.get("overall", external.get("status", "MISSING"))
    sheet.write(3, 1, overall, _status_format(overall, formats))
    headers = ["数据集", "状态", "行数", "数据截至/发布时间", "刷新时间", "覆盖", "来源", "原因/限制", "是否参与当前结论"]
    rows = []
    for name, item in external.get("datasets", {}).items():
        rows.append([
            name, item.get("status", "UNKNOWN"), item.get("rows", ""),
            item.get("max_date", item.get("published_at", "")),
            item.get("refreshed_at", external.get("downloaded_at", "")),
            item.get("coverage_pct", ""), item.get("source", ""), item.get("reason", ""), "否；仅外部补充观察",
        ])
    _write_table(sheet, 5, headers, rows, formats)
    for offset, row in enumerate(rows, start=1):
        sheet.write(5 + offset, 1, row[1], _status_format(row[1], formats))
    history_row = 8 + len(rows)
    sheet.merge_range(history_row, 0, history_row, 11, "历史复盘审计", formats["section"])
    manifest = frames["history_manifest"]
    sheet.write(history_row + 1, 0, "复盘截至", formats["body"])
    sheet.write(history_row + 1, 1, manifest.get("as_of", ""), formats["body"])
    sheet.write(history_row + 2, 0, "逻辑边界", formats["body"])
    sheet.merge_range(history_row + 2, 1, history_row + 2, 8, manifest.get("boundary", "仅描述，不进入当前配置"), formats["note"])
    sheet.set_column("A:A", 32)
    sheet.set_column("B:C", 18)
    sheet.set_column("D:I", 28)
    sheet.freeze_panes(6, 0)


def validate_workbook(path: Path) -> None:
    forbidden = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "Authorization", "Bearer ")
    token_pattern = re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    if workbook.sheetnames != EXPECTED_SHEETS:
        raise ValueError(f"Unexpected sheets: {workbook.sheetnames}")
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                text = "" if cell.value is None else str(cell.value)
                if any(item in text for item in forbidden) or token_pattern.search(text):
                    raise ValueError(f"Invalid workbook content at {sheet.title}!{cell.coordinate}")


def build_research_workbook(repo_dir: Path, output_dir: Path, as_of: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"厄尔尼诺农产品CTA研究框架_八页复盘版_{as_of.replace('-', '')}.xlsx"
    frames = load_workbook_inputs(repo_dir)
    with xlsxwriter.Workbook(str(output_path)) as workbook:
        workbook.set_properties({
            "title": "厄尔尼诺农产品CTA研究框架",
            "subject": "当前监控与独立历史复盘",
            "author": "El Nino CTA Research",
            "comments": "历史规律暂不进入CTA配置修正",
        })
        formats = create_formats(workbook)
        add_current_conclusion_sheet(workbook, frames, formats)
        add_logic_sheet(workbook, frames, formats)
        add_history_sheet(workbook, frames, formats)
        add_climate_sheet(workbook, frames, formats)
        add_l2_sheet(workbook, frames, formats)
        add_market_sheet(workbook, frames, formats)
        add_cta_sheet(workbook, frames, formats)
        add_audit_sheet(workbook, frames, formats)
    validate_workbook(output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the eight-sheet ENSO CTA research workbook")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output", default="research")
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    print(build_research_workbook(Path(args.repo), Path(args.output), args.as_of))


if __name__ == "__main__":
    main()
