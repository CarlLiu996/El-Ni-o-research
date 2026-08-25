from __future__ import annotations

import pandas as pd


METRIC_META = {
    "mpob_production": ("马来西亚棕榈油月度供给", "高位偏松，低位偏紧", "单一国家月度口径"),
    "ffb_yield": ("单位面积鲜果串产出，解释供给效率", "高位偏松，低位偏紧", "MPOB历史回填，非点时数据"),
    "global_palm_stock_use": ("全球期末库存相对消费的缓冲", "高位偏松，低位偏紧", "USDA年度估计/预测"),
    "mpob_exports": ("马来西亚棕榈油外需代理", "高位偏紧，低位偏松", "未覆盖印尼出口"),
    "mpob_inventory": ("马来西亚月末现货缓冲", "高位偏松，低位偏紧", "不等同全球库存"),
    "india_sugar_output": ("印度食糖供给兑现", "高位偏松，低位偏紧", "月度流量，需结合榨季累计"),
    "unica_crush": ("巴西中南部甘蔗压榨进度", "高位通常偏松", "甘蔗可分流至乙醇"),
    "unica_sugar_mix": ("巴西中南部甘蔗用于制糖的比例", "高位通常偏松", "本身不是产糖量"),
    "global_sugar_total_use_source": ("全球食糖消费需求", "高位偏紧，低位偏松", "USDA年度低频口径"),
    "global_sugar_ending_stocks_source": ("全球食糖期末库存缓冲", "高位偏松，低位偏紧", "USDA年度低频口径"),
    "global_sugar_stock_use": ("全球食糖期末库存相对消费", "高位偏松，低位偏紧", "综合结果，不替代库存与消费"),
    "global_palm_total_use": ("全球棕榈油消费需求", "高位偏紧，低位偏松", "USDA年度估计/预测"),
    "global_palm_ending_stocks": ("全球棕榈油期末库存缓冲", "高位偏松，低位偏紧", "USDA年度估计/预测"),
    "brazil_cane_for_sugar_proxy": ("压榨量 × 制糖比；非食糖产量", "高位通常偏松", "缺少ATR/出糖率，非食糖产量"),
}


def percentile_label(value: float | None) -> str:
    if pd.isna(value):
        return "不可比较"
    if float(value) <= 1 / 3:
        return "低位"
    if float(value) >= 2 / 3:
        return "高位"
    return "中位"


def _classify_value_type(row: pd.Series) -> str:
    explicit = str(row.get("value_type", "")).strip().lower()
    if explicit in {"actual", "forecast"}:
        return explicit
    note = str(row.get("note", "")).lower()
    if any(word in note for word in ("forecast", "projection", "estimate")):
        return "forecast"
    observed = pd.to_datetime(row.get("date"), errors="coerce")
    published = pd.to_datetime(row.get("published_at"), errors="coerce")
    return "forecast" if pd.notna(observed) and pd.notna(published) and observed > published else "actual"


def _detail_row(metric_id: str, ordered: pd.DataFrame) -> dict[str, object]:
    latest = ordered.iloc[-1]
    latest_date = pd.Timestamp(latest["date"])
    seasonal = pd.to_numeric(
        ordered.loc[ordered["date"].dt.month.eq(latest_date.month), "value"], errors="coerce"
    ).dropna()
    value = float(latest["value"])
    return {
        "metric_id": metric_id,
        "label": latest.get("indicator_name", metric_id),
        "date": latest_date,
        "value": value,
        "unit": latest.get("unit", pd.NA),
        "source": latest.get("source", pd.NA),
        "published_at": pd.to_datetime(latest.get("published_at"), errors="coerce"),
        "value_type": _classify_value_type(latest),
        "seasonal_percentile": seasonal.le(value).mean() if not seasonal.empty else pd.NA,
    }


def build_l2_details(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    details: list[dict[str, object]] = []
    for dataset, frame in frames.items():
        ordered = frame.copy()
        ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
        ordered["value"] = pd.to_numeric(ordered["value"], errors="coerce")
        ordered = ordered.dropna(subset=["date", "value"]).sort_values("date")
        if not ordered.empty:
            details.append(_detail_row(dataset, ordered))

    palm_balance = frames["global_palm_stock_use"].copy()
    palm_balance["date"] = pd.to_datetime(palm_balance["date"], errors="coerce")
    palm_balance = palm_balance.sort_values("date")
    palm_latest = palm_balance.iloc[-1]
    for metric_id, column, label in (
        ("global_palm_total_use", "total_use_1000t", "全球棕榈油消费量"),
        ("global_palm_ending_stocks", "ending_stocks_1000t", "全球棕榈油期末库存"),
    ):
        seasonal = pd.to_numeric(
            palm_balance.loc[
                palm_balance["date"].dt.month.eq(pd.Timestamp(palm_latest["date"]).month), column
            ], errors="coerce"
        ).dropna()
        value = float(palm_latest[column])
        details.append({
            "metric_id": metric_id,
            "label": label,
            "date": palm_latest["date"],
            "value": value,
            "unit": "1000 tonnes",
            "source": palm_latest.get("source", pd.NA),
            "published_at": pd.to_datetime(palm_latest.get("published_at"), errors="coerce"),
            "value_type": _classify_value_type(palm_latest),
            "seasonal_percentile": seasonal.le(value).mean() if not seasonal.empty else pd.NA,
        })

    crush = frames["unica_crush"].copy()
    mix = frames["unica_sugar_mix"].copy()
    for frame in (crush, mix):
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame.sort_values("date", inplace=True)
    aligned = pd.merge_asof(crush, mix, on="date", direction="backward", suffixes=("_crush", "_mix"))
    aligned["proxy_value"] = aligned["value_crush"] * aligned["value_mix"] / 100.0
    proxy_history = aligned.dropna(subset=["proxy_value"])
    if not proxy_history.empty:
        latest = proxy_history.iloc[-1]
        proxy_season = proxy_history.loc[
            proxy_history["date"].dt.month.eq(pd.Timestamp(latest["date"]).month), "proxy_value"
        ]
        proxy_value = float(latest["proxy_value"])
        details.append({
            "metric_id": "brazil_cane_for_sugar_proxy",
            "label": "巴西中南部制糖甘蔗量代理",
            "date": latest["date"],
            "value": proxy_value,
            "unit": "tonnes cane allocated to sugar",
            "source": "UNICA crush × sugar mix",
            "published_at": max(
                pd.Timestamp(latest["published_at_crush"]),
                pd.Timestamp(latest["published_at_mix"]),
            ),
            "value_type": (
                "forecast"
                if _classify_value_type(latest.rename(lambda x: x.removesuffix("_crush"))) == "forecast"
                or _classify_value_type(latest.rename(lambda x: x.removesuffix("_mix"))) == "forecast"
                else "actual"
            ),
            "seasonal_percentile": proxy_season.le(proxy_value).mean(),
        })

    result = pd.DataFrame(details)
    metadata = result["metric_id"].map(METRIC_META)
    if metadata.isna().any():
        missing = result.loc[metadata.isna(), "metric_id"].tolist()
        raise ValueError(f"Missing metric metadata: {missing}")
    result[["meaning", "direction_rule", "limitation"]] = metadata.apply(pd.Series)
    result["current_position"] = result["seasonal_percentile"].map(percentile_label)
    result["position_meaning"] = result.apply(
        lambda row: (
            f"{row['current_position']}；{row['direction_rule']}"
            if row["current_position"] != "不可比较"
            else f"不可比较；{row['limitation']}"
        ),
        axis=1,
    )
    return result


def _direction(category: str, percentile: float) -> str:
    if category == "demand":
        return "TIGHT" if percentile >= 2 / 3 else "LOOSE" if percentile <= 1 / 3 else "NEUTRAL"
    return "TIGHT" if percentile <= 1 / 3 else "LOOSE" if percentile >= 2 / 3 else "NEUTRAL"


def build_l2_summary(
    frames: dict[str, pd.DataFrame],
    as_of: str | pd.Timestamp,
) -> pd.DataFrame:
    details = build_l2_details(frames).set_index("metric_id")
    definitions = [
        ("palm_oil", "supply", "mpob_production", ["mpob_production"], ["ffb_yield"]),
        ("palm_oil", "demand", "mpob_exports", ["mpob_exports"], ["global_palm_total_use"]),
        ("palm_oil", "inventory", "mpob_inventory", ["mpob_inventory"], ["global_palm_ending_stocks", "global_palm_stock_use"]),
        ("sugar", "supply", "india_sugar_output", ["india_sugar_output", "brazil_cane_for_sugar_proxy"], ["brazil_cane_for_sugar_proxy"]),
        ("sugar", "demand", "global_sugar_total_use_source", ["global_sugar_total_use_source"], []),
        ("sugar", "inventory", "global_sugar_ending_stocks_source", ["global_sugar_ending_stocks_source"], ["global_sugar_stock_use"]),
    ]
    rows: list[dict[str, object]] = []
    for commodity, category, primary, scoring_metrics, drivers in definitions:
        item = details.loc[primary]
        scoring = details.loc[scoring_metrics]
        if isinstance(scoring, pd.Series):
            scoring = scoring.to_frame().T
        if scoring["value_type"].ne("actual").any() or scoring["seasonal_percentile"].isna().any():
            direction = "INSUFFICIENT"
        else:
            components = [_direction(category, float(value)) for value in scoring["seasonal_percentile"]]
            direction = components[0] if len(set(components)) == 1 else "NEUTRAL"
        rows.append({
            "commodity": commodity,
            "category": category,
            "primary_metric": primary,
            "primary_value": item["value"],
            "unit": item["unit"],
            "date": item["date"],
            "published_at": item["published_at"],
            "drivers": ",".join(drivers),
            "scoring_metrics": ",".join(scoring_metrics),
            "value_type": item["value_type"],
            "driver_value_types": ",".join(details.loc[driver, "value_type"] for driver in drivers),
            "seasonal_percentile": item["seasonal_percentile"],
            "direction": direction,
            "as_of": pd.Timestamp(as_of),
        })
    return pd.DataFrame(rows)
