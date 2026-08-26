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
from elnino_cta.signals import build_cta_proxy


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
SYMBOL_CN = {"P": "棕榈油 P", "SR": "白糖 SR"}
STATUS_CN = {
    "OK": "可用（OK）",
    "FRESH": "最新（FRESH）",
    "CONFIRMED": "已确认（CONFIRMED）",
    "KEEP_MONITORING": "继续观察（KEEP_MONITORING）",
    "NOT_CONFIRMED": "尚未确认（NOT_CONFIRMED）",
    "INCOMPLETE": "数据不完整（INCOMPLETE）",
    "PARTIAL": "部分可用（PARTIAL）",
    "STALE": "数据过期（STALE）",
    "FETCH_ERROR": "获取失败（FETCH_ERROR）",
    "AUTH_REQUIRED": "缺少授权（AUTH_REQUIRED）",
    "NOT_PUBLISHED": "官方尚未发布（NOT_PUBLISHED）",
    "NOT_ANNOUNCED": "官方尚未宣布（NOT_ANNOUNCED）",
    "LOW_COVERAGE": "覆盖不足（LOW_COVERAGE）",
    "INSUFFICIENT_COVERAGE": "覆盖不足（INSUFFICIENT_COVERAGE）",
    "REVIEW_REQUIRED": "需要人工复核（REVIEW_REQUIRED）",
    "MISSING": "数据缺失（MISSING）",
    "UNKNOWN": "状态未知（UNKNOWN）",
}
METRIC_CN = {
    "mpob_production": "马来西亚棕榈油产量",
    "ffb_yield": "马来西亚鲜果串（FFB）单产",
    "mpob_exports": "马来西亚棕榈油出口",
    "mpob_inventory": "马来西亚棕榈油库存",
    "global_palm_total_use": "全球棕榈油消费量",
    "global_palm_ending_stocks": "全球棕榈油期末库存",
    "global_palm_stock_use": "全球棕榈油库销比",
    "india_sugar_output": "印度食糖产量",
    "unica_crush": "巴西中南部甘蔗压榨量",
    "unica_sugar_mix": "巴西中南部制糖比",
    "brazil_cane_for_sugar_proxy": "巴西用于制糖的甘蔗量代理",
    "global_sugar_total_use_source": "全球食糖消费量",
    "global_sugar_ending_stocks_source": "全球食糖期末库存",
    "global_sugar_stock_use": "全球食糖库销比",
}
EXTERNAL_DATASET_CN = {
    "monsoon_cumulative": "印度季风累计降雨偏差",
    "imd_events": "IMD季风开始/退出",
    "ndvi": "印度糖蔗主产区NDVI代理",
}
ALERT_ACTION_CN = {
    "P_PRICE_STALE": "刷新 Tushare fut_daily/fut_mapping；接口权限缺失时配置后重跑",
    "SR_PRICE_STALE": "刷新 Tushare fut_daily/fut_mapping；接口权限缺失时配置后重跑",
    "FUND_UNICA_SUGAR_MIX": "刷新 Wind EDB W5495204；若官方尚未更新则保留过期标记",
    "BRAZIL_CS_SUGAR_DRY": "继续刷新 NASA POWER，并等待 UNICA 压榨与制糖比验证",
}
CLIMATE_GUIDE = {
    "oni": {
        "label": "ONI｜海洋尼诺指数\nOceanic Niño Index",
        "question": "太平洋海水是否异常变暖",
        "threshold": "≥+0.5℃偏厄尔尼诺；≤-0.5℃偏拉尼娜",
        "meaning": "Niño 3.4区三个月平均海温距平；正式事件还需持续性确认",
    },
    "roni": {
        "label": "RONI｜相对海洋尼诺指数\nRelative Oceanic Niño Index",
        "question": "扣除整体海洋变暖后，异常是否仍存在",
        "threshold": "≥+0.5偏厄尔尼诺；≤-0.5偏拉尼娜",
        "meaning": "在ONI基础上扣除热带海洋整体变暖，更强调真正的东西向温差",
    },
    "soi": {
        "label": "SOI｜南方涛动指数\nSouthern Oscillation Index",
        "question": "大气是否配合海温异常",
        "threshold": "持续为负支持厄尔尼诺；持续为正支持拉尼娜",
        "meaning": "塔希提与达尔文气压差的标准化指标；负值表示厄尔尼诺式大气响应",
    },
    "dmi": {
        "label": "DMI｜印度洋偶极子指数\nDipole Mode Index",
        "question": "印度洋区域修正是否放大或抵消影响",
        "threshold": "≥+0.5为正IOD；≤-0.5为负IOD",
        "meaning": "西印度洋减东印度洋海温差；不是ENSO本身，用于修正印度和印尼影响落区",
    },
}


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
    futures = load_csv_directory(processed / "futures")
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
        "futures": futures,
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
    column_formats: dict[int, Any] | None = None,
) -> None:
    for column, value in enumerate(headers):
        sheet.write(start_row, column, value, formats["header"])
    for offset, values in enumerate(rows, start=1):
        for column, value in enumerate(values):
            fmt = (column_formats or {}).get(column, formats["body"])
            if isinstance(value, (pd.Timestamp,)) and column not in (column_formats or {}):
                fmt = formats["date"]
                value = value.to_pydatetime()
            sheet.write(start_row + offset, column, "" if pd.isna(value) else value, fmt)


def _status_text(status: object) -> str:
    raw = str(status or "UNKNOWN")
    return STATUS_CN.get(raw, raw)


def _direction_text(value: object) -> str:
    mapping = {
        "TIGHT": "偏紧：通常对价格偏多",
        "LOOSE": "偏松：通常对价格偏空",
        "NEUTRAL": "中性：没有明显方向",
        "INSUFFICIENT": "证据不足：暂不判断",
    }
    return mapping.get(str(value), str(value))


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
    as_of = monitor.get("as_of", "未知")
    write_title(
        sheet,
        "00｜领导摘要：现在能否据此增配 CTA？",
        f"数据评估日：{as_of}｜CTA是按规则跟随趋势的期货策略，不等于直接押注农产品上涨",
        formats,
    )
    gate = monitor.get("research_gate", {})
    overall = gate.get("overall", "UNKNOWN")
    critical_count = int(monitor.get("critical_alerts", 0) or 0)
    evidence_complete = (
        gate.get("climate") == "CONFIRMED"
        and gate.get("fundamentals") == "CONFIRMED"
        and gate.get("market_cta") == "CONFIRMED"
        and critical_count == 0
    )
    headline = (
        "证据完整：可以进入配置复核"
        if evidence_complete
        else "证据不完整：暂不形成CTA增配结论"
    )
    sheet.merge_range(3, 0, 4, 3, _status_text(overall), _status_format(overall, formats))
    sheet.merge_range(
        3, 4, 4, 11,
        headline + "。只有L2供需库存收紧、L3价格定价和L4中慢速趋势共同确认，才讨论增配。",
        formats["note"],
    )
    headers = ["层级", "状态", "用大白话回答的问题", "领导现在怎么读"]
    questions = {
        "climate": ("L1 气候天气", "这次厄尔尼诺是否真实，并已影响主产区天气"),
        "fundamentals": ("L2 供给基本面", "天气影响是否已经变成减产、需求变化或库存收紧"),
        "market_cta": ("L3/L4 定价与CTA", "市场价格和趋势策略是否已经共同确认这个故事"),
    }
    rows = []
    for key, (label, question) in questions.items():
        status = gate.get(key, "UNKNOWN")
        rows.append([
            label,
            _status_text(status),
            question,
            "本层已通过" if status == "CONFIRMED" else "本层未通过，不能升级配置",
        ])
    _write_table(sheet, 6, headers, rows, formats)
    for offset, key in enumerate(questions, start=1):
        raw = gate.get(key, "UNKNOWN")
        sheet.write(6 + offset, 1, _status_text(raw), _status_format(raw, formats))
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
    sheet.merge_range(12, 0, 12, 11, "为什么现在还不能得出完整结论", formats["section"])
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
    write_title(sheet, "01｜总逻辑：从天气故事到可配置策略", "先看气候，再看供需库存，再看价格和趋势；任一关键层缺证据，就不升级配置", formats)
    headers = ["序号", "层级", "核心问题", "关键指标", "通过后才看", "否决边界"]
    rows = [
        [1, "L1 气候天气", "厄尔尼诺是否真实且影响主产区", "海温、大气、印度洋修正、产区降雨/土壤/长势", "L2", "气候异常不等于一定减产"],
        [2, "L2 供给基本面", "天气是否已经转化成减产、需求或库存变化", "棕榈油/白糖各看供给、需求、库存", "L3", "预测值只作背景，实际数据不足就不下结论"],
        [3, "L3 市场定价", "市场是否已经开始为紧缺或宽松定价", "P/SR价格趋势、前后月结构；现货基差缺失会披露", "L4", "行情过期或换月不透明则不确认"],
        [4, "L4 CTA环境", "规则化趋势策略是否能抓住这段行情", "Fast/Medium/Slow；统一20%目标波动", "L5", "次日执行、成本后，并要求中慢速与跨品种确认"],
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
    cta_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
    daily_cta_summary: pd.DataFrame,
    formats: dict[str, Any],
) -> tuple[int, dict[tuple[str, str], int]]:
    headers = [
        "事件", "品种", "价格路径", "CTA代理净值", "状态", "起始", "峰值ONI",
        "证据覆盖", "商品收益3M", "商品收益6M", "商品收益12M", "商品收益18M",
        "CTA成本后收益 Medium12M", "CTA成本后收益 Slow12M", "现有日频代理",
    ]
    rows: list[list[object]] = []
    row_map: dict[tuple[str, str], int] = {}
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
                daily_text = "未覆盖"
            else:
                daily_text = "; ".join(
                    f"{item.speed}:{item.coverage_status}"
                    + (f"/{float(item.net_return):.1%}" if pd.notna(item.net_return) else "")
                    for item in daily.itertuples(index=False)
                )
            cta_group = cta_paths[
                cta_paths.get("event_id", pd.Series(dtype=str)).eq(event.event_id)
                & cta_paths.get("commodity", pd.Series(dtype=str)).eq(commodity)
            ]
            medium = cta_group[cta_group.get("speed", pd.Series(dtype=str)).eq("medium")]
            slow = cta_group[cta_group.get("speed", pd.Series(dtype=str)).eq("slow")]
            chosen_cta = medium if not medium.empty else slow
            price_available = pd.to_numeric(price_group.get("price_index"), errors="coerce").notna().sum() >= 2
            cta_available = pd.to_numeric(chosen_cta.get("event_equity"), errors="coerce").notna().sum() >= 2
            row_map[(str(event.event_id), commodity)] = 5 + len(rows)
            rows.append([
                event.event_id, COMMODITY_CN[commodity], "" if price_available else "未覆盖",
                "" if cta_available else "未覆盖", _status_text(event.status),
                pd.to_datetime(event.start_date), event.peak_oni,
                pd.to_numeric(event_evidence.get("coverage_pct"), errors="coerce").mean() if not event_evidence.empty else pd.NA,
                *(price_return(horizon) for horizon in (3, 6, 12, 18)),
                *cta_values, daily_text,
            ])
    _write_table(
        sheet,
        4,
        headers,
        rows,
        formats,
        column_formats={7: formats["percent"], 8: formats["percent"], 9: formats["percent"],
                        10: formats["percent"], 11: formats["percent"], 12: formats["percent"],
                        13: formats["percent"]},
    )
    return 5 + len(rows), row_map


def write_event_path_data_and_sparklines(
    sheet: Any,
    price_paths: pd.DataFrame,
    cta_paths: pd.DataFrame,
    row_map: dict[tuple[str, str], int],
    formats: dict[str, Any],
) -> None:
    source_col = 40
    source_row = 0
    for keys, group in price_paths.groupby(["event_id", "commodity"], sort=False):
        normalized_keys = (str(keys[0]), str(keys[1]))
        visible_row = row_map.get(normalized_keys)
        if visible_row is None:
            continue
        ordered = group.sort_values("relative_month")
        values = pd.to_numeric(ordered["price_index"], errors="coerce").dropna().tolist()
        if len(values) >= 2:
            sheet.write_row(source_row, source_col, values)
            sheet.add_sparkline(visible_row, 2, {
                "range": xlsxwriter.utility.xl_range(source_row, source_col, source_row, source_col + len(values) - 1),
                "type": "line", "series_color": "#4472C4", "show_hidden": True,
            })
        cta_group = cta_paths[
            cta_paths.get("event_id", pd.Series(dtype=str)).eq(keys[0])
            & cta_paths.get("commodity", pd.Series(dtype=str)).eq(keys[1])
        ]
        medium = cta_group[cta_group.get("speed", pd.Series(dtype=str)).eq("medium")]
        slow = cta_group[cta_group.get("speed", pd.Series(dtype=str)).eq("slow")]
        chosen = medium if not medium.empty else slow
        cta_values = pd.to_numeric(
            chosen.sort_values("relative_month").get("event_equity"), errors="coerce"
        ).dropna().tolist()
        if len(cta_values) >= 2:
            cta_source_row = source_row + 200
            sheet.write_row(cta_source_row, source_col, cta_values)
            sheet.add_sparkline(visible_row, 3, {
                "range": xlsxwriter.utility.xl_range(
                    cta_source_row, source_col, cta_source_row, source_col + len(cta_values) - 1
                ),
                "type": "line", "series_color": "#C55A11", "show_hidden": True,
            })
        source_row += 1
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})


def write_pattern_table(sheet: Any, patterns: pd.DataFrame, start_row: int, formats: dict[str, Any]) -> None:
    headers = ["品种", "指标", "样本", "正/负/平", "同向占比", "中位数", "Q25", "Q75", "规律强度", "解释边界"]
    rows = []
    for item in patterns.itertuples(index=False):
        rows.append([
            COMMODITY_CN.get(item.commodity, item.commodity), METRIC_CN.get(item.metric_id, item.metric_id), item.sample_size,
            f"{item.positive_count}/{item.negative_count}/{item.flat_count}",
            item.same_direction_share, item.median, item.q25, item.q75,
            item.pattern_strength, "仅描述已完成事件；不生成配置或置信度修正",
        ])
    _write_table(sheet, start_row, headers, rows, formats)


def add_history_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[2])
    sheet.set_tab_color("#8064A2")
    write_title(sheet, "02｜历次厄尔尼诺复盘", "每行同时看气候证据、品种价格和CTA代理；迷你图左端为T-6月、中间为T0、右端为T+18月", formats)
    end_row, row_map = write_event_summary_table(
        sheet, frames["events"], frames["evidence"], frames["price_paths"], frames["cta_paths"],
        frames["cta_summary"], frames["daily_cta_summary"], formats,
    )
    write_event_path_data_and_sparklines(
        sheet, frames["price_paths"], frames["cta_paths"], row_map, formats
    )
    pattern_row = max(end_row + 3, 11)
    sheet.merge_range(pattern_row, 0, pattern_row, 11, "历史规律统计", formats["section"])
    write_pattern_table(sheet, frames["patterns"], pattern_row + 1, formats)
    sheet.set_column("A:B", 13)
    sheet.set_column("C:D", 18)
    sheet.set_column("E:G", 13)
    sheet.set_column("H:N", 15)
    sheet.set_column("O:O", 24)
    sheet.freeze_panes(5, 0)


def _last_record(frame: pd.DataFrame) -> pd.Series | None:
    return frame.iloc[-1] if not frame.empty else None


def _safe_float(value: object) -> float | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) else None


def _dataset_meta(frames: dict[str, object], name: str) -> dict[str, object]:
    return dict(frames["external_manifest"].get("datasets", {}).get(name, {}))


def _external_display_status(
    raw_status: object,
    monitor_item: dict[str, object],
) -> str:
    freshness = str(monitor_item.get("freshness_status", ""))
    lag = monitor_item.get("staleness_days")
    if str(raw_status) == "OK" and freshness == "STALE":
        return f"数据延迟{lag}天（STALE）" if lag is not None else _status_text("STALE")
    return _status_text(raw_status)


def _external_reason(meta: dict[str, object], raw_status: object) -> str:
    if str(raw_status) in {"OK", "NOT_ANNOUNCED"}:
        return "数据已取得；事件尚未发生时会显示为“官方尚未宣布”"
    return str(meta.get("reason_cn") or meta.get("reason") or "旧版采集仅记录状态码，需重新刷新定位")


def _add_row_sparkline(
    sheet: Any,
    visible_row: int,
    visible_col: int,
    values: list[float],
    source_row: int,
    source_col: int,
    color: str,
) -> bool:
    cleaned = pd.to_numeric(pd.Series(values), errors="coerce").dropna().tolist()
    if len(cleaned) < 2:
        return False
    sheet.write_row(source_row, source_col, cleaned)
    sheet.add_sparkline(visible_row, visible_col, {
        "range": xlsxwriter.utility.xl_range(
            source_row, source_col, source_row, source_col + len(cleaned) - 1
        ),
        "type": "line",
        "series_color": color,
        "markers": False,
        "show_hidden": True,
    })
    return True


def write_external_observation_rows(
    sheet: Any,
    frames: dict[str, object],
    start_row: int,
    formats: dict[str, Any],
) -> None:
    headers = [
        "外部观测", "状态", "当前值/事件", "同期基准", "领导怎么读", "数据截至", "来源",
        "迷你趋势", "为什么没有数据", "怎么补齐", "对结论影响",
    ]
    monsoon = _last_record(frames["monsoon_cumulative"])
    events = frames["monsoon_events"]
    ndvi = _last_record(frames["sugar_ndvi"])
    monsoon_meta = _dataset_meta(frames, "monsoon_cumulative")
    imd_meta = _dataset_meta(frames, "imd_events")
    ndvi_meta = _dataset_meta(frames, "ndvi")
    monitor_external = frames["monitor"].get("external_observations", {})
    monsoon_status = monsoon_meta.get(
        "status", monsoon.get("status", "MISSING") if monsoon is not None else "MISSING"
    )
    monsoon_current = (
        monsoon.get("current_cumulative_mm", monsoon.get("actual_cumulative_mm"))
        if monsoon is not None else None
    )
    monsoon_value = (
        f"累计{float(monsoon_current):.1f}mm；较常年{float(monsoon.get('cumulative_departure_pct')):+.1%}"
        if monsoon is not None and monsoon_status == "OK" and pd.notna(monsoon.get("cumulative_departure_pct"))
        else "尚无可用数值"
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
        event_text = "尚无可用事件日期"
    ndvi_status = ndvi_meta.get(
        "status", ndvi.get("status", "MISSING") if ndvi is not None else "MISSING"
    )
    ndvi_value = (
        f"NDVI {ndvi.get('ndvi')}；z={ndvi.get('z_score')}；有效像元{float(ndvi.get('valid_pixel_ratio')):.1%}"
        if ndvi is not None and ndvi_status == "OK" and pd.notna(ndvi.get("ndvi"))
        else "尚无可用数值"
    )
    monsoon_display = _external_display_status(
        monsoon_status, monitor_external.get("monsoon_cumulative", {})
    )
    event_display = _external_display_status(event_status, monitor_external.get("imd_events", {}))
    ndvi_display = _external_display_status(ndvi_status, monitor_external.get("ndvi", {}))
    rows = [
        ["印度季风累计降雨偏差", monsoon_display, monsoon_value,
         monsoon.get("climatology_cumulative_mm", monsoon.get("normal_cumulative_mm", "")) if monsoon is not None else "",
         "负偏差=雨季累计偏少；正偏差=偏多。必须同时看更新延迟和地点覆盖。", monsoon.get("date", "") if monsoon is not None else "",
         monsoon_meta.get("source", monsoon.get("source", "") if monsoon is not None else ""), "",
         _external_reason(monsoon_meta, monsoon_status),
         monsoon_meta.get("next_action", "等待NASA POWER更新或重试；沿用最后完整数据日"),
         monsoon_meta.get("conclusion_impact", "仅作产区补充证据，不单独触发CTA配置动作")],
        ["IMD季风开始/退出", event_display, event_text, "开始常年约6月1日；西北部退出常年约9月17日",
         "偏早/偏晚影响有效雨季长度，但宣告具有事后确认属性",
         (
             events.get("actual_date", events.get("official_date", pd.Series(dtype=str))).dropna().max()
             if not events.empty else ""
         ),
         imd_meta.get("source", "IMD"), "事件型指标",
         _external_reason(imd_meta, event_status),
         imd_meta.get("next_action", "继续读取IMD官方公告；退出尚未发生时保持未宣布"),
         imd_meta.get("conclusion_impact", "仅作有效雨季长度补充证据")],
        ["印度糖蔗主产区NDVI代理", ndvi_display, ndvi_value,
         ndvi.get("climatology_mean", ndvi.get("same_period_mean", "")) if ndvi is not None else "",
         "低分位/负z值代表长势偏弱；主产区代表点邻域不是糖蔗专属掩膜",
         ndvi.get("product_date", "") if ndvi is not None else "",
         ndvi_meta.get("source", ndvi.get("source", "") if ndvi is not None else ""), "",
         _external_reason(ndvi_meta, ndvi_status),
         ndvi_meta.get("next_action", "公共接口恢复后重试；或配置Earthdata令牌走官方备用接口"),
         ndvi_meta.get("conclusion_impact", "缺少甘蔗长势验证，不能认为印度供给证据完整")],
    ]
    _write_table(sheet, start_row, headers, rows, formats)
    for offset, row in enumerate(rows, start=1):
        raw = (monsoon_status, event_status, ndvi_status)[offset - 1]
        sheet.write(start_row + offset, 1, row[1], _status_format(raw, formats))
    source_col = 70
    monsoon_values = pd.to_numeric(
        frames["monsoon_cumulative"].get("cumulative_departure_pct"), errors="coerce"
    ).dropna().tail(24).tolist()
    ndvi_values = pd.to_numeric(frames["sugar_ndvi"].get("ndvi"), errors="coerce").dropna().tail(24).tolist()
    if not _add_row_sparkline(sheet, start_row + 1, 7, monsoon_values, 20, source_col, "#4472C4"):
        sheet.write(start_row + 1, 7, "少于2期", formats["center"])
    if not _add_row_sparkline(sheet, start_row + 3, 7, ndvi_values, 21, source_col, "#70AD47"):
        sheet.write(start_row + 3, 7, "少于2期", formats["center"])
    sheet.set_column(source_col, source_col + 40, None, None, {"hidden": True})


def add_climate_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[3])
    sheet.set_tab_color("#00A6A6")
    write_title(sheet, "03｜L1 气候天气", "本页回答：这次厄尔尼诺是否真实、是否有大气配合、是否已经影响主产区", formats)
    climate = frames["monitor"].get("layers", {}).get("climate", {})
    sheet.merge_range(3, 0, 3, 11, "领导先看：RONI/ONI确认海温，SOI确认大气配合，DMI修正印度洋影响；四者都不能直接替代作物供给数据。", formats["note"])
    headers = ["指标（中文全称）", "它回答什么", "当前值", "状态/方向", "阈值/正常区间", "当前位置意味着什么", "数据截至", "迷你趋势"]
    rows = []
    for key in ("oni", "roni", "soi", "dmi"):
        item = climate.get(key, {})
        history = frames["climate"].get(f"noaa_{key}", pd.DataFrame())
        latest_history = history.iloc[-1] if not history.empty else pd.Series()
        value = item.get(key, latest_history.get(key, ""))
        date_value = item.get("date", latest_history.get("date", ""))
        guide = CLIMATE_GUIDE[key]
        rows.append([
            guide["label"], guide["question"], value,
            item.get("state", item.get("phase", "需结合阈值判断")),
            guide["threshold"], guide["meaning"], date_value, "",
        ])
    _write_table(sheet, 4, headers, rows, formats)
    source_col = 30
    for offset, key in enumerate(("oni", "roni", "soi", "dmi"), start=1):
        history = frames["climate"].get(f"noaa_{key}", pd.DataFrame())
        values = pd.to_numeric(history.get(key), errors="coerce").dropna().tail(24).tolist()
        if not _add_row_sparkline(sheet, 4 + offset, 7, values, offset - 1, source_col, "#4472C4"):
            sheet.write(4 + offset, 7, "少于2期", formats["center"])
    sheet.set_column(source_col, source_col + 40, None, None, {"hidden": True})
    sheet.merge_range(10, 0, 10, 11, "三项持续外部观测", formats["section"])
    write_external_observation_rows(sheet, frames, 11, formats)
    sheet.set_column("A:A", 30)
    sheet.set_column("B:B", 28)
    sheet.set_column("C:D", 16)
    sheet.set_column("E:G", 27)
    sheet.set_column("H:H", 16)
    sheet.set_column("I:K", 32)
    sheet.freeze_panes(5, 0)


def write_l2_summary_rows(
    sheet: Any,
    summary: pd.DataFrame,
    fundamental_history: dict[str, pd.DataFrame],
    start_row: int,
    formats: dict[str, Any],
) -> None:
    headers = [
        "品种", "类别", "主指标（中文）", "当前值", "迷你趋势", "单位",
        "实际/预测", "季节分位", "方向", "方向怎么算", "数据截至", "辅助验证", "领导怎么读",
    ]
    rows = []
    for item in summary.itertuples(index=False):
        drivers = "、".join(
            METRIC_CN.get(part.strip(), part.strip())
            for part in str(item.drivers or "").split(",") if part.strip()
        )
        rows.append([
            COMMODITY_CN[item.commodity], CATEGORY_CN[item.category], METRIC_CN.get(item.primary_metric, item.primary_metric),
            item.primary_value, "", item.unit, item.value_type, item.seasonal_percentile,
            _direction_text(item.direction), item.direction_reason, item.date, drivers or "无",
            "先看实际主指标，再看同季节位置；预测值只能作背景",
        ])
    _write_table(sheet, start_row, headers, rows, formats, column_formats={7: formats["percent"]})
    for offset, row in enumerate(rows, start=1):
        raw = summary.iloc[offset - 1]["direction"]
        sheet.write(start_row + offset, 8, row[8], _status_format(raw, formats))
    source_col = 40
    for offset, item in enumerate(summary.itertuples(index=False), start=1):
        values = _l2_history_values(item.primary_metric, fundamental_history)
        if len(values) < 2:
            sheet.write(start_row + offset, 4, "少于2期", formats["center"])
            continue
        source_row = 100 + offset
        sheet.write_row(source_row, source_col, values)
        sheet.add_sparkline(start_row + offset, 4, {
            "range": xlsxwriter.utility.xl_range(
                source_row, source_col, source_row, source_col + len(values) - 1
            ),
            "type": "line", "series_color": "#70AD47", "show_hidden": True,
        })


def write_l2_detail_rows(
    sheet: Any,
    details: pd.DataFrame,
    fundamental_history: dict[str, pd.DataFrame],
    start_row: int,
    formats: dict[str, Any],
) -> None:
    headers = ["指标", "名称", "当前值", "单位", "迷你趋势", "它衡量什么", "观测日", "发布日期", "实际/预测", "季节分位", "当前位置含义", "来源", "缺失/局限"]
    rows = []
    for item in details.itertuples(index=False):
        rows.append([
            item.metric_id, METRIC_CN.get(item.metric_id, item.label), item.value, item.unit, "",
            item.meaning, item.date, item.published_at, item.value_type, item.seasonal_percentile,
            item.position_meaning, item.source, item.limitation,
        ])
    _write_table(sheet, start_row, headers, rows, formats, column_formats={9: formats["percent"]})
    source_col = 52
    for index, item in enumerate(details.itertuples(index=False), start=1):
        values = _l2_history_values(item.metric_id, fundamental_history)
        if len(values) < 2:
            continue
        source_row = index - 1
        sheet.write_row(source_row, source_col, values)
        sheet.add_sparkline(start_row + index, 4, {
            "range": xlsxwriter.utility.xl_range(source_row, source_col, source_row, source_col + len(values) - 1),
            "type": "line", "series_color": "#70AD47", "show_hidden": True,
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
    write_title(sheet, "04｜L2 供给基本面", "本页回答：天气是否已经转化成产量、需求和库存变化；迷你图紧邻当前值", formats)
    sheet.merge_range(3, 0, 3, 12, "判断顺序：实际主指标 → 同季节分位 → 方向；预测只作背景，白糖供给需印度产量与巴西制糖甘蔗代理共同验证。", formats["note"])
    sheet.merge_range(4, 0, 4, 12, "季节分位怎么算：月度指标与历史同月比较；UNICA与历史同榨季月份比较；年度USDA指标在历史年度中排名。分位=不高于当前值的有效历史样本数÷有效样本总数。", formats["note"])
    sheet.merge_range(5, 0, 5, 12, "方向怎么算：供给/库存低位→偏紧，高位→偏松；需求低位→偏松，高位→偏紧。低位≤33%，高位≥67%；评分指标冲突→中性，含预测或缺失→证据不足。", formats["note"])
    sheet.merge_range(8, 0, 8, 11, "六格结论", formats["section"])
    write_l2_summary_rows(sheet, frames["l2_summary"], frames["fundamentals"], 9, formats)
    sheet.merge_range(17, 0, 17, 11, "指标明细", formats["section"])
    write_l2_detail_rows(sheet, frames["l2_details"], frames["fundamentals"], 18, formats)
    sheet.set_column("A:B", 14)
    sheet.set_column("C:C", 26)
    sheet.set_column("D:E", 15)
    sheet.set_column("F:I", 14)
    sheet.set_column("J:J", 48)
    sheet.set_column("K:K", 15)
    sheet.set_column("L:M", 28)
    sheet.freeze_panes(10, 0)


def _flatten(prefix: str, value: object, output: list[tuple[str, object]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, output)
    elif not isinstance(value, list):
        output.append((prefix, value))


def _futures_history(frames: dict[str, object], symbol: str) -> pd.DataFrame:
    for name in (f"{symbol}_active_wind", f"{symbol}_dominant_tushare", f"{symbol}_continuous_exploratory"):
        frame = frames["futures"].get(name, pd.DataFrame())
        if not frame.empty:
            return frame.copy()
    return pd.DataFrame()


def _research_signal(frames: dict[str, object], symbol: str, speed: str) -> pd.DataFrame:
    return frames["signals"].get(f"{symbol}_{speed}_proxy_research", pd.DataFrame()).copy()


def _wind_exploratory_signal(
    frames: dict[str, object], symbol: str, speed: str
) -> pd.DataFrame:
    stored = frames["signals"].get(
        f"{symbol}_{speed}_proxy_wind_exploratory", pd.DataFrame()
    )
    if not stored.empty:
        return stored.copy()
    history = frames["futures"].get(f"{symbol}_active_wind", pd.DataFrame())
    return build_cta_proxy(history, speed) if not history.empty else pd.DataFrame()


def _return_over_rows(frame: pd.DataFrame, rows: int, column: str = "close") -> object:
    values = pd.to_numeric(frame.get(column), errors="coerce").dropna()
    if len(values) < 2:
        return pd.NA
    start = values.iloc[max(0, len(values) - rows - 1)]
    return values.iloc[-1] / start - 1 if start else pd.NA


def _annualized_volatility(frame: pd.DataFrame, rows: int = 20) -> object:
    values = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
    returns = values.pct_change().dropna().tail(rows)
    return float(returns.std() * (252 ** 0.5)) if len(returns) >= 2 else pd.NA


def _max_drawdown(frame: pd.DataFrame, rows: int = 60) -> object:
    values = pd.to_numeric(frame.get("close"), errors="coerce").dropna().tail(rows + 1)
    if len(values) < 2:
        return pd.NA
    return float((values / values.cummax() - 1).min())


def add_market_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[5])
    sheet.set_tab_color("#ED7D31")
    write_title(sheet, "05｜L3 市场定价", "本页回答：棕榈油和白糖价格是否已开始为供需变化定价；迷你图展示主力合约近段走势", formats)
    sheet.merge_range(3, 0, 3, 11, "期货价格=市场对未来供需的交易结果；Contango表示远月贵于近月，通常不支持眼前紧缺。现货基差尚未正式接入，会作为缺口披露。", formats["note"])
    headers = [
        "品种", "行情口径", "收盘价", "价格趋势", "20日涨跌", "60日涨跌",
        "20日年化波动", "60日最大回撤", "前后月结构", "前后月价差", "数据截至", "领导怎么读",
    ]
    market = frames["monitor"].get("layers", {}).get("market", {})
    rows: list[list[object]] = []
    for symbol in ("P", "SR"):
        item = market.get(symbol, {})
        history = _futures_history(frames, symbol).sort_values("date")
        if not history.empty:
            history["date"] = pd.to_datetime(history["date"], errors="coerce")
            history = history.dropna(subset=["date"]).sort_values("date")
        latest = history.iloc[-1] if not history.empty else pd.Series()
        provider = str(latest.get("provider", ""))
        is_wind = provider == "Wind EDB"
        if is_wind:
            code = latest.get("indicator_code", "")
            price_basis = f"Wind活跃合约（{code}）" if code else "Wind活跃合约"
        else:
            price_basis = item.get("contract", latest.get("contract", ""))
        term = {} if is_wind else item.get("term_structure", {})
        latest_date = latest.get("date", item.get("date"))
        monitor_as_of = pd.to_datetime(frames["monitor"].get("as_of"), errors="coerce")
        parsed_latest_date = pd.to_datetime(latest_date, errors="coerce")
        staleness = (
            max(0, int((monitor_as_of - parsed_latest_date).days))
            if pd.notna(monitor_as_of) and pd.notna(parsed_latest_date)
            else item.get("staleness_days")
        )
        source_text = (
            f"数据来源：Wind（{latest.get('source', '交易所')}）"
            if is_wind else f"数据来源：{provider or item.get('provider', '既有行情文件')}"
        )
        stale_text = (
            f"行情已延迟{staleness}天，超过4天阈值，L3不能确认；{source_text}"
            if staleness is not None and int(staleness) > 4
            else f"行情时效合格；仍需结合基差与CTA趋势；{source_text}"
        )
        rows.append([
            SYMBOL_CN[symbol], price_basis,
            latest.get("close", item.get("close", "")), "",
            _return_over_rows(history, 20) if not history.empty else item.get("price_return_20d", ""),
            _return_over_rows(history, 60) if not history.empty else item.get("price_return_60d", ""),
            _annualized_volatility(history, 20) if not history.empty else item.get("realized_vol_20d", ""),
            _max_drawdown(history, 60) if not history.empty else item.get("max_price_drawdown_60d", ""),
            term.get("curve", "未取得（活跃合约序列不含月差）" if is_wind else "未取得"),
            term.get("front_second_spread_pct", ""), latest_date or "", stale_text,
        ])
    _write_table(
        sheet, 4, headers, rows, formats,
        column_formats={4: formats["percent"], 5: formats["percent"], 6: formats["percent"],
                        7: formats["percent"], 9: formats["percent"]},
    )
    source_col = 30
    for offset, symbol in enumerate(("P", "SR"), start=1):
        history = _futures_history(frames, symbol).sort_values("date")
        values = pd.to_numeric(history.get("close"), errors="coerce").dropna().tail(60).tolist()
        if not _add_row_sparkline(sheet, 4 + offset, 3, values, offset - 1, source_col, "#ED7D31"):
            sheet.write(4 + offset, 3, "少于2期", formats["center"])
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})
    sheet.merge_range(9, 0, 9, 11, "尚缺数据：可审计的现货价格/基差序列尚未接入；在补齐前，L3只依据期货价格和期限结构，结论置信度受限。", formats["note"])
    sheet.set_column("A:B", 18)
    sheet.set_column("C:C", 13)
    sheet.set_column("D:D", 18)
    sheet.set_column("E:J", 15)
    sheet.set_column("K:K", 15)
    sheet.set_column("L:L", 34)
    sheet.freeze_panes(5, 0)


def add_cta_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[6])
    sheet.set_tab_color("#FFC000")
    write_title(sheet, "06｜L4 本研究P/SR双品种CTA环境", "CTA是按价格规则做多或做空的趋势策略；三档速度统一20%年化目标波动，信号次日执行", formats)
    headers = ["速度", "方向回看", "波动率窗口", "用途", "确认边界"]
    rows = [
        ["Fast", "20/60交易日", "20交易日", "快速风险变化；噪声较高", "不单独触发主题配置"],
        ["Medium", "60/120交易日", "60交易日", "中期趋势确认", "需与Slow及跨品种证据共同判断"],
        ["Slow", "120/250交易日", "120交易日", "长期趋势确认", "起效慢，但更接近配置视角"],
    ]
    _write_table(sheet, 4, headers, rows, formats)
    sheet.merge_range(9, 0, 9, 11, "确认规则：同一品种Medium与Slow同号（均指执行仓位）且非零、两者近20日净收益均为正、Medium持续达到阈值，才算该品种确认；P与SR分别确认，P与SR方向不必相同。", formats["note"])
    sheet.merge_range(10, 0, 10, 11, "当前P/SR趋势策略代理", formats["section"])
    signal_headers = [
        "品种", "速度", "今日目标信号", "代理净值趋势", "今日执行仓位", "20日净收益", "60日净收益",
        "60日最大回撤", "仓位持续天数", "数据截至", "当前含义", "确认角色",
    ]
    cta = frames["monitor"].get("layers", {}).get("cta", {})
    signal_rows: list[list[object]] = []
    for symbol in ("P", "SR"):
        for speed in ("fast", "medium", "slow"):
            item = cta.get(symbol, {}).get(speed, {})
            frame = _research_signal(frames, symbol, speed).sort_values("date")
            latest = frame.iloc[-1] if not frame.empty else pd.Series()
            position = item.get("position", latest.get("position", ""))
            target_position = item.get("target_position", latest.get("target_position", ""))
            numeric_position = _safe_float(position)
            numeric_target = _safe_float(target_position)
            def position_text(value: float | None) -> str:
                return (
                    f"{value:+.2f}倍（做多）" if value is not None and value > 0
                    else f"{value:+.2f}倍（做空）" if value is not None and value < 0
                    else "0倍（空仓/中性）" if value == 0 else "未取得"
                )
            execution_text = position_text(numeric_position)
            target_text = position_text(numeric_target)
            current_meaning = (
                f"目标={target_text}；执行={execution_text}。执行仓位来自上一交易日目标，二者不同表示信号正在切换。"
            )
            wind_frame = _wind_exploratory_signal(frames, symbol, speed)
            if not wind_frame.empty:
                wind_frame = wind_frame.sort_values("date")
                wind_latest = wind_frame.iloc[-1]
                wind_date = str(wind_latest.get("date", ""))[:10]
                wind_target = _safe_float(wind_latest.get("target_position"))
                current_meaning += (
                    f" Wind {wind_date}仅方向复核={position_text(wind_target)}；"
                    "活跃合约换月规则未公开，不计入正式CTA确认。"
                )
                if symbol == "SR" and wind_target == 0:
                    current_meaning += " SR最新复核已转中性，旧研究执行空仓不代表当前继续做空。"
            role = {
                "fast": "快速预警，噪声高，不单独确认",
                "medium": "核心中期确认",
                "slow": "配置级长期确认",
            }[speed]
            signal_rows.append([
                SYMBOL_CN[symbol], speed.capitalize(), target_text, "", execution_text,
                item.get("net_return_20d", _return_over_rows(frame, 20, "equity")),
                item.get("net_return_60d", _return_over_rows(frame, 60, "equity")),
                item.get("max_drawdown_60d", ""), item.get("position_persistence_days", ""),
                item.get("date", latest.get("date", "")),
                current_meaning, role,
            ])
    _write_table(
        sheet, 11, signal_headers, signal_rows, formats,
        column_formats={5: formats["percent"], 6: formats["percent"], 7: formats["percent"]},
    )
    source_col = 30
    row_index = 0
    for symbol in ("P", "SR"):
        for speed in ("fast", "medium", "slow"):
            frame = _research_signal(frames, symbol, speed).sort_values("date")
            values = pd.to_numeric(frame.get("equity"), errors="coerce").dropna().tail(60).tolist()
            if not _add_row_sparkline(sheet, 12 + row_index, 3, values, row_index, source_col, "#FFC000"):
                sheet.write(12 + row_index, 3, "少于2期", formats["center"])
            row_index += 1
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})
    sheet.set_column("A:B", 16)
    sheet.set_column("C:C", 22)
    sheet.set_column("D:D", 18)
    sheet.set_column("E:E", 22)
    sheet.set_column("F:J", 16)
    sheet.set_column("K:L", 34)
    sheet.freeze_panes(5, 0)


def add_audit_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[7])
    sheet.set_tab_color("#7F7F7F")
    write_title(
        sheet,
        "07｜L5 数据审计",
        "本页回答：哪些数据能用、哪些缺失、为什么缺、怎样补齐，以及缺口会怎样影响结论；任何Token都不写入工作簿",
        formats,
    )
    external = frames["external_manifest"]
    sheet.write(3, 0, "外部观测总状态", formats["header"])
    overall = str(external.get("overall", external.get("status", "MISSING")))
    sheet.write(3, 1, _status_text(overall), _status_format(overall, formats))
    headers = [
        "数据集（中文）", "状态", "行数", "观测截至日", "官方发布时间", "本次刷新时间",
        "覆盖率", "来源", "为什么没有/有什么限制", "怎么补齐", "对结论影响",
    ]
    rows = []
    for name, item in external.get("datasets", {}).items():
        raw_status = str(item.get("status", "UNKNOWN"))
        reason = _external_reason(item, raw_status)
        if not reason and raw_status in {"OK", "CONFIRMED"}:
            reason = "数据可用；仍需按发布频率持续刷新"
        rows.append([
            EXTERNAL_DATASET_CN.get(name, name), _status_text(raw_status), item.get("rows", ""),
            item.get("data_as_of", item.get("max_date", "")), item.get("published_at", ""),
            item.get("refreshed_at", external.get("downloaded_at", "")), item.get("coverage_pct", ""),
            item.get("source", item.get("source_host", "")), reason,
            item.get("next_action", "按数据源发布频率自动重试"),
            item.get("conclusion_impact", "作为L1补充证据；不单独触发CTA动作"),
        ])
    _write_table(sheet, 5, headers, rows, formats, column_formats={6: formats["percent"]})
    for offset, (name, item) in enumerate(external.get("datasets", {}).items(), start=1):
        raw_status = str(item.get("status", "UNKNOWN"))
        sheet.write(5 + offset, 1, _status_text(raw_status), _status_format(raw_status, formats))
    monitor = frames["monitor"]
    blocker_row = 8 + len(rows)
    sheet.merge_range(blocker_row, 0, blocker_row, 11, "当前阻断结论的数据缺口", formats["section"])
    blocker_rows = []
    for alert in monitor.get("alerts", []):
        code = str(alert.get("code", "UNKNOWN"))
        blocker_rows.append([
            "当前告警", str(alert.get("severity", "unknown")).upper(), code,
            alert.get("message", ""), ALERT_ACTION_CN.get(code, "回溯对应源数据，修复后重新运行监控"),
        ])
    for gap in monitor.get("scope_gaps", []):
        action = (
            "接入交易所或数据商的可审计连续合约历史，并验证换月规则与成本"
            if any(keyword in str(gap) for keyword in ("BMD", "ICE", "期货"))
            else "补齐对应数据或尽调材料后再进入配置判断"
        )
        blocker_rows.append(["范围缺口", "未覆盖", "SCOPE_GAP", gap, action])
    if not blocker_rows:
        blocker_rows.append(["当前状态", "无", "NONE", "未发现额外数据阻断项", "按计划持续刷新"])
    blocker_header_row = blocker_row + 1
    sheet.write(blocker_header_row, 0, "类型", formats["header"])
    sheet.write(blocker_header_row, 1, "严重度/状态", formats["header"])
    sheet.write(blocker_header_row, 2, "代码", formats["header"])
    sheet.merge_range(blocker_header_row, 3, blocker_header_row, 6, "缺口或原因", formats["header"])
    sheet.merge_range(blocker_header_row, 7, blocker_header_row, 10, "怎么补齐", formats["header"])
    for offset, row in enumerate(blocker_rows, start=1):
        output_row = blocker_header_row + offset
        sheet.write(output_row, 0, row[0], formats["body"])
        sheet.write(output_row, 1, row[1], formats["body"])
        sheet.write(output_row, 2, row[2], formats["body"])
        sheet.merge_range(output_row, 3, output_row, 6, row[3], formats["body"])
        sheet.merge_range(output_row, 7, output_row, 10, row[4], formats["body"])
        sheet.set_row(output_row, 34)
    history_row = blocker_row + len(blocker_rows) + 4
    sheet.merge_range(history_row, 0, history_row, 11, "历史复盘审计", formats["section"])
    manifest = frames["history_manifest"]
    sheet.write(history_row + 1, 0, "复盘截至", formats["body"])
    sheet.write(history_row + 1, 1, manifest.get("as_of", ""), formats["body"])
    sheet.write(history_row + 2, 0, "逻辑边界", formats["body"])
    sheet.merge_range(history_row + 2, 1, history_row + 2, 8, manifest.get("boundary", "仅描述，不进入当前配置"), formats["note"])
    glossary_row = history_row + 5
    sheet.merge_range(glossary_row, 0, glossary_row, 11, "状态词怎么理解", formats["section"])
    glossary = [
        ["可用（OK）", "已取得并通过基本校验，可进入对应层判断"],
        ["官方尚未宣布（NOT_ANNOUNCED）", "这是事件尚未发生或官方尚未确认，不是抓取故障"],
        ["缺少授权（AUTH_REQUIRED）", "接口需要有效账户或令牌；配置后可自动补数"],
        ["获取失败（FETCH_ERROR）", "本次访问数据源失败；原因列区分服务器、网络、解析等问题"],
        ["数据过期（STALE）", "历史数据存在，但距报告日超过允许时滞，不能确认当前状态"],
    ]
    _write_table(sheet, glossary_row + 1, ["状态", "给领导的解释"], glossary, formats)
    sheet.set_column("A:A", 32)
    sheet.set_column("B:C", 18)
    sheet.set_column("D:G", 18)
    sheet.set_column("H:H", 36)
    sheet.set_column("I:K", 34)
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
    output_path = output_dir / f"厄尔尼诺农产品CTA研究框架_八页领导汇报版_{as_of.replace('-', '')}.xlsx"
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
