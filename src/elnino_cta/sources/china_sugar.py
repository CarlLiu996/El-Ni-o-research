"""Domestic sugar source adapters and explicitly scoped monitoring views."""

from __future__ import annotations

import re
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup


METRICS = {
    "production": ("累计产糖", "供给", "万吨", r"产(?:混合)?糖"),
    "sales": ("累计销糖", "销售代理", "万吨", r"累计(?:销售食糖|销糖)"),
    "sales_rate": ("累计销糖率", "销售进度", "%", r"(?:产销率|销糖率)(?:为)?"),
    "inventory": ("工业库存", "库存", "万吨", r"工业库存"),
}
CASDE_METRICS = {
    "食糖产量": "production", "甘蔗糖": "cane_sugar", "甜菜糖": "beet_sugar",
    "进口": "imports", "消费": "consumption", "出口": "exports", "结余变化": "balance_change",
}


def _page(html: str | bytes) -> tuple[BeautifulSoup, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = re.sub(r"\s+", "", soup.get_text(" ", strip=True))
    match = re.search(r"20\d{2}-\d{2}-\d{2}", text)
    if not match:
        raise ValueError("页面未找到发布日期，禁止以下载日代替")
    published = pd.Timestamp(match.group()).strftime("%Y-%m-%d")
    return soup, text, published


def parse_province_report(
    html: str | bytes, *, region: str, observation_date: str, source_url: str,
) -> pd.DataFrame:
    soup, text, published = _page(html)
    title = soup.title.get_text() if soup.title else ""
    if region not in title or any(word in title for word in ("预计", "预估", "预测")):
        raise ValueError("不是指定省份的实际产销报告")
    date = pd.Timestamp(observation_date)
    period_pattern = rf"(?:截至|截止)(?:{date.year}年)?{date.month}月(?:底|末|{date.day}日)"
    if not re.search(period_pattern, text) or date > pd.Timestamp(published):
        raise ValueError("观察期与原文或发布日期不一致")
    crop = re.search(r"(20\d{2})/(?:20)?(\d{2})(?:年)?榨季", text)
    if not crop:
        raise ValueError("未确认榨季")
    crop_year = f"{crop[1]}/{crop[2]}"
    rows = []
    for metric, (label, category, unit, pattern) in METRICS.items():
        # Inspect the last occurrence with a numeric value: headings can repeat
        # the current value but lack the adjoining year-on-year comparison.
        matches = [m for m in re.finditer(pattern + r"([\d.]+)" + re.escape(unit), text)
                   if text[max(0, m.start() - 2):m.start()] not in {"同期", "单月"}]
        if not matches:
            raise ValueError(f"{region} {metric} 缺少可解析数值")
        match = matches[-1]
        value = float(match[1])
        context = text[match.end():match.end() + 80]
        prior_match = re.match(r"[（(](?:上榨季|去年)(?:同期)(?:产糖|销糖|销糖率|工业库存)([\d.]+)", context)
        change_match = re.match(r"[，,；;]?同比(增加|减少|降低|下降|提高|上升)([\d.]+)(万吨|个百分点)", context)
        prior = float(prior_match[1]) if prior_match else float("nan")
        prior_basis = "原文上年同期值" if prior_match else "未取得"
        if change_match:
            sign = -1 if change_match[1] in {"减少", "降低", "下降"} else 1
            prior = value - sign * float(change_match[2])
            prior_basis = "当前值减原文同比增减量"
        if value < 0 or (metric == "sales_rate" and value > 100) or (pd.notna(prior) and prior < 0):
            raise ValueError("产销指标范围无效")
        rows.append({
            "region": region, "metric": metric, "label": label, "category": category,
            "date": date.strftime("%Y-%m-%d"), "crop_year": crop_year,
            "published_at": published, "value": value, "unit": unit,
            "prior_value": prior, "prior_basis": prior_basis,
            "change": value - prior,
            "yoy": value / prior - 1 if prior > 0 and metric != "sales_rate" else float("nan"),
            "value_type": "actual", "source": "沐甜科技公开省级产销资料",
            "source_url": source_url,
        })
    result = pd.DataFrame(rows)
    values = result.set_index("metric")["value"]
    if abs(values.production - values.sales - values.inventory) > 0.05:
        raise ValueError("产量减销量与工业库存不一致，需人工核验；未覆盖原值")
    if abs(values.sales / values.production * 100 - values.sales_rate) > 0.05:
        raise ValueError("销糖率与产销量不能勾稽")
    return result


def parse_casde(html: str | bytes, *, source_url: str) -> pd.DataFrame:
    soup, _, published = _page(html)
    tables = pd.read_html(StringIO(str(soup)))
    selected = [table for table in tables if {"食糖产量", "甘蔗糖", "甜菜糖"} <= set(table.iloc[:, 0].astype(str).str.strip())]
    if len(selected) != 1:
        raise ValueError("未唯一定位中国食糖供需表")
    table = selected[0]
    if "万吨" not in table.to_string():
        raise ValueError("未确认食糖供需表单位为万吨")
    rows = []
    for col in range(1, len(table.columns)):
        vintage = re.sub(r"\s+", "", str(table.iloc[0, col]))
        crop = re.search(r"(20\d{2})/(\d{2})", vintage)
        if not crop:
            continue
        kind = "forecast" if "预测" in vintage else "estimate" if "估计" in vintage else "historical_estimate"
        for label, metric in CASDE_METRICS.items():
            values = table.loc[table.iloc[:, 0].astype(str).str.strip().eq(label), table.columns[col]]
            if len(values) != 1:
                raise ValueError(f"{label} 缺失或重复")
            value = float(str(values.iloc[0]).replace(" ", ""))
            if metric != "balance_change" and value < 0:
                raise ValueError("供需数值无效")
            rows.append({
                "metric": metric, "label": label, "crop_year": f"{crop[1]}/{crop[2]}",
                "date": f"{int(crop[1]) + 1}-09-30", "published_at": published,
                "vintage_label": vintage, "value": value, "unit": "万吨", "value_type": kind,
                "source": "农业农村部CASDE", "source_url": source_url,
            })
    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("供需表没有有效年度")
    for _, group in result.groupby("vintage_label"):
        v = group.set_index("metric")["value"]
        if abs(v.production + v.imports - v.consumption - v.exports - v.balance_change) > 2:
            raise ValueError("年度供需平衡表不能勾稽")
    return result


def parse_price_json(raw: dict) -> pd.DataFrame:
    names = {"综合价格": "composite_price", "原糖价格": "raw_sugar_price", "现货价格": "spot_price"}
    dates = raw["category"]
    data = raw["data"]
    if any(name not in data or len(data[name]) != len(dates) for name in names):
        raise ValueError("价格字段缺失或日期与价格长度不同")
    result = pd.DataFrame({"date": pd.to_datetime(dates, errors="raise")})
    for name, column in names.items():
        result[column] = pd.to_numeric(data[name], errors="coerce")
    if result.date.duplicated().any():
        raise ValueError("价格日期重复，需核验")
    return result.sort_values("date").reset_index(drop=True)


def parse_import_report(html: str | bytes, *, observation_date: str, source_url: str) -> pd.DataFrame:
    _, text, published = _page(html)
    date = pd.Timestamp(observation_date)
    match = re.search(rf"{date.year}年{date.month}月份我国进口食糖([\d.]+)万吨", text)
    if not match or date > pd.Timestamp(published):
        raise ValueError("没有指定月份的实际进口报道")
    return pd.DataFrame([{
        "date": date.strftime("%Y-%m-%d"), "published_at": published,
        "metric": "monthly_imports", "value": float(match[1]), "unit": "万吨",
        "value_type": "actual_reported", "status": "REVIEW_REQUIRED",
        "source": "沐甜科技转引海关（月度近似数）", "source_url": source_url,
        "limitation": "未取得海关原始明细；转载累计数存在差异，仅列当月近似值，不评分",
    }])


def current_province_rows(history: pd.DataFrame, as_of: str) -> pd.DataFrame:
    if history.empty:
        return history.copy()
    cutoff = pd.Timestamp(as_of)
    visible = history.loc[
        pd.to_datetime(history.published_at).le(cutoff)
        & pd.to_datetime(history.date).le(cutoff)
    ].sort_values(["date", "published_at"])
    current = visible.groupby(["region", "metric"], sort=False).tail(1).copy()
    for index, item in current.iterrows():
        stale = (cutoff - pd.Timestamp(item.published_at)).days > 45
        current.loc[index, "status"] = "STALE" if stale else "PARTIAL"
        prior = item.prior_value
        if stale:
            direction = "数据过期：不判断当前方向"
        elif pd.isna(prior):
            direction = "缺同比基准：仅展示当前值"
        elif item.metric == "production":
            direction = "国内产量同比增加" if item.value > prior else "国内产量同比减少" if item.value < prior else "国内产量同比持平"
        elif item.metric == "inventory":
            direction = "库存高于去年同期" if item.value > prior else "库存低于去年同期" if item.value < prior else "库存同比持平"
        elif item.metric == "sales_rate":
            direction = "销售进度同比放缓" if item.value < prior else "销售进度同比加快" if item.value > prior else "销售进度同比持平"
        else:
            direction = "糖厂销量同比增加" if item.value > prior else "糖厂销量同比减少" if item.value < prior else "糖厂销量同比持平"
        current.loc[index, "direction"] = direction
        current.loc[index, "limitation"] = {
            "production": "省级实际产糖，不代表全国产量；增产不证明气候没有影响",
            "sales": "糖厂销售给贸易商或用糖企业，不等于末端消费",
            "sales_rate": "销量÷产量；增产也会拉低该比例，不能单独判断需求减少",
            "inventory": "仅糖厂工业库存，不含全国商业库存、进口加工糖及储备",
        }[item.metric] + "；同月长期历史不足，不计算季节分位"
        current.loc[index, "direction_reason"] = (
            f"当前{item.value:g}，上年同期{prior:g}；差值{item.change:+.2f}"
            + ("个百分点" if item.metric == "sales_rate" else "万吨")
            if pd.notna(prior) else "原文没有同口径上年同期值，不外推"
        )
    return current.reset_index(drop=True)
