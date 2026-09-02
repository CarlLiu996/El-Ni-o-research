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
from elnino_cta.expanded_review import PRICE_CHAIN_MAP
from elnino_cta.signals import build_cta_proxy
from elnino_cta.sources.china_sugar import current_province_rows


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
COMMODITY_CN = {
    "palm_oil": "棕榈油", "sugar": "白糖", "cotton": "棉花", "corn": "玉米",
    "soybean": "大豆产业链", "rapeseed": "菜籽产业链",
}
SYMBOL_CN = {
    "P": "棕榈油 P", "SR": "白糖 SR", "CF": "棉花 CF", "C": "玉米 C",
    "M": "豆粕 M", "Y": "豆油 Y", "OI": "菜籽油 OI", "RM": "菜籽粕 RM",
}
UNIVERSE_SYMBOLS = ("P", "SR", "CF", "C", "M", "Y", "OI", "RM")
CHAIN_STATUS_CN = {
    "LONG_CONFIRMED": "中慢速同向做多",
    "SHORT_CONFIRMED": "中慢速同向做空",
    "SPEED_DIVERGENCE": "中慢速方向分歧",
    "CHAIN_DIVERGENCE": "链内合约方向分歧",
    "UNCONFIRMED": "方向未确认",
    "INCOMPLETE": "数据不完整",
}
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
FOCUS_HISTORY_EVENTS = {
    "1982-1983": "1982/83",
    "1997-1998": "1997/98",
    "2014-2016": "2015/16",
    "2023-2024": "2023/24",
}
REPORT_PALM_YIELD_NOTES = {
    "1982-1983": "研报棕榈单产：印尼0.0%、马来0.0%",
    "1997-1998": "研报棕榈单产：印尼-19.9%、马来-11.5%",
    "2014-2016": "研报棕榈单产：印尼-10.4%、马来-13.0%",
    "2023-2024": "研报棕榈单产：印尼-4.2%、马来+6.3%",
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
    expanded_history = load_csv_directory(processed / "expanded_universe" / "history")
    expanded_baselines = load_csv_directory(processed / "expanded_universe")
    expanded_futures = load_csv_directory(processed / "expanded_universe" / "futures")
    expanded_signals = load_csv_directory(processed / "expanded_universe" / "signals")
    expanded_climate_dir = processed / "expanded_universe" / "climate"
    current_weather_snapshot = (
        pd.read_csv(expanded_climate_dir / "current_weather_snapshot.csv")
        if (expanded_climate_dir / "current_weather_snapshot.csv").exists() else pd.DataFrame()
    )
    current_weather_path = (
        pd.read_csv(expanded_climate_dir / "current_weather_30d_path.csv")
        if (expanded_climate_dir / "current_weather_30d_path.csv").exists() else pd.DataFrame()
    )
    expanded_research = load_csv_directory(repo_dir / "research" / "expanded_universe")
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
        "expanded_matrix": expanded_research.get("focal_event_matrix", empty),
        "expanded_patterns": expanded_research.get("focal_pattern_summary", empty),
        "expanded_price_paths": expanded_history.get("event_price_paths", empty),
        "expanded_cta_paths": expanded_history.get("event_cta_paths", empty),
        "expanded_baselines": expanded_baselines,
        "expanded_futures": expanded_futures,
        "expanded_signals": expanded_signals,
        "current_weather_snapshot": current_weather_snapshot,
        "current_weather_path": current_weather_path,
        "china_sugar": load_csv_directory(processed / "china_sugar"),
        "china_sugar_manifest": _read_json(processed / "china_sugar" / "manifest.json"),
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
        "summary_blue": workbook.add_format({
            "bold": True, "font_color": "#17365D", "bg_color": "#D9EAF7",
            "text_wrap": True, "valign": "vcenter", "align": "left",
            "border": 1, "border_color": "#9EBDD7",
        }),
        "summary_gold": workbook.add_format({
            "bold": True, "font_color": "#7F6000", "bg_color": "#FFF2CC",
            "text_wrap": True, "valign": "vcenter", "align": "left",
            "border": 1, "border_color": "#E6B800",
        }),
        "summary_gray": workbook.add_format({
            "bold": True, "font_color": "#404040", "bg_color": "#E7E6E6",
            "text_wrap": True, "valign": "vcenter", "align": "left",
            "border": 1, "border_color": "#BFBFBF",
        }),
        "good": workbook.add_format({**border, "font_color": "#006100", "bg_color": "#C6EFCE", "bold": True, "text_wrap": True}),
        "warn": workbook.add_format({**border, "font_color": "#9C6500", "bg_color": "#FFEB9C", "bold": True, "text_wrap": True}),
        "bad": workbook.add_format({**border, "font_color": "#9C0006", "bg_color": "#FFC7CE", "bold": True, "text_wrap": True}),
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
        f"工作簿补充日：{frames.get('workbook_as_of', as_of)}；原监控快照：{as_of}｜CTA是按规则跟随趋势的期货策略，不等于直接押注农产品上涨",
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
    for offset, row in enumerate([headers, *rows]):
        output_row = 6 + offset
        style = formats["body"] if offset else formats["header"]
        sheet.write(output_row, 0, row[0], style)
        sheet.write(output_row, 1, row[1], style)
        sheet.merge_range(output_row, 2, output_row, 5, row[2], style)
        sheet.merge_range(output_row, 6, output_row, 11, row[3], style)
        sheet.set_row(output_row, 42 if offset else 24)
    for offset, key in enumerate(questions, start=1):
        raw = gate.get(key, "UNKNOWN")
        sheet.write(6 + offset, 1, _status_text(raw), _status_format(raw, formats))
    if frames.get("expanded_patterns") is not None and not frames.get("expanded_patterns", pd.DataFrame()).empty:
        sheet.merge_range(
            11, 0, 11, 11,
            "扩品种复盘边界：新增棉花、玉米、大豆链和菜籽链的历史规律与探索性最新行情只用于研究观察；在正式连续合约、实时需求/库存和人工确认补齐前，不自动修改本页CTA配置结论。",
            formats["note"],
        )
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
    sheet.set_column("B:B", 28)
    sheet.set_column("C:D", 34)
    sheet.set_column("E:L", 12)
    sheet.freeze_panes(6, 0)
    last_row = max(13, 12 + min(len(alerts), 8))
    if frames.get("china_sugar"):
        last_row += 2
        sheet.merge_range(last_row, 0, last_row + 1, 11, _domestic_sugar_headline(frames), formats["summary_gold"])
        sheet.set_row(last_row, 28)
        sheet.set_row(last_row + 1, 28)
        last_row += 1
    sheet.print_area(0, 0, last_row, 11)


def add_logic_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[1])
    sheet.set_tab_color("#4472C4")
    write_title(sheet, "01｜总逻辑：从天气故事到可配置策略", "先看气候，再看供需库存，再看价格和趋势；任一关键层缺证据，就不升级配置", formats)
    headers = ["序号", "层级", "核心问题", "关键指标", "通过后才看", "否决边界"]
    expanded_mode = bool(frames.get("expanded_futures") or frames.get("expanded_baselines"))
    rows = [
        [1, "L1 气候天气", "厄尔尼诺是否真实且影响主产区", "海温、大气、印度洋修正、产区降雨/土壤/长势", "L2", "气候异常不等于一定减产"],
        [2, "L2 供给基本面", "天气是否已经转化成减产、需求或库存变化", "白糖：国内产销库存→进口传导→印度/巴西；其他作物链分别验证供需", "L3", "省级≠全国；糖厂销售≠消费；年度估计/预测只作背景"],
        [3, "L3 市场定价", "市场是否已经开始为紧缺或宽松定价", "八合约价格趋势；正式与探索性口径分列" if expanded_mode else "P/SR价格趋势、前后月结构；现货基差缺失会披露", "L4", "行情过期或换月不透明则不确认"],
        [4, "L4 CTA环境", "规则化趋势策略是否能抓住这段行情", "Fast/Medium/Slow；统一20%目标波动", "L5", "次日执行、成本后，并要求中慢速与跨品种确认"],
        [5, "L5 数据审计", "来源、时效、覆盖与方法是否可信", "发布日期、缺失、哈希、限制", "配置结论", "关键数据不完整则保持观察"],
        ["H", "历史复盘旁路", "历次事件中气候、基本面、价格、CTA如何共同演化", "四次重点事件×六作物链；统一T0、价格、Medium/Slow CTA" if expanded_mode else "统一T0窗口、价格收益、Medium/Slow CTA代理", "研究理解", "仅描述，不进入配置或置信度修正"],
    ]
    _write_table(sheet, 4, headers, rows, formats)
    sheet.merge_range(13, 0, 14, 11, "主链：L1 → L2 → L3 → L4 → L5 → 当前配置结论\n旁路：H 历史复盘 → 先总结规律，待人工确认后再讨论是否设计置信度修正。", formats["note"])
    sheet.set_column("A:A", 8)
    sheet.set_column("B:B", 20)
    sheet.set_column("C:F", 32)
    sheet.freeze_panes(5, 0)
    if frames.get("china_sugar"):
        sheet.merge_range(16, 0, 17, 11,
                          "白糖新增三段逻辑：①国内现实看广西/云南产糖、销量、销糖率和工业库存；②进口连接国际与国内市场；③CASDE全国年度估计/预测只作背景。海外减产与国内高库存可能同时存在，不将印度产量直接当作郑糖供给。",
                          formats["note"])
    sheet.print_area(0, 0, 17 if frames.get("china_sugar") else 14, 11)


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


def _focus_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "event_id" not in events:
        return pd.DataFrame(columns=events.columns)
    focus = events[events["event_id"].astype(str).isin(FOCUS_HISTORY_EVENTS)].copy()
    order = {event_id: index for index, event_id in enumerate(FOCUS_HISTORY_EVENTS)}
    focus["_focus_order"] = focus["event_id"].astype(str).map(order)
    return focus.sort_values("_focus_order").drop(columns="_focus_order")


def _event_price_returns(
    price_paths: pd.DataFrame,
    event_id: str,
    commodity: str,
) -> dict[int, float]:
    if price_paths.empty:
        return {}
    group = price_paths[
        price_paths.get("event_id", pd.Series(dtype=str)).astype(str).eq(event_id)
        & price_paths.get("commodity", pd.Series(dtype=str)).eq(commodity)
    ].copy()
    if group.empty or "relative_month" not in group or "price_index" not in group:
        return {}
    group["relative_month"] = pd.to_numeric(group["relative_month"], errors="coerce")
    group["price_index"] = pd.to_numeric(group["price_index"], errors="coerce")
    indexed = group.dropna(subset=["relative_month", "price_index"]).drop_duplicates(
        "relative_month", keep="last"
    ).set_index("relative_month")["price_index"]
    base = indexed.get(0)
    if pd.isna(base) or float(base) == 0:
        return {}
    return {
        horizon: float(indexed.get(horizon)) / float(base) - 1
        for horizon in (6, 12, 18)
        if pd.notna(indexed.get(horizon))
    }


def _cta_return(
    cta_summary: pd.DataFrame,
    event_id: str,
    commodity: str,
    speed: str,
) -> float | None:
    row = _lookup_metric(
        cta_summary,
        event_id=event_id,
        commodity=commodity,
        speed=speed,
        horizon_months=12,
    )
    if row is None:
        return None
    value = pd.to_numeric(pd.Series([row.get("net_return")]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else None


def _format_percent(value: float | None) -> str:
    return "未覆盖" if value is None or pd.isna(value) else f"{value:+.1%}"


def _format_price_returns(values: dict[int, float]) -> str:
    return "｜".join(f"{horizon}M {_format_percent(values.get(horizon))}" for horizon in (6, 12, 18))


def _format_cta_returns(cta_summary: pd.DataFrame, event_id: str, commodity: str) -> str:
    medium = _cta_return(cta_summary, event_id, commodity, "medium")
    slow = _cta_return(cta_summary, event_id, commodity, "slow")
    return f"中速12M {_format_percent(medium)}\n慢速12M {_format_percent(slow)}"


def _sugar_yield_note(evidence: pd.DataFrame, event_id: str) -> str:
    values: list[str] = []
    for country_cn, country in (("巴西", "Brazil"), ("印度", "India")):
        row = _lookup_metric(
            evidence,
            event_id=event_id,
            commodity="sugar",
            metric_id=f"crop:{country}:Sugar cane:Yield:y1:yoy",
        )
        numeric = None if row is None else pd.to_numeric(
            pd.Series([row.get("value")]), errors="coerce"
        ).iloc[0]
        if pd.notna(numeric):
            values.append(f"{country_cn}{float(numeric):+.1%}")
    return "项目FAOSTAT次年甘蔗单产：" + ("、".join(values) if values else "未覆盖")


def _event_takeaway(
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
    event_id: str,
) -> str:
    palm = _event_price_returns(price_paths, event_id, "palm_oil")
    sugar = _event_price_returns(price_paths, event_id, "sugar")
    palm_cta = _cta_return(cta_summary, event_id, "palm_oil", "medium")
    sugar_cta = _cta_return(cta_summary, event_id, "sugar", "medium")
    palm_12, sugar_12 = palm.get(12), sugar.get(12)
    if palm_cta is not None and sugar_cta is not None and palm_cta < 0 and sugar_cta < 0:
        return "强厄尔尼诺也会出现两个品种CTA同时亏损；趋势路径比气候标签更重要。"
    if palm_12 is not None and sugar_12 is not None and (palm_12 > 0) != (sugar_12 > 0):
        return "同一次气候冲击下两个品种12M价格方向相反，不能由ENSO直接推出交易方向。"
    if (
        palm_12 is not None and sugar_12 is not None and palm_12 < 0 and sugar_12 < 0
        and palm_cta is not None and sugar_cta is not None and palm_cta > 0 and sugar_cta > 0
    ):
        return "价格均下跌但中速CTA均盈利，CTA收益来自双向趋势而不是只做多。"
    return "价格与CTA必须逐品种确认；气候只提供风险背景，不直接生成交易信号。"


def _history_summary_texts(
    focus: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
) -> tuple[str, str, str]:
    event_ids = focus.get("event_id", pd.Series(dtype=str)).astype(str).tolist()

    def price_stat(commodity: str, horizon: int) -> tuple[int, int]:
        values = [
            _event_price_returns(price_paths, event_id, commodity).get(horizon)
            for event_id in event_ids
        ]
        valid = [value for value in values if value is not None and pd.notna(value)]
        return sum(value > 0 for value in valid), len(valid)

    palm_6, palm_6_n = price_stat("palm_oil", 6)
    palm_18, palm_18_n = price_stat("palm_oil", 18)
    sugar_stats = [price_stat("sugar", horizon) for horizon in (6, 12, 18)]
    cta_values = [
        _cta_return(cta_summary, event_id, commodity, "medium")
        for event_id in event_ids
        for commodity in ("palm_oil", "sugar")
    ]
    cta_valid = [value for value in cta_values if value is not None and pd.notna(value)]
    cta_positive = sum(value > 0 for value in cta_valid)
    palm_text = (
        f"棕榈油｜T+6月仅{palm_6}/{palm_6_n}次上涨，T+18月{palm_18}/{palm_18_n}次上涨。"
        "历史更像滞后传导，不是事件发生后立即做多。"
    )
    sugar_counts = "、".join(
        f"{horizon}M {positive}/{sample}次上涨"
        for horizon, (positive, sample) in zip((6, 12, 18), sugar_stats)
    )
    sugar_text = f"白糖｜{sugar_counts}。印度、巴西和库存条件会改变方向，未形成稳定上涨规律。"
    cta_text = (
        "CTA｜厄尔尼诺不是直接买入信号。"
        f"12个月中速代理共{cta_positive}/{len(cta_valid)}个事件×品种观察盈利；"
        "策略可做多也可做空，必须等待价格趋势形成。"
    )
    return palm_text, sugar_text, cta_text


def _write_focus_event_table(
    sheet: Any,
    focus: pd.DataFrame,
    evidence: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
    formats: dict[str, Any],
    start_row: int,
) -> tuple[int, dict[str, int]]:
    headers = [
        "研报重点事件", "强度与事件窗", "棕榈油价格路径", "棕榈油收益",
        "棕榈油CTA代理", "棕榈油CTA收益", "白糖价格路径", "白糖收益",
        "白糖CTA代理", "白糖CTA收益", "基本面证据", "这次说明什么",
    ]
    rows: list[list[object]] = []
    row_map: dict[str, int] = {}
    for event in focus.itertuples(index=False):
        event_id = str(event.event_id)
        palm_returns = _event_price_returns(price_paths, event_id, "palm_oil")
        sugar_returns = _event_price_returns(price_paths, event_id, "sugar")
        start = pd.to_datetime(event.start_date, errors="coerce")
        end = pd.to_datetime(event.end_date, errors="coerce")
        window = (
            f"峰值ONI {float(event.peak_oni):.2f}\n持续{int(event.duration_seasons)}季\n"
            f"NOAA窗 {start:%Y-%m}—{end:%Y-%m}"
            if pd.notna(start) and pd.notna(end)
            else f"峰值ONI {float(event.peak_oni):.2f}"
        )
        row_map[event_id] = start_row + 1 + len(rows)
        rows.append([
            FOCUS_HISTORY_EVENTS[event_id], window, "", _format_price_returns(palm_returns),
            "", _format_cta_returns(cta_summary, event_id, "palm_oil"),
            "", _format_price_returns(sugar_returns), "",
            _format_cta_returns(cta_summary, event_id, "sugar"),
            REPORT_PALM_YIELD_NOTES[event_id] + "\n" + _sugar_yield_note(evidence, event_id),
            _event_takeaway(price_paths, cta_summary, event_id),
        ])
    _write_table(sheet, start_row, headers, rows, formats)
    for offset in range(1, len(rows) + 1):
        sheet.set_row(start_row + offset, 76)
    return start_row + len(rows) + 1, row_map


def _write_focus_sparklines(
    sheet: Any,
    price_paths: pd.DataFrame,
    cta_paths: pd.DataFrame,
    row_map: dict[str, int],
) -> None:
    source_col = 40
    source_row = 0
    for event_id, visible_row in row_map.items():
        for commodity, price_col, cta_col, price_color in (
            ("palm_oil", 2, 4, "#4472C4"),
            ("sugar", 6, 8, "#8064A2"),
        ):
            price_group = price_paths[
                price_paths.get("event_id", pd.Series(dtype=str)).astype(str).eq(event_id)
                & price_paths.get("commodity", pd.Series(dtype=str)).eq(commodity)
            ].sort_values("relative_month")
            price_values = pd.to_numeric(price_group.get("price_index"), errors="coerce").dropna().tolist()
            if len(price_values) >= 2:
                sheet.write_row(source_row, source_col, price_values)
                sheet.add_sparkline(visible_row, price_col, {
                    "range": xlsxwriter.utility.xl_range(
                        source_row, source_col, source_row, source_col + len(price_values) - 1
                    ),
                    "type": "line", "series_color": price_color, "show_hidden": True,
                })
            else:
                sheet.write(visible_row, price_col, "未覆盖")
            source_row += 1
            cta_group = cta_paths[
                cta_paths.get("event_id", pd.Series(dtype=str)).astype(str).eq(event_id)
                & cta_paths.get("commodity", pd.Series(dtype=str)).eq(commodity)
                & cta_paths.get("speed", pd.Series(dtype=str)).eq("medium")
            ].sort_values("relative_month")
            cta_values = pd.to_numeric(cta_group.get("event_equity"), errors="coerce").dropna().tolist()
            if len(cta_values) >= 2:
                sheet.write_row(source_row, source_col, cta_values)
                sheet.add_sparkline(visible_row, cta_col, {
                    "range": xlsxwriter.utility.xl_range(
                        source_row, source_col, source_row, source_col + len(cta_values) - 1
                    ),
                    "type": "line", "series_color": "#C55A11", "show_hidden": True,
                })
            else:
                sheet.write(visible_row, cta_col, "未覆盖")
            source_row += 1
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})


def _history_pattern_rows(
    focus: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
) -> list[list[object]]:
    event_ids = focus.get("event_id", pd.Series(dtype=str)).astype(str).tolist()

    def price_values(commodity: str, horizon: int) -> list[float]:
        values = [
            _event_price_returns(price_paths, event_id, commodity).get(horizon)
            for event_id in event_ids
        ]
        return [float(value) for value in values if value is not None and pd.notna(value)]

    palm_18 = price_values("palm_oil", 18)
    sugar_12 = price_values("sugar", 12)
    paired = [
        (_event_price_returns(price_paths, event_id, "palm_oil").get(12),
         _event_price_returns(price_paths, event_id, "sugar").get(12))
        for event_id in event_ids
    ]
    valid_pairs = [(palm, sugar) for palm, sugar in paired if palm is not None and sugar is not None]
    opposite = sum((palm > 0) != (sugar > 0) for palm, sugar in valid_pairs)
    cta = [
        _cta_return(cta_summary, event_id, commodity, "medium")
        for event_id in event_ids
        for commodity in ("palm_oil", "sugar")
    ]
    cta = [float(value) for value in cta if value is not None and pd.notna(value)]
    return [
        ["气候强度不能决定商品方向", f"12M价格方向相反 {opposite}/{len(valid_pairs)}次",
         "较高", "必须分别看棕榈油与白糖基本面和价格", "不能把强厄尔尼诺直接翻译成统一多头"],
        ["棕榈油存在延迟上涨特征", f"T+18月上涨 {sum(value > 0 for value in palm_18)}/{len(palm_18)}次",
         "中等（仅4次）", "用于提示减产可能滞后进入价格", "不能据此在T0立即做多"],
        ["白糖未形成稳定上涨规律", f"T+12月上涨 {sum(value > 0 for value in sugar_12)}/{len(sugar_12)}次",
         "较高", "继续依赖印度/巴西、库存和糖醇比验证", "不能使用“厄尔尼诺=多糖”规则"],
        ["CTA收益来自趋势而非气候方向", f"中速12M盈利 {sum(value > 0 for value in cta)}/{len(cta)}个观察",
         "中等（代理回测）", "价格形成趋势后再看中慢速是否确认", "不能把气候事件当成CTA入场信号"],
    ]


def _write_history_pattern_table(
    sheet: Any,
    start_row: int,
    headers: list[str],
    rows: list[list[object]],
    formats: dict[str, Any],
) -> None:
    column_groups = ((0, 1), (2, 3), (4, 5), (6, 8), (9, 11))
    for value, (first_col, last_col) in zip(headers, column_groups):
        sheet.merge_range(start_row, first_col, start_row, last_col, value, formats["header"])
    for offset, values in enumerate(rows, start=1):
        for value, (first_col, last_col) in zip(values, column_groups):
            sheet.merge_range(
                start_row + offset,
                first_col,
                start_row + offset,
                last_col,
                "" if pd.isna(value) else value,
                formats["body"],
            )


def _expanded_chain_path(
    paths: pd.DataFrame,
    event_id: str,
    chain_id: str,
    value_column: str,
    speed: str | None = None,
) -> list[float]:
    if paths.empty:
        return []
    current = paths[
        paths.get("event_id", pd.Series(dtype=str)).astype(str).eq(event_id)
    ].copy()
    if speed is not None and "speed" in current:
        current = current[current["speed"].astype(str).eq(speed)]
    current["chain_id"] = current.get("commodity", pd.Series(dtype=str)).map(PRICE_CHAIN_MAP)
    current = current[current["chain_id"].eq(chain_id)]
    if current.empty or value_column not in current or "relative_month" not in current:
        return []
    current[value_column] = pd.to_numeric(current[value_column], errors="coerce")
    current["relative_month"] = pd.to_numeric(current["relative_month"], errors="coerce")
    return current.groupby("relative_month")[value_column].mean().dropna().sort_index().tolist()


def _expanded_row_takeaway(row: pd.Series) -> str:
    weather = _safe_float(row.get("weather_precip_anomaly"))
    crop_yield = _safe_float(row.get("yield_deviation"))
    price = _safe_float(row.get("price_6m"))
    cta = _safe_float(row.get("cta_medium_6m"))
    full_chain = (
        weather is not None and weather < 0
        and crop_yield is not None and crop_yield < 0
        and price is not None and price > 0
        and cta is not None and cta > 0
    )
    if full_chain:
        return "本次完整传导成立；仍需跨事件复核，不能单次外推。"
    if cta is not None and cta > 0 and price is not None and price < 0:
        return "CTA捕捉到下跌趋势；收益来自双向跟随，不是气候做多。"
    if cta is not None and cta > 0:
        return "CTA本次获利，但前端天气与单产没有形成完整一致链路。"
    return "完整传导未成立，且CTA未稳定捕捉；作为反例保留。"


def add_expanded_history_sheet(
    workbook: xlsxwriter.Workbook,
    frames: dict[str, object],
    formats: dict[str, Any],
) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[2])
    sheet.set_tab_color("#8064A2")
    write_title(
        sheet,
        "02｜四次重点厄尔尼诺：六条中国可交易作物链复盘",
        "每行同时看天气、单产、国际价格代理和CTA；大豆、菜籽链内先汇总，不重复增加证据",
        formats,
    )
    matrix = frames["expanded_matrix"].copy()
    patterns = frames["expanded_patterns"].copy()
    stable = patterns[patterns.get("evidence_strength", pd.Series(dtype=str)).eq("STABLE_CONVERSION")]
    cta_tendency = patterns[patterns.get("cta_tendency", pd.Series(dtype=str)).eq("CTA_TENDENCY")]
    cta_names = "、".join(cta_tendency.get("chain_name_cn", pd.Series(dtype=str)).astype(str)) or "无"
    sheet.merge_range(3, 0, 3, 11, "领导先看三个结论", formats["section"])
    sheet.merge_range(
        4, 0, 6, 3,
        f"完整传导｜{len(stable)}/6条作物链达到稳定门槛。当前结论：历史不支持把厄尔尼诺直接翻译成商品做多。",
        formats["summary_blue"],
    )
    sheet.merge_range(
        4, 4, 6, 7,
        f"CTA趋势机会倾向｜{cta_names}的Medium月频代理在四次重点事件中至少3次盈利。它只说明较常出现可跟随趋势，不决定多空。",
        formats["summary_gold"],
    )
    sheet.merge_range(
        4, 8, 6, 11,
        "配置边界｜这是国际现货价格代理的小样本复盘；新增结果不自动修改当前配置，需等待国内期货与实时基本面确认。",
        formats["summary_gray"],
    )

    headers = [
        "重点事件", "作物链", "峰值ONI", "产区降雨异常", "单产偏离前5年",
        "价格路径", "6M价格", "12M价格", "CTA净值路径", "Medium 6M", "Slow 6M", "领导怎么读",
    ]
    rows: list[list[object]] = []
    visible_rows: list[tuple[int, str, str]] = []
    for _, item in matrix.iterrows():
        event_id = str(item.get("event_id"))
        chain_id = str(item.get("chain_id"))
        visible_rows.append((8 + len(rows), event_id, chain_id))
        rows.append([
            FOCUS_HISTORY_EVENTS.get(event_id, event_id),
            item.get("chain_name_cn", COMMODITY_CN.get(chain_id, chain_id)),
            item.get("peak_oni"), item.get("weather_precip_anomaly"), item.get("yield_deviation"),
            "", item.get("price_6m"), item.get("price_12m"), "",
            item.get("cta_medium_6m"), item.get("cta_slow_6m"), _expanded_row_takeaway(item),
        ])
    _write_table(
        sheet, 7, headers, rows, formats,
        column_formats={2: formats["number"], 3: formats["percent"], 4: formats["percent"],
                        6: formats["percent"], 7: formats["percent"], 9: formats["percent"],
                        10: formats["percent"]},
    )
    source_col = 40
    source_row = 0
    for visible_row, event_id, chain_id in visible_rows:
        price_values = _expanded_chain_path(
            frames["expanded_price_paths"], event_id, chain_id, "price_index"
        )
        cta_values = _expanded_chain_path(
            frames["expanded_cta_paths"], event_id, chain_id, "event_equity", speed="medium"
        )
        for values, visible_col, color in (
            (price_values, 5, "#4472C4"), (cta_values, 8, "#C55A11")
        ):
            if len(values) >= 2:
                sheet.write_row(source_row, source_col, values)
                sheet.add_sparkline(visible_row, visible_col, {
                    "range": xlsxwriter.utility.xl_range(
                        source_row, source_col, source_row, source_col + len(values) - 1
                    ),
                    "type": "line", "series_color": color, "show_hidden": True,
                })
            else:
                sheet.write(visible_row, visible_col, "未覆盖", formats["center"])
            source_row += 1
        sheet.set_row(visible_row, 44)
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})

    pattern_row = 9 + len(rows)
    sheet.merge_range(pattern_row, 0, pattern_row, 11, "跨事件结论：完整传导与CTA机会必须分开", formats["section"])
    pattern_headers = ["作物链", "完整传导成功", "有效事件", "CTA盈利比例", "结论"]
    pattern_rows = []
    for _, item in patterns.iterrows():
        conversion_count = int(item.get("conversion_valid_events", 0) or 0)
        success_count = int(item.get("conversion_success_count", 0) or 0)
        cta_share = item.get("cta_medium_positive_share", pd.NA)
        verdict = (
            "CTA趋势机会倾向；不支持气候直接定方向"
            if item.get("cta_tendency") == "CTA_TENDENCY"
            else "CTA结果混合；不支持气候直接定方向"
        )
        pattern_rows.append([
            item.get("chain_name_cn", item.get("chain_id")),
            f"{success_count}/{conversion_count}", item.get("valid_events"), cta_share, verdict,
        ])
    _write_table(sheet, pattern_row + 1, pattern_headers, pattern_rows, formats,
                 column_formats={3: formats["percent"]})
    boundary_row = pattern_row + len(pattern_rows) + 4
    sheet.merge_range(
        boundary_row, 0, boundary_row, 11,
        "结论：没有稳定的气候→减产→上涨→CTA完整传导；棕榈油、白糖、棉花存在CTA趋势机会倾向。"
        "该倾向可能来自上涨或下跌趋势，不能作为商品多头规则，也不自动修改当前配置。",
        formats["note"],
    )
    sheet.set_row(boundary_row, 42)
    sheet.set_column("A:B", 16)
    sheet.set_column("C:E", 15)
    sheet.set_column("F:F", 17)
    sheet.set_column("G:H", 14)
    sheet.set_column("I:I", 17)
    sheet.set_column("J:K", 14)
    sheet.set_column("L:L", 38)
    sheet.freeze_panes(8, 0)
    sheet.print_area(0, 0, boundary_row, 11)
    sheet.fit_to_pages(1, 2)


def add_history_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    if not frames.get("expanded_matrix", pd.DataFrame()).empty:
        add_expanded_history_sheet(workbook, frames, formats)
        return
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[2])
    sheet.set_tab_color("#8064A2")
    write_title(
        sheet,
        "02｜四次强厄尔尼诺复盘：历史到底说明什么",
        "只展示研报重点事件；价格与中速CTA迷你图均为T-6月至T+18月，T0为NOAA事件起点",
        formats,
    )
    focus = _focus_events(frames["events"])
    sheet.merge_range(3, 0, 3, 11, "领导先看结论（Executive Summary）", formats["section"])
    palm_text, sugar_text, cta_text = _history_summary_texts(
        focus, frames["price_paths"], frames["cta_summary"]
    )
    sheet.merge_range(4, 0, 7, 3, palm_text, formats["summary_blue"])
    sheet.merge_range(4, 4, 7, 7, sugar_text, formats["summary_gold"])
    sheet.merge_range(4, 8, 7, 11, cta_text, formats["summary_gray"])
    sheet.set_row(4, 24)
    sheet.set_row(5, 24)
    sheet.set_row(6, 24)
    sheet.set_row(7, 24)

    sheet.merge_range(9, 0, 9, 11, "四次重点事件：气候 → 基本面 → 价格 → CTA", formats["section"])
    end_row, row_map = _write_focus_event_table(
        sheet, focus, frames["evidence"], frames["price_paths"], frames["cta_summary"], formats, 10
    )
    _write_focus_sparklines(sheet, frames["price_paths"], frames["cta_paths"], row_map)

    pattern_row = max(end_row + 2, 17)
    sheet.merge_range(pattern_row, 0, pattern_row, 11, "这四次真正告诉我们什么", formats["section"])
    pattern_headers = ["规律", "四次样本结果", "判断强度", "可以怎么用", "不能怎么用"]
    pattern_rows = _history_pattern_rows(focus, frames["price_paths"], frames["cta_summary"])
    _write_history_pattern_table(sheet, pattern_row + 1, pattern_headers, pattern_rows, formats)
    for row in range(pattern_row + 2, pattern_row + 2 + len(pattern_rows)):
        sheet.set_row(row, 38)

    boundary_row = pattern_row + 7
    sheet.merge_range(boundary_row, 0, boundary_row, 11, "使用边界与下一步", formats["section"])
    sheet.merge_range(
        boundary_row + 1, 0, boundary_row + 1, 11,
        "怎么用：历史复盘用于理解传导顺序——气候只是背景，基本面决定落地，价格确认方向，CTA等待趋势。",
        formats["body"],
    )
    sheet.merge_range(
        boundary_row + 2, 0, boundary_row + 2, 11,
        "暂不做：本页不修改当前CTA配置、不做置信度修正；其余事件保留在底层数据，仅供审计和稳健性检查。",
        formats["note"],
    )
    sheet.merge_range(
        boundary_row + 3, 0, boundary_row + 3, 11,
        "口径与来源：重点事件来自华泰期货《厄尔尼诺下全球农业产区天气及历史对比》（2026-05-11）；"
        "事件窗与峰值采用NOAA ONI，价格采用World Bank月度现货代理，CTA为月度Medium/Slow成本后研究代理。"
        "研报NINO3.4峰值与NOAA ONI因口径和数据版本不同，不应直接混用。",
        formats["note"],
    )
    sheet.set_row(boundary_row + 3, 46)
    sheet.set_column("A:A", 13)
    sheet.set_column("B:B", 19)
    sheet.set_column("C:C", 14)
    sheet.set_column("D:D", 22)
    sheet.set_column("E:E", 14)
    sheet.set_column("F:F", 18)
    sheet.set_column("G:G", 14)
    sheet.set_column("H:H", 22)
    sheet.set_column("I:I", 14)
    sheet.set_column("J:J", 18)
    sheet.set_column("K:K", 31)
    sheet.set_column("L:L", 34)
    sheet.freeze_panes(11, 0)
    sheet.print_area(0, 0, boundary_row + 3, 11)


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
    weather_snapshot = frames.get("current_weather_snapshot", pd.DataFrame())
    visible_last_row = 14
    if not weather_snapshot.empty:
        weather_path = frames.get("current_weather_path", pd.DataFrame())
        weather_start = 16
        sheet.merge_range(weather_start, 0, weather_start, 11, "六条作物链当前主产区天气代理", formats["section"])
        sheet.merge_range(
            weather_start + 1, 0, weather_start + 1, 11,
            "30日降雨距平=最近30日代表点累计降雨相对1991—2020同月常态的偏离；低于-10%称偏干，高于+10%称偏湿。它只回答主产区水分环境，尚未按作物生育期或种植区权重聚合，不能直接等同减产。",
            formats["note"],
        )
        state_cn = {"DRY": "偏干", "WET": "偏湿", "NEAR_NORMAL": "接近常态"}
        weather_rows = []
        for item in weather_snapshot.itertuples(index=False):
            chain_id = str(item.chain_id)
            state = str(item.state)
            weather_rows.append([
                COMMODITY_CN.get(chain_id, getattr(item, "chain_name_cn", chain_id)),
                getattr(item, "precip_anomaly_pct", ""),
                getattr(item, "soil_wetness_anomaly", ""),
                state_cn.get(state, state),
                getattr(item, "coverage_pct", ""),
                getattr(item, "date", ""),
                getattr(item, "source", "NASA POWER"), "",
                getattr(item, "limitation", "30日代表点代理；尚未按作物生育期或种植区权重聚合"),
            ])
        weather_header_row = weather_start + 2
        _write_table(
            sheet,
            weather_header_row,
            ["作物链", "30日降雨距平", "土壤湿度距平", "当前状态", "数据覆盖", "数据截至", "来源", "迷你趋势", "领导怎么读"],
            weather_rows,
            formats,
            column_formats={1: formats["percent"], 4: formats["percent"]},
        )
        for offset, row in enumerate(weather_rows, start=1):
            chain_id = next(key for key, label in COMMODITY_CN.items() if label == row[0])
            values = pd.to_numeric(
                weather_path.loc[weather_path.get("chain_id").eq(chain_id), "precip_anomaly_pct"]
                if not weather_path.empty and "chain_id" in weather_path and "precip_anomaly_pct" in weather_path
                else pd.Series(dtype=float),
                errors="coerce",
            ).dropna().tail(60).tolist()
            if not _add_row_sparkline(sheet, weather_header_row + offset, 7, values, 100 + offset, source_col, "#00A6A6"):
                sheet.write(weather_header_row + offset, 7, "少于2期", formats["center"])
        visible_last_row = weather_header_row + len(weather_rows)
    sheet.set_column("A:A", 30)
    sheet.set_column("B:B", 28)
    sheet.set_column("C:D", 16)
    sheet.set_column("E:G", 27)
    sheet.set_column("H:H", 16)
    sheet.set_column("I:K", 32)
    sheet.freeze_panes(5, 0)
    sheet.print_area(0, 0, visible_last_row, 11)


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


def _sugar_visible(frames: dict[str, object], name: str) -> pd.DataFrame:
    data = frames.get("china_sugar", {}).get(name, pd.DataFrame()).copy()
    if data.empty:
        return data
    cutoff = pd.Timestamp(frames.get("workbook_as_of", frames["monitor"].get("as_of")))
    column = "published_at" if "published_at" in data else "date"
    return data[pd.to_datetime(data[column], errors="coerce").le(cutoff)].copy()


def _domestic_sugar_headline(frames: dict[str, object]) -> str:
    history = _sugar_visible(frames, "province_history")
    current = current_province_rows(history, frames.get("workbook_as_of", frames["monitor"].get("as_of")))
    if current.empty:
        evidence = "尚无可用省级实际产销，不能用年度预测填补实际数据"
    else:
        stocks = current[current.metric.eq("inventory")]
        evidence = "；".join(f"{r.region}：{r.direction}" for r in stocks.itertuples(index=False))
    return "白糖国内｜" + evidence + "。全国实际产销库存未补齐；海外偏紧不等于国内郑糖偏紧。本次仅补充研究证据，不自动调整CTA配置或历史置信度。"


def write_domestic_sugar_rows(sheet: Any, frames: dict[str, object], start: int, formats: dict[str, Any]) -> int:
    history = _sugar_visible(frames, "province_history")
    current = current_province_rows(history, frames["workbook_as_of"])
    sheet.merge_range(start, 0, start, 12, "L2-1｜白糖国内实际：先看自己生产多少、卖得多快、剩多少", formats["section"])
    sheet.merge_range(start + 1, 0, start + 1, 12,
                      "读图提示：迷你图只展示同一榨季已取得月份；库存月度下降不等于库存偏低，还要看同比。历史同月样本不足，季节分位不计算；下列方向是同比描述，不是买卖信号。", formats["note"])
    sheet.set_row(start + 1, 32)
    headers = ["地区", "环节", "实际指标", "当前值", "迷你趋势", "单位", "上年同期", "同比变化", "方向（描述）", "方向怎么算", "观测截至", "发布/来源", "指标含义与限制"]
    rows = []
    for item in current.itertuples(index=False):
        change = f"{item.change:+.2f}个百分点" if item.metric == "sales_rate" else _format_percent(item.yoy) if pd.notna(item.yoy) else "缺基准"
        rows.append([item.region, item.category, item.label, item.value, "", item.unit,
                     item.prior_value, change, item.direction, item.direction_reason, item.date,
                     f"{item.published_at}\n沐甜公开产销（点击原文）", item.limitation])
    header = start + 2
    _write_table(sheet, header, headers, rows, formats, column_formats={3: formats["number"], 6: formats["number"]})
    for offset, item in enumerate(current.itertuples(index=False), start=1):
        row = header + offset
        path = history[(history.region == item.region) & (history.metric == item.metric) & (history.crop_year == item.crop_year)].sort_values(["date", "published_at"]).drop_duplicates("date", keep="last")
        values = pd.to_numeric(path.value, errors="coerce").tolist()
        if not _add_row_sparkline(sheet, row, 4, values, 40 + offset, 52, "#4472C4"):
            sheet.write(row, 4, "少于2期", formats["center"])
        sheet.write_url(row, 11, item.source_url, formats["link"], f"{item.published_at}\n沐甜：原文")
        sheet.set_row(row, 56)
    cursor = header + len(rows) + 2
    if current.empty:
        sheet.merge_range(cursor, 0, cursor, 12, "未取得可用省级实际数据，不能用海外或年度预测替代。", formats["note"])
        cursor += 2
    sheet.merge_range(cursor, 0, cursor, 12, "L2-2｜全国年度背景：国产糖含甘蔗糖和甜菜糖，估计/预测单列", formats["section"])
    annual = _sugar_visible(frames, "casde_history")
    table_rows = []
    base_year = pd.Timestamp(frames["workbook_as_of"]).year - (pd.Timestamp(frames["workbook_as_of"]).month < 10)
    crops = [f"{year}/{str(year + 1)[-2:]}" for year in (base_year, base_year + 1)]
    selected = annual[annual.published_at.eq(annual.published_at.max())].drop_duplicates(["crop_year", "metric"], keep="last") if not annual.empty else annual
    definitions = [
        ("production", "食糖产量", "全国生产的食糖；不是印度产量，也不是甘蔗原料吨数"),
        ("cane_sugar", "其中：甘蔗糖", "食糖产量的组成部分，不能再与总产量相加"),
        ("beet_sugar", "其中：甜菜糖", "北方甜菜制糖；同样属于国产食糖供给"),
        ("consumption", "消费", "全国年度消费估计，不是糖厂当月销量"),
        ("imports", "进口", "年度进口估计/预测，不是当月实际到港"),
        ("exports", "出口", "全国年度出口估计/预测"),
        ("balance_change", "结余变化", "产量+进口−消费−出口；正值为年度结余增加，不是期末库存"),
    ]
    for metric, label, meaning in definitions:
        values, kinds = [], []
        for crop in crops:
            part = selected[(selected.crop_year == crop) & (selected.metric == metric)] if not selected.empty else pd.DataFrame()
            item = part.iloc[-1] if not part.empty else pd.Series(dtype=object)
            values.append(item.get("value", ""))
            kinds.append({"estimate": "估计", "forecast": "预测", "historical_estimate": "历史估计"}.get(item.get("value_type"), "未取得"))
        table_rows.append(["全国", "年度背景", label, values[0], "", "万吨", values[1], kinds[0] + " / " + kinds[1],
                           "不计实际方向", meaning, selected.published_at.max() if not selected.empty else "", "农业农村部CASDE", "非逐月实际；保留每次发布版本"])
    _write_table(sheet, cursor + 1, ["范围", "用途", "指标", crops[0], "估计修订", "单位", crops[1], "两列类型", "评分边界", "它衡量什么", "报告发布日期", "来源", "限制"], table_rows, formats)
    for offset, (metric, _, _) in enumerate(definitions, start=1):
        row = cursor + 1 + offset
        series = annual[(annual.crop_year == crops[0]) & (annual.metric == metric)].sort_values("published_at").drop_duplicates("published_at", keep="last") if not annual.empty else pd.DataFrame()
        if not series.empty:
            values = series.value.tolist()
            if not _add_row_sparkline(sheet, row, 4, values, 60 + offset, 52, "#A5A5A5"):
                sheet.write(row, 4, "仅1个版本", formats["center"])
            sheet.write_url(row, 11, str(series.iloc[-1].source_url), formats["link"], "农业农村部：原表")
        sheet.set_row(row, 42)
    cursor += len(table_rows) + 3
    sheet.merge_range(cursor, 0, cursor, 12,
                      "年度迷你图表示同一榨季估计值的版本修订，不是每月生产曲线；全国实际产销库存未补齐，不能用“结余变化”补成期末库存。", formats["note"])
    sheet.set_row(cursor, 32)
    cursor += 2
    imports = _sugar_visible(frames, "imports")
    import_text = "进口当月实际数据未取得。"
    if not imports.empty:
        item = imports.sort_values("date").iloc[-1]
        import_text = f"{item.date}当月进口约{item.value:g}万吨（{item.published_at}公开转载）；该数仍需海关原始明细核验，不纳入评分。"
    sheet.merge_range(cursor, 0, cursor, 12, "L2-3｜进口传导与尚缺证据", formats["section"])
    sources = frames.get("china_sugar_manifest", {}).get("sources", {})
    cost_dates = "；".join(f"{label}接口截至{sources[key].get('data_as_of', '未知')}" for key, label in (("import_inquota", "配额内"), ("import_outquota", "配额外")) if key in sources)
    sheet.merge_range(cursor + 1, 0, cursor + 2, 12, import_text + (cost_dates or "未取得当前进口成本序列") + "。历史值不冒充当前成本；缺进口成本、政策约束和全国实际库存时，无法判断海外减产能传导多少到郑糖。", formats["note"])
    sheet.set_row(cursor + 1, 26)
    sheet.set_row(cursor + 2, 26)
    sheet.set_column(40, 440, None, None, {"hidden": True})
    return cursor + 4


def add_l2_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[4])
    sheet.set_tab_color("#70AD47")
    write_title(sheet, "04｜L2 供给基本面", "本页回答：天气是否已经转化成产量、需求和库存变化；迷你图紧邻当前值", formats)
    sheet.merge_range(3, 0, 3, 12, "判断顺序：白糖先看国内实际产销库存，再看进口如何传导海外影响；印度/巴西只是海外样本，不能直接代表国内郑糖供需。年度估计/预测只作背景。", formats["note"])
    sheet.merge_range(4, 0, 4, 12, "海外六格的季节分位怎么算：月度指标与历史同月比较；UNICA与历史同榨季月份比较；年度USDA指标在历史年度中排名。分位=不高于当前值的有效历史样本数÷有效样本总数。", formats["note"])
    sheet.merge_range(5, 0, 5, 12, "海外六格的方向怎么算：供给/库存低位→偏紧，高位→偏松；需求低位→偏松，高位→偏紧。低位≤33%，高位≥67%；评分指标冲突→中性，含预测或缺失→证据不足。", formats["note"])
    start = write_domestic_sugar_rows(sheet, frames, 8, formats) if frames.get("china_sugar") else 8
    sheet.merge_range(start, 0, start, 11, "海外样本六格｜马来棕榈油 / 印度巴西与全球食糖（非国内郑糖结论）", formats["section"])
    write_l2_summary_rows(sheet, frames["l2_summary"], frames["fundamentals"], start + 1, formats)
    sheet.merge_range(start + 9, 0, start + 9, 11, "海外指标明细", formats["section"])
    detail_start = start + 10
    write_l2_detail_rows(sheet, frames["l2_details"], frames["fundamentals"], detail_start, formats)
    visible_last_row = detail_start + len(frames["l2_details"])
    faostat = frames.get("expanded_baselines", {}).get("faostat_crop_history", pd.DataFrame())
    if not faostat.empty:
        coverage_start = visible_last_row + 2
        sheet.merge_range(coverage_start, 0, coverage_start, 12, "六条作物链基本面覆盖审计", formats["section"])
        sheet.merge_range(
            coverage_start + 1, 0, coverage_start + 1, 12,
            "FAOSTAT产量历史基线用于识别长期敏感度，不代表当前产量预测；棉花、玉米、大豆和菜籽的USDA PSD需求/库存未取得，因此不能判断当前供需缺口。缺失项会保留在表内，不以价格上涨倒推基本面。",
            formats["note"],
        )
        crop_items = {
            "palm_oil": "Oil palm fruit", "sugar": "Sugar cane",
            "cotton": "Seed cotton, unginned", "corn": "Maize (corn)",
            "soybean": "Soya beans", "rapeseed": "Rape or colza seed",
        }
        coverage_rows: list[list[object]] = []
        supply_series: dict[str, list[float]] = {}
        for chain_id, item_name in crop_items.items():
            crop = faostat[
                (faostat["item"].astype(str) == item_name)
                & (faostat["element"].astype(str) == "Production")
            ].copy()
            crop["year"] = pd.to_numeric(crop.get("year"), errors="coerce")
            crop["value"] = pd.to_numeric(crop.get("value"), errors="coerce")
            annual = crop.dropna(subset=["year", "value"]).groupby("year", as_index=False)["value"].sum().sort_values("year")
            supply_series[chain_id] = annual["value"].tail(30).tolist()
            latest = annual.iloc[-1] if not annual.empty else pd.Series()
            latest_year = int(latest["year"]) if not latest.empty else ""
            latest_value = latest.get("value", "")
            unit = str(crop["unit"].dropna().iloc[-1]) if not crop.empty and "unit" in crop else ""
            coverage_rows.append([
                COMMODITY_CN[chain_id], "供给", "历史基线可用；当前判断缺失",
                latest_value, "", f"{unit}；{latest_year}", "FAOSTAT",
                "主产区合计产量历史，用于复盘长期敏感度",
                "最新为年度历史实绩，不能替代当前作物年度预测",
                "接入USDA PSD或各国月度/季度官方产量预测",
                "当前供给方向仍不能确认",
            ])
            for category in ("需求", "库存"):
                if chain_id in {"palm_oil", "sugar"}:
                    coverage_rows.append([
                        COMMODITY_CN[chain_id], category, "海外/全球样本；非国内完整覆盖", "", "", "",
                        "MPOB/USDA/Wind既有监测", "上方按同季节分位判断",
                        "部分年度USDA指标含预测，已在明细中标注",
                        "按发布频率持续刷新", "可与供给共同使用，但预测不单独确认",
                    ])
                else:
                    coverage_rows.append([
                        COMMODITY_CN[chain_id], category, "缺失", "", "", "", "USDA PSD（待接入）",
                        "USDA PSD需求/库存未取得",
                        "新API需有效API Key；官方bulk下载本次超时",
                        "配置USDA PSD API Key或重试官方bulk文件",
                        "不能判断当前供需缺口",
                    ])
        coverage_header_row = coverage_start + 2
        _write_table(
            sheet,
            coverage_header_row,
            ["作物链", "类别", "覆盖状态", "最新值", "迷你趋势", "单位/年份", "来源", "它衡量什么", "为什么没有/限制", "怎么补齐", "对结论影响"],
            coverage_rows,
            formats,
        )
        source_col = 360
        for index, row in enumerate(coverage_rows, start=1):
            if row[1] != "供给":
                continue
            chain_id = next(key for key, label in COMMODITY_CN.items() if label == row[0])
            values = supply_series.get(chain_id, [])
            if len(values) < 2:
                sheet.write(coverage_header_row + index, 4, "少于2期", formats["center"])
                continue
            source_row = 250 + index
            sheet.write_row(source_row, source_col, values)
            sheet.add_sparkline(coverage_header_row + index, 4, {
                "range": xlsxwriter.utility.xl_range(source_row, source_col, source_row, source_col + len(values) - 1),
                "type": "line", "series_color": "#70AD47", "show_hidden": True,
            })
        sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})
        visible_last_row = coverage_header_row + len(coverage_rows)
    sheet.set_column("A:B", 14)
    sheet.set_column("C:C", 26)
    sheet.set_column("D:E", 15)
    sheet.set_column("F:I", 14)
    sheet.set_column("J:J", 48)
    sheet.set_column("K:K", 15)
    sheet.set_column("L:M", 28)
    sheet.freeze_panes(10, 0)
    sheet.print_area(0, 0, visible_last_row, 12)


def _flatten(prefix: str, value: object, output: list[tuple[str, object]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, output)
    elif not isinstance(value, list):
        output.append((prefix, value))


def _futures_history(frames: dict[str, object], symbol: str) -> pd.DataFrame:
    expanded = frames.get("expanded_futures", {}).get(
        f"{symbol}_continuous_exploratory", pd.DataFrame()
    )
    if not expanded.empty:
        return expanded.copy()
    for name in (f"{symbol}_active_wind", f"{symbol}_dominant_tushare", f"{symbol}_continuous_exploratory"):
        frame = frames["futures"].get(name, pd.DataFrame())
        if not frame.empty:
            return frame.copy()
    return pd.DataFrame()


def _research_signal(frames: dict[str, object], symbol: str, speed: str) -> pd.DataFrame:
    return frames["signals"].get(f"{symbol}_{speed}_proxy_research", pd.DataFrame()).copy()


def _latest_cta_signal(
    frames: dict[str, object], symbol: str, speed: str
) -> tuple[pd.DataFrame, str]:
    expanded = frames.get("expanded_signals", {}).get(
        f"{symbol}_{speed}_proxy_exploratory", pd.DataFrame()
    )
    if not expanded.empty:
        return expanded.copy(), "AkShare/Sina连续合约CTA代理（探索性）"
    research = _research_signal(frames, symbol, speed)
    if not research.empty:
        return research, "Tushare主力映射CTA代理（正式研究口径）"
    return pd.DataFrame(), "未取得"


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
    expanded_mode = bool(frames.get("expanded_futures"))
    symbols = UNIVERSE_SYMBOLS if expanded_mode else ("P", "SR")
    write_title(
        sheet,
        "05｜L3 市场定价",
        "本页回答：八个国内期货合约是否已开始为供需变化定价；每行迷你图展示最近60个交易日"
        if expanded_mode else
        "本页回答：棕榈油和白糖价格是否已开始为供需变化定价；迷你图展示主力合约近段走势",
        formats,
    )
    sheet.merge_range(3, 0, 3, 11, "期货价格=市场对未来供需的交易结果；Contango表示远月贵于近月，通常不支持眼前紧缺。AkShare/Sina连续合约用于补齐最新方向，但换月与复权规则未公开，因此标为探索性；现货基差尚未正式接入。", formats["note"])
    headers = [
        "品种", "行情口径", "收盘价", "价格趋势", "20日涨跌", "60日涨跌",
        "20日年化波动", "60日最大回撤", "前后月结构", "前后月价差", "数据截至", "领导怎么读",
    ]
    market = frames["monitor"].get("layers", {}).get("market", {})
    rows: list[list[object]] = []
    for symbol in symbols:
        item = market.get(symbol, {})
        history = _futures_history(frames, symbol).sort_values("date")
        if not history.empty:
            history["date"] = pd.to_datetime(history["date"], errors="coerce")
            history = history.dropna(subset=["date"]).sort_values("date")
        latest = history.iloc[-1] if not history.empty else pd.Series()
        provider = str(latest.get("provider", ""))
        is_wind = provider == "Wind EDB"
        is_sina = provider == "akshare_sina_continuous"
        if is_sina:
            price_basis = "AkShare/Sina连续合约（探索性）"
        elif is_wind:
            code = latest.get("indicator_code", "")
            price_basis = f"Wind活跃合约（{code}）" if code else "Wind活跃合约"
        else:
            price_basis = item.get("contract", latest.get("contract", ""))
        term = {} if is_wind or is_sina else item.get("term_structure", {})
        latest_date = latest.get("date", item.get("date"))
        monitor_as_of = pd.to_datetime(frames.get("workbook_as_of", frames["monitor"].get("as_of")), errors="coerce")
        parsed_latest_date = pd.to_datetime(latest_date, errors="coerce")
        staleness = (
            max(0, int((monitor_as_of - parsed_latest_date).days))
            if pd.notna(monitor_as_of) and pd.notna(parsed_latest_date)
            else item.get("staleness_days")
        )
        source_text = (
            "数据来源：AkShare/Sina；换月与复权规则未公开，仅作最新方向观察"
            if is_sina else
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
            term.get("curve", "未取得（连续/活跃合约序列不含月差）" if is_wind or is_sina else "未取得"),
            term.get("front_second_spread_pct", ""), latest_date or "", stale_text,
        ])
    _write_table(
        sheet, 4, headers, rows, formats,
        column_formats={4: formats["percent"], 5: formats["percent"], 6: formats["percent"],
                        7: formats["percent"], 9: formats["percent"]},
    )
    source_col = 30
    for offset, symbol in enumerate(symbols, start=1):
        history = _futures_history(frames, symbol).sort_values("date")
        values = pd.to_numeric(history.get("close"), errors="coerce").dropna().tail(60).tolist()
        if not _add_row_sparkline(sheet, 4 + offset, 3, values, offset - 1, source_col, "#ED7D31"):
            sheet.write(4 + offset, 3, "少于2期", formats["center"])
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})
    note_row = 5 + len(rows)
    sheet.merge_range(note_row, 0, note_row, 11, "尚缺数据：新增六个合约尚无Tushare/Wind正式连续合约和可审计换月规则；匹配交割地区、等级与合约的现货基差仍缺失，不能单独确认基本面传导或修改CTA配置。", formats["note"])
    prices = _sugar_visible(frames, "price_history")
    if not prices.empty:
        note_row += 2
        sheet.merge_range(note_row, 0, note_row, 11, "白糖现货与国际原糖价格补充｜独立观察，不替换上方期货行情", formats["section"])
        extra_rows = []
        price_paths = []
        for label, column, unit in (("中国食糖现货价格指标", "spot_price", "元/吨"), ("国际原糖价格指标", "raw_sugar_price", "美分/磅")):
            valid = prices.dropna(subset=[column]).sort_values("date")
            if valid.empty:
                continue
            latest = valid.iloc[-1]
            lag = (pd.Timestamp(frames["workbook_as_of"]) - pd.Timestamp(latest.date)).days
            extra_rows.append([label, "沐甜公开价格接口", latest[column], "", unit,
                               _return_over_rows(valid, 20, column), _return_over_rows(valid, 60, column),
                               latest.date, "过期" if lag > 4 else "时效合格", "只作价格旁证", "不计算基差", "地区、等级与合约未匹配；不用于计算CTA代理"])
            price_paths.append(valid[column].tail(60).tolist())
        _write_table(sheet, note_row + 1, ["指标", "来源", "最新值", "60日趋势", "单位", "20日涨跌", "60日涨跌", "观测截至", "时效", "用途", "边界", "为什么"], extra_rows, formats, column_formats={5: formats["percent"], 6: formats["percent"]})
        for offset, path in enumerate(price_paths, start=1):
            _add_row_sparkline(sheet, note_row + 1 + offset, 3, path, 30 + offset, 30, "#ED7D31")
            sheet.set_row(note_row + 1 + offset, 42)
        note_row += len(extra_rows) + 3
        sheet.merge_range(note_row, 0, note_row, 11, "进口成本说明：可下载的配额内/外历史接口已经滞后，未放入当前价格比较；现货指标与连续期货直接相减可能产生伪基差，所以不计算基差。", formats["note"])
        sheet.set_row(note_row, 32)
    sheet.set_column("A:B", 18)
    sheet.set_column("C:C", 13)
    sheet.set_column("D:D", 18)
    sheet.set_column("E:J", 15)
    sheet.set_column("K:K", 15)
    sheet.set_column("L:L", 34)
    sheet.freeze_panes(5, 0)
    sheet.print_area(0, 0, note_row, 11)


def add_cta_sheet(workbook: xlsxwriter.Workbook, frames: dict[str, object], formats: dict[str, Any]) -> None:
    sheet = workbook.add_worksheet(EXPECTED_SHEETS[6])
    sheet.set_tab_color("#FFC000")
    expanded_mode = bool(frames.get("expanded_signals"))
    symbols = UNIVERSE_SYMBOLS if expanded_mode else ("P", "SR")
    write_title(
        sheet,
        "06｜L4 八个合约、六条作物链CTA环境" if expanded_mode else "06｜L4 本研究P/SR双品种CTA环境",
        "CTA是按价格规则做多或做空的趋势策略；三档速度统一20%年化目标波动，信号次日执行",
        formats,
    )
    headers = ["速度", "方向回看", "波动率窗口", "用途", "确认边界"]
    rows = [
        ["Fast", "20/60交易日", "20交易日", "快速风险变化；噪声较高", "不单独触发主题配置"],
        ["Medium", "60/120交易日", "60交易日", "中期趋势确认", "需与Slow及跨品种证据共同判断"],
        ["Slow", "120/250交易日", "120交易日", "长期趋势确认", "起效慢，但更接近配置视角"],
    ]
    _write_table(sheet, 4, headers, rows, formats)
    confirmation_note = (
        "确认规则：先看每个合约Medium与Slow是否同号且非零；M+Y合并为大豆链、OI+RM合并为菜籽链，链内合约方向冲突则该链不确认，产业链内合约不重复计票。"
        "至少两条独立作物链同向、且净收益与持续性通过，才称跨品种CTA环境确认。当前新增合约为探索性连续序列，只作方向观察，不自动修改当前CTA配置。"
        if expanded_mode else
        "确认规则：同一品种Medium与Slow同号（均指执行仓位）且非零、两者近20日净收益均为正、Medium持续达到阈值，才算该品种确认；P与SR分别确认，P与SR方向不必相同。"
    )
    sheet.merge_range(9, 0, 9, 11, confirmation_note, formats["note"])
    sheet.merge_range(10, 0, 10, 11, "当前八合约趋势策略代理（探索性）" if expanded_mode else "当前P/SR趋势策略代理", formats["section"])
    signal_headers = [
        "品种", "速度", "今日目标信号", "代理净值趋势", "今日执行仓位", "20日净收益", "60日净收益",
        "60日最大回撤", "仓位持续天数", "数据截至", "当前含义", "确认角色",
    ]
    cta = frames["monitor"].get("layers", {}).get("cta", {})
    signal_rows: list[list[object]] = []
    for symbol in symbols:
        for speed in ("fast", "medium", "slow"):
            item = {} if expanded_mode else cta.get(symbol, {}).get(speed, {})
            frame, signal_basis = _latest_cta_signal(frames, symbol, speed)
            frame = frame.sort_values("date") if not frame.empty else frame
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
                f"目标={target_text}；执行={execution_text}。执行仓位来自上一交易日目标，二者不同表示信号正在切换。口径：{signal_basis}。"
            )
            wind_frame = _wind_exploratory_signal(frames, symbol, speed) if not expanded_mode else pd.DataFrame()
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
    for symbol in symbols:
        for speed in ("fast", "medium", "slow"):
            frame, _ = _latest_cta_signal(frames, symbol, speed)
            frame = frame.sort_values("date") if not frame.empty else frame
            values = pd.to_numeric(frame.get("equity"), errors="coerce").dropna().tail(60).tolist()
            if not _add_row_sparkline(sheet, 12 + row_index, 3, values, row_index, source_col, "#FFC000"):
                sheet.write(12 + row_index, 3, "少于2期", formats["center"])
            row_index += 1
    visible_last_row = 11 + len(signal_rows)
    if expanded_mode:
        summary_start = 13 + len(signal_rows)
        sheet.merge_range(summary_start, 0, summary_start, 11, "六条作物链方向汇总（仅中慢速方向一致性，不等于正式配置确认）", formats["section"])
        chain_frame = frames.get("expanded_signals", {}).get("chain_confirmations", pd.DataFrame())
        chain_rows = []
        if not chain_frame.empty:
            for _, chain in chain_frame.iterrows():
                status = str(chain.get("status", "UNCONFIRMED"))
                chain_rows.append([
                    chain.get("chain_name_cn", chain.get("chain_id", "")),
                    chain.get("symbols", ""),
                    CHAIN_STATUS_CN.get(status, status),
                    "这是方向筛选第一关；还需净收益、持续性和至少两条独立作物链共同验证",
                ])
        _write_table(
            sheet,
            summary_start + 1,
            ["作物链", "国内合约", "中慢速方向状态", "领导怎么读"],
            chain_rows,
            formats,
        )
        visible_last_row = summary_start + 1 + len(chain_rows)
    sheet.set_column(source_col, source_col + 80, None, None, {"hidden": True})
    sheet.set_column("A:B", 16)
    sheet.set_column("C:C", 22)
    sheet.set_column("D:D", 18)
    sheet.set_column("E:E", 22)
    sheet.set_column("F:J", 16)
    sheet.set_column("K:L", 34)
    sheet.freeze_panes(5, 0)
    sheet.print_area(0, 0, visible_last_row, 11)
    if expanded_mode:
        sheet.fit_to_pages(1, 2)


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
    if frames.get("china_sugar"):
        sheet.merge_range(blocker_row, 0, blocker_row, 10, "国内白糖新增数据：覆盖、版本与剩余缺口", formats["section"])
        domestic_rows = []
        labels = {"province": "省级实际产销", "casde": "全国年度供需背景", "price": "糖价/现货指标", "import": "当月进口（转载）", "import_cost": "进口成本历史"}
        for key, item in frames.get("china_sugar_manifest", {}).get("sources", {}).items():
            domestic_rows.append([labels.get(item.get("kind"), key) + " / " + key,
                                  _status_text(item.get("status")), item.get("rows", 0),
                                  item.get("data_as_of", ""), item.get("published_at", ""),
                                  item.get("reason", ""), "保留原文、发布日期及SHA256；新公告需登记来源后重跑"])
        domestic_rows.extend([
            ["全国实际产销库存未补齐", "缺失", "", "", "", "两省数据不代表全国；糖厂销售不等于终端消费；CASDE结余变化不等于期末库存", "补全国协会同口径月度原表，不拼接不同范围"],
            ["长期季节分位", "样本不足", "", "", "", "省级序列本次覆盖近月；同月长期历史不足，季节分位不计算", "补足多年同月原文，保留真实发布日期"],
        ])
        domestic_headers = ["数据集", "状态", "行数", "观察期/年度结束", "发布日期", "限制与影响", "补齐方式"]
        for offset, row in enumerate([domestic_headers, *domestic_rows]):
            output_row = blocker_row + 1 + offset
            style = formats["body"] if offset else formats["header"]
            for column, value in enumerate(row[:5]):
                sheet.write(output_row, column, value, style)
            sheet.merge_range(output_row, 5, output_row, 7, row[5], style)
            sheet.merge_range(output_row, 8, output_row, 10, row[6], style)
            sheet.set_row(output_row, 44 if offset else 30)
        blocker_row += len(domestic_rows) + 4
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
    expanded_present = bool(
        frames.get("expanded_baselines") or frames.get("expanded_futures")
        or not frames.get("current_weather_snapshot", pd.DataFrame()).empty
    )
    glossary_row = history_row + 5
    if expanded_present:
        expanded_row = history_row + 5
        sheet.merge_range(expanded_row, 0, expanded_row, 11, "扩品种数据覆盖与结论权限", formats["section"])
        baselines = frames.get("expanded_baselines", {})
        faostat = baselines.get("faostat_crop_history", pd.DataFrame())
        world_bank = baselines.get("world_bank_monthly_prices", pd.DataFrame())
        weather = frames.get("current_weather_snapshot", pd.DataFrame())
        expanded_futures = frames.get("expanded_futures", {})
        futures_frames = [frame for name, frame in expanded_futures.items() if name.endswith("continuous_exploratory") and not frame.empty]
        futures_rows = sum(len(frame) for frame in futures_frames)
        futures_dates = [pd.to_datetime(frame.get("date"), errors="coerce").max() for frame in futures_frames]
        latest_futures = max((date for date in futures_dates if pd.notna(date)), default=pd.NaT)
        formal_frames = [frame for name, frame in expanded_futures.items() if "dominant_tushare" in name and not frame.empty]
        coverage_rows = [
            ["主产区天气", "可用（代表点代理）" if not weather.empty else "缺失", len(weather),
             pd.to_datetime(weather.get("date"), errors="coerce").max() if not weather.empty else "",
             "NASA POWER官方点位；30日距平", "尚未按种植面积和生育期加权", "进入L1观察，不直接等同减产"],
            ["年度产量/单产", "可用（历史描述）" if not faostat.empty else "缺失", len(faostat),
             int(pd.to_numeric(faostat.get("year"), errors="coerce").max()) if not faostat.empty else "",
             "FAOSTAT年度修订库", "无历史发布版本，不能做无未来函数实时因子", "进入历史复盘，不确认当前供给"],
            ["国际历史价格代理", "可用（历史描述）" if not world_bank.empty else "缺失", len(world_bank),
             pd.to_datetime(world_bank.get("date"), errors="coerce").max() if not world_bank.empty else "",
             "World Bank Pink Sheet", "不是国内可交易连续合约", "只用于四次事件价格/CTA复盘"],
            ["八合约最新价格与CTA", "可用（探索性）" if futures_frames else "缺失", futures_rows,
             latest_futures if pd.notna(latest_futures) else "", "AkShare/Sina连续合约",
             "换月与复权规则未公开", "进入L3/L4方向观察，不进入正式配置确认"],
            ["新增六合约正式行情", "可用" if formal_frames else "缺失", sum(len(frame) for frame in formal_frames), "",
             "Tushare合约行情+主力映射", "当前环境/GitHub未配置TUSHARE_TOKEN，无法正式刷新" if not formal_frames else "正式口径已落盘",
             "缺失时新增合约不能进入正式CTA结论"],
            ["新增作物需求与库存", "缺失", 0, "", "USDA PSD",
             "新API需要有效API Key；官方bulk下载本次超时", "棉花、玉米、大豆和菜籽不能判断当前供需缺口"],
        ]
        _write_table(
            sheet,
            expanded_row + 1,
            ["数据模块", "状态", "行数", "数据截至", "来源/口径", "为什么没有或有什么限制", "对结论影响"],
            coverage_rows,
            formats,
        )
        glossary_row = expanded_row + 10
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
    sheet.print_area(0, 0, glossary_row + 1 + len(glossary), 10)
    sheet.fit_to_pages(1, 0 if frames.get("china_sugar") else 1)


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
    frames["workbook_as_of"] = as_of
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
