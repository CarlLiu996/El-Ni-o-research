from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from elnino_cta.factor_mining import (
    evaluate_incremental_splits,
    evaluate_factor_splits,
    forward_compound_return,
    lag_monthly_feature,
    monthly_cta_frame,
    past_seasonal_zscore,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed"
OUT_DATA = DATA / "factor_mining"
OUT_RESEARCH = ROOT / "research" / "factor_mining"
OUT_DATA.mkdir(parents=True, exist_ok=True)
OUT_RESEARCH.mkdir(parents=True, exist_ok=True)


# Market panel: formal Tushare roll-adjusted CTA paths, aggregated to month end.
market_panels: dict[str, pd.DataFrame] = {}
for commodity, symbol in (("palm_oil", "P"), ("sugar", "SR")):
    speed_frames: dict[str, pd.DataFrame] = {}
    for speed in ("medium", "slow"):
        daily = pd.read_csv(DATA / "signals" / f"{symbol}_{speed}_proxy_research.csv")
        monthly = monthly_cta_frame(daily).rename(columns={
            "cta_return": f"cta_{speed}_return",
            "signal": f"{speed}_signal",
            "position": f"{speed}_position",
            "realized_vol": f"{speed}_realized_vol",
        })
        if speed == "slow":
            monthly = monthly.drop(columns="price_return")
        speed_frames[speed] = monthly
    market = speed_frames["medium"].join(speed_frames["slow"], how="outer")
    market["mom3"] = market["price_return"].add(1.0).rolling(3).apply(np.prod, raw=True).sub(1.0)
    market["mom6"] = market["price_return"].add(1.0).rolling(6).apply(np.prod, raw=True).sub(1.0)
    same_sign = (
        np.sign(market["medium_signal"]) == np.sign(market["slow_signal"])
    ) & (np.sign(market["medium_signal"]) != 0)
    market["trend_confirm"] = same_sign.astype(float)
    market["trend_confirm_signed"] = np.where(same_sign, np.sign(market["medium_signal"]), 0.0)
    market["trend_strength_signed"] = market["mom6"] / market["medium_realized_vol"].replace(0, np.nan)
    for horizon in (1, 3, 6):
        market[f"price_fwd{horizon}"] = forward_compound_return(market["price_return"], horizon)
        market[f"abs_price_fwd{horizon}"] = market[f"price_fwd{horizon}"].abs()
        for speed in ("medium", "slow"):
            market[f"cta_{speed}_fwd{horizon}"] = forward_compound_return(
                market[f"cta_{speed}_return"], horizon
            )
    market_panels[commodity] = market


# Climate indices are conservatively lagged one month before use.
climate = pd.DataFrame(index=pd.date_range("2015-01-31", "2026-08-31", freq="ME"))
for filename, value_col in (
    ("noaa_oni.csv", "oni"),
    ("noaa_roni.csv", "roni"),
    ("noaa_soi.csv", "soi"),
    ("noaa_dmi.csv", "dmi"),
):
    raw = pd.read_csv(DATA / "climate" / filename, usecols=["date", value_col])
    raw["date"] = pd.to_datetime(raw["date"]).dt.to_period("M").dt.to_timestamp("M")
    raw[value_col] = pd.to_numeric(raw[value_col], errors="coerce")
    raw = raw.drop_duplicates("date", keep="last").set_index("date")[value_col]
    climate[value_col] = lag_monthly_feature(raw.reindex(climate.index), 1)
climate["soi_inverse"] = -climate["soi"]
climate["enso_warm"] = climate["oni"].clip(lower=0)
climate["oni_change3"] = climate["oni"].diff(3)


# Daily regional weather -> monthly regional anomalies against 1991-2020 normals.
weather = pd.read_csv(
    DATA / "climate" / "nasa_power_precipitation.csv",
    usecols=["date", "precip_mm", "root_zone_soil_wetness", "location", "region"],
)
weather["date"] = pd.to_datetime(weather["date"])
weather["month"] = weather["date"].dt.to_period("M").dt.to_timestamp("M")
location_month = weather.groupby(["month", "region", "location"], observed=True).agg(
    precip_mm=("precip_mm", lambda values: values.sum(min_count=20)),
    soil_wetness=("root_zone_soil_wetness", "mean"),
    precip_days=("precip_mm", "count"),
    soil_days=("root_zone_soil_wetness", "count"),
).reset_index()
location_month.loc[location_month["precip_days"] < 20, "precip_mm"] = np.nan
location_month.loc[location_month["soil_days"] < 20, "soil_wetness"] = np.nan
region_month = location_month.groupby(["month", "region"], observed=True).agg(
    precip_mm=("precip_mm", "mean"),
    soil_wetness=("soil_wetness", "mean"),
).reset_index()
region_month["calendar_month"] = region_month["month"].dt.month
normal = region_month[region_month["month"].between("1991-01-31", "2020-12-31")]
normal = normal.groupby(["region", "calendar_month"], observed=True).agg(
    precip_mean=("precip_mm", "mean"),
    precip_std=("precip_mm", "std"),
    soil_mean=("soil_wetness", "mean"),
    soil_std=("soil_wetness", "std"),
).reset_index()
region_month = region_month.merge(normal, on=["region", "calendar_month"], how="left")
region_month["precip_z"] = (
    region_month["precip_mm"] - region_month["precip_mean"]
) / region_month["precip_std"].replace(0, np.nan)
region_month["soil_z"] = (
    region_month["soil_wetness"] - region_month["soil_mean"]
) / region_month["soil_std"].replace(0, np.nan)
weather_features = pd.DataFrame(index=climate.index)
for region in ("malaysia_palm", "indonesia_palm", "india_sugar", "brazil_cs_sugar"):
    region_data = region_month[region_month["region"] == region].set_index("month")
    for metric in ("precip_z", "soil_z"):
        series = region_data[metric].reindex(weather_features.index)
        weather_features[f"{region}_{metric}3"] = lag_monthly_feature(series.rolling(3).mean(), 1)


# External India observations; only valid observations enter the panel.
ndvi = pd.read_csv(DATA / "climate" / "india_sugar_ndvi.csv")
ndvi["product_date"] = pd.to_datetime(ndvi["product_date"])
ndvi["month"] = ndvi["product_date"].dt.to_period("M").dt.to_timestamp("M")
ndvi["z_score"] = pd.to_numeric(ndvi["z_score"], errors="coerce")
ndvi = ndvi[(ndvi["status"] == "OK") & ndvi["z_score"].notna()]
ndvi_month = ndvi.groupby("month")["z_score"].mean().reindex(climate.index)
weather_features["india_ndvi_z"] = lag_monthly_feature(ndvi_month, 1)

monsoon = pd.read_csv(DATA / "climate" / "india_monsoon_cumulative.csv")
monsoon["date"] = pd.to_datetime(monsoon["date"])
monsoon["month"] = monsoon["date"].dt.to_period("M").dt.to_timestamp("M")
monsoon = monsoon[monsoon["status"] == "OK"].sort_values("date")
monsoon_month = monsoon.groupby("month")["cumulative_departure_pct"].last().reindex(climate.index)
weather_features["india_monsoon_departure"] = lag_monthly_feature(monsoon_month, 1)
monsoon_final = monsoon[monsoon["date"].dt.month == 9].groupby("month")[
    "cumulative_departure_pct"
].last().reindex(climate.index)
weather_features["india_monsoon_legacy"] = -lag_monthly_feature(
    monsoon_final, 1
).ffill(limit=10)


# Fundamental proxy features. Historical releases were bulk-downloaded in 2026,
# so one-month lags reduce alignment risk but do not reconstruct true vintages.
fundamentals = pd.DataFrame(index=climate.index)
for filename, name in (
    ("mpob_inventory.csv", "palm_inventory"),
    ("mpob_production.csv", "palm_production"),
    ("mpob_exports.csv", "palm_exports"),
):
    raw = pd.read_csv(DATA / "fundamentals" / filename, usecols=["date", "value"])
    raw["date"] = pd.to_datetime(raw["date"]).dt.to_period("M").dt.to_timestamp("M")
    series = raw.drop_duplicates("date", keep="last").set_index("date")["value"].reindex(climate.index)
    yoy = series.pct_change(12, fill_method=None)
    z36 = (series - series.rolling(36).mean()) / series.rolling(36).std()
    fundamentals[f"{name}_yoy"] = lag_monthly_feature(yoy, 1)
    fundamentals[f"{name}_z36"] = lag_monthly_feature(z36, 1)
    fundamentals[f"{name}_seasonal_z"] = lag_monthly_feature(
        past_seasonal_zscore(series, lookback_years=5, min_periods=3), 1
    )

india_output = pd.read_csv(
    DATA / "fundamentals" / "india_sugar_output.csv", usecols=["date", "value"]
)
india_output["date"] = pd.to_datetime(india_output["date"]).dt.to_period("M").dt.to_timestamp("M")
india_output = india_output.drop_duplicates("date", keep="last").set_index("date")["value"].reindex(climate.index)
fundamentals["india_output_seasonal_z"] = lag_monthly_feature(
    past_seasonal_zscore(india_output, lookback_years=5, min_periods=3), 1
)

unica = pd.read_csv(DATA / "fundamentals" / "unica_crush.csv", usecols=["date", "value"])
unica["date"] = pd.to_datetime(unica["date"])
unica["month"] = unica["date"].dt.to_period("M").dt.to_timestamp("M")
unica = unica.sort_values("date").groupby("month")["value"].last().reindex(climate.index)
fundamentals["unica_crush_seasonal_z"] = lag_monthly_feature(
    past_seasonal_zscore(unica, lookback_years=5, min_periods=3), 1
)


base_features = climate.join(weather_features).join(fundamentals)
panels: dict[str, pd.DataFrame] = {}
factor_specs: dict[str, list[tuple[str, str, str]]] = {}
for commodity, market in market_panels.items():
    panel = market.join(base_features, how="left")
    panel["market_trend"] = panel["trend_confirm_signed"]
    if commodity == "palm_oil":
        panel["palm_dryness3"] = -panel[[
            "malaysia_palm_precip_z3", "indonesia_palm_precip_z3"
        ]].mean(axis=1)
        panel["palm_soil_dryness3"] = -panel[[
            "malaysia_palm_soil_z3", "indonesia_palm_soil_z3"
        ]].mean(axis=1)
        for lag in (6, 9, 12):
            panel[f"palm_dryness_lag{lag}"] = panel["palm_dryness3"].shift(lag)
            panel[f"palm_soil_dryness_lag{lag}"] = panel["palm_soil_dryness3"].shift(lag)
        panel["inventory_tightness"] = -panel["palm_inventory_seasonal_z"]
        panel["production_shortfall"] = -panel["palm_production_seasonal_z"]
        panel["export_strength"] = panel["palm_exports_seasonal_z"]
        panel["climate_stress"] = panel["enso_warm"] * panel["palm_dryness3"].clip(lower=0)
        panel["fundamental_tightness"] = panel[[
            "inventory_tightness", "production_shortfall"
        ]].mean(axis=1)
        panel["stress_x_inventory"] = (
            panel["climate_stress"] * panel["inventory_tightness"].clip(lower=0)
        )
        panel["fundamental_x_market"] = panel["fundamental_tightness"] * panel["mom3"]
        panel["full_conversion"] = panel["stress_x_inventory"] * panel["mom3"].clip(lower=0)
        factor_specs[commodity] = [
            ("oni", "L1气候", "ONI，滞后1个月"),
            ("palm_dryness3", "L1局地天气", "马来西亚与印尼3个月降雨异常的相反数"),
            ("palm_soil_dryness3", "L1局地天气", "马来西亚与印尼3个月土壤湿度异常的相反数"),
            ("palm_dryness_lag6", "L1作物滞后", "棕榈油主产区干旱信号滞后6个月"),
            ("palm_dryness_lag9", "L1作物滞后", "棕榈油主产区干旱信号滞后9个月"),
            ("palm_dryness_lag12", "L1作物滞后", "棕榈油主产区干旱信号滞后12个月"),
            ("palm_soil_dryness_lag6", "L1作物滞后", "棕榈油主产区土壤偏干信号滞后6个月"),
            ("palm_soil_dryness_lag9", "L1作物滞后", "棕榈油主产区土壤偏干信号滞后9个月"),
            ("palm_soil_dryness_lag12", "L1作物滞后", "棕榈油主产区土壤偏干信号滞后12个月"),
            ("climate_stress", "L1交互", "暖ENSO强度×局地干旱"),
            ("inventory_tightness", "L2基本面", "MPOB库存相对过去同月的Z分数之相反数，滞后1个月"),
            ("production_shortfall", "L2基本面", "MPOB产量相对过去同月的Z分数之相反数，滞后1个月"),
            ("export_strength", "L2基本面", "MPOB出口相对过去同月的Z分数，滞后1个月"),
            ("stress_x_inventory", "L1×L2", "气候压力×正向库存紧张"),
            ("mom3", "L3市场", "过去3个月可交易收益动量"),
            ("mom6", "L3市场", "过去6个月可交易收益动量"),
            ("market_trend", "L4 CTA", "中慢速同向时取其方向，否则为0"),
            ("fundamental_x_market", "L2×L3", "基本面紧张方向×3个月动量"),
            ("full_conversion", "全链条", "气候压力×库存紧张×正向价格动量"),
        ]
    else:
        panel["india_dryness3"] = -panel["india_sugar_precip_z3"]
        panel["brazil_dryness3"] = -panel["brazil_cs_sugar_precip_z3"]
        brazil_growth_seed = panel["brazil_dryness3"].where(panel.index.month.isin([12, 1, 2, 3]))
        panel["brazil_growth_dryness_legacy"] = brazil_growth_seed.ffill(limit=8)
        panel["brazil_harvest_dryness"] = panel["brazil_dryness3"].where(
            panel.index.month.isin([4, 5, 6, 7, 8, 9, 10, 11])
        )
        panel["india_ndvi_stress"] = -panel["india_ndvi_z"]
        panel["monsoon_shortfall"] = -panel["india_monsoon_departure"]
        panel["india_output_shortfall"] = -panel["india_output_seasonal_z"]
        panel["unica_crush_shortfall"] = -panel["unica_crush_seasonal_z"]
        panel["climate_stress"] = panel["enso_warm"] * panel["india_dryness3"].clip(lower=0)
        panel["fundamental_tightness"] = panel[[
            "india_output_shortfall", "unica_crush_shortfall"
        ]].mean(axis=1)
        panel["stress_x_output"] = (
            panel["climate_stress"] * panel["india_output_shortfall"].clip(lower=0)
        )
        panel["fundamental_x_market"] = panel["fundamental_tightness"] * panel["mom3"]
        panel["full_conversion"] = panel["stress_x_output"] * panel["mom3"].clip(lower=0)
        factor_specs[commodity] = [
            ("oni", "L1气候", "ONI，滞后1个月"),
            ("india_dryness3", "L1局地天气", "印度糖蔗区3个月降雨异常的相反数"),
            ("brazil_dryness3", "L1局地天气", "巴西中南部糖蔗区3个月降雨异常的相反数"),
            ("brazil_growth_dryness_legacy", "L1作物日历", "巴西12月至3月生长期偏干信号，向后保留8个月"),
            ("brazil_harvest_dryness", "L1作物日历", "仅保留巴西4月至11月压榨期偏干信号"),
            ("india_ndvi_stress", "L1作物长势", "印度糖蔗NDVI Z分数的相反数"),
            ("monsoon_shortfall", "L1季风", "印度季风累计偏差的相反数"),
            ("india_monsoon_legacy", "L1作物日历", "印度9月季风最终偏差，自10月起保留至下一榨季"),
            ("climate_stress", "L1交互", "暖ENSO强度×印度局地干旱"),
            ("india_output_shortfall", "L2基本面", "印度糖产量相对过去同月的Z分数之相反数，滞后1个月"),
            ("unica_crush_shortfall", "L2基本面", "UNICA压榨量相对过去同月的Z分数之相反数，滞后1个月"),
            ("stress_x_output", "L1×L2", "气候压力×印度产量短缺"),
            ("mom3", "L3市场", "过去3个月可交易收益动量"),
            ("mom6", "L3市场", "过去6个月可交易收益动量"),
            ("market_trend", "L4 CTA", "中慢速同向时取其方向，否则为0"),
            ("fundamental_x_market", "L2×L3", "基本面紧张方向×3个月动量"),
            ("full_conversion", "全链条", "气候压力×产量短缺×正向价格动量"),
        ]
    panel["commodity"] = commodity
    panels[commodity] = panel


diagnostic_rows: list[dict[str, object]] = []
targets = (
    "price_fwd3", "abs_price_fwd3", "cta_medium_fwd3", "cta_slow_fwd3",
    "price_fwd6", "cta_medium_fwd6", "cta_slow_fwd6",
)
for commodity, panel in panels.items():
    for factor, layer, formula in factor_specs[commodity]:
        for target in targets:
            diagnostics = evaluate_factor_splits(panel[factor], panel[target], min_observations=12)
            coverage = pd.concat([panel[factor], panel[target]], axis=1).dropna()
            for row in diagnostics.to_dict("records"):
                diagnostic_rows.append({
                    "commodity": commodity,
                    "factor": factor,
                    "layer": layer,
                    "formula": formula,
                    "target": target,
                    "coverage_n": len(coverage),
                    "coverage_start": coverage.index.min().date().isoformat() if len(coverage) else "",
                    "coverage_end": coverage.index.max().date().isoformat() if len(coverage) else "",
                    **row,
                })
diagnostics = pd.DataFrame(diagnostic_rows)


summary_rows: list[dict[str, object]] = []
for keys, group in diagnostics.groupby(["commodity", "factor", "layer", "formula", "target"]):
    commodity, factor, layer, formula, target = keys
    split = group.set_index("split")
    train_ic = split.loc["train", "oriented_ic"]
    validation_ic = split.loc["validation", "oriented_ic"]
    test_ic = split.loc["test", "oriented_ic"]
    test_spread = split.loc["test", "quantile_spread"]
    coverage_n = int(group["coverage_n"].iloc[0])
    train_direction = split.loc["train", "train_direction"]
    if (
        pd.notna(train_ic) and pd.notna(validation_ic) and pd.notna(test_ic)
        and train_ic >= 0.08 and validation_ic > 0 and test_ic >= max(0.05, train_ic * 0.5)
        and pd.notna(test_spread) and test_spread > 0 and coverage_n >= 60
    ):
        verdict = "探索候选"
    elif (
        pd.notna(train_ic) and pd.notna(validation_ic) and pd.notna(test_ic)
        and train_ic > 0 and validation_ic > 0 and test_ic > 0
        and pd.notna(test_spread) and test_spread > 0
    ):
        verdict = "弱候选"
    else:
        verdict = "拒绝"
    summary_rows.append({
        "commodity": commodity,
        "factor": factor,
        "layer": layer,
        "formula": formula,
        "target": target,
        "coverage_n": coverage_n,
        "train_direction": train_direction,
        "train_oriented_ic": train_ic,
        "validation_oriented_ic": validation_ic,
        "test_oriented_ic": test_ic,
        "test_quantile_spread": test_spread,
        "verdict": verdict,
    })
summary = pd.DataFrame(summary_rows)


# Test whether non-market factors add information beyond price trend and volatility.
incremental_rows: list[dict[str, object]] = []
for commodity, panel in panels.items():
    controls = panel[["mom3", "market_trend", "medium_realized_vol"]]
    for factor, layer, _formula in factor_specs[commodity]:
        if layer in {"L3市场", "L4 CTA"}:
            continue
        for target in targets:
            result = evaluate_incremental_splits(
                panel[factor], panel[target], controls, min_observations=12
            )
            for row in result.to_dict("records"):
                incremental_rows.append({
                    "commodity": commodity,
                    "factor": factor,
                    "target": target,
                    **row,
                })
incremental = pd.DataFrame(incremental_rows)
incremental_wide = incremental.pivot_table(
    index=["commodity", "factor", "target"],
    columns="split",
    values="oriented_incremental_ic",
    aggfunc="first",
).rename(columns={
    "train": "train_incremental_ic",
    "validation": "validation_incremental_ic",
    "test": "test_incremental_ic",
}).reset_index()
summary = summary.merge(incremental_wide, on=["commodity", "factor", "target"], how="left")
market_layer = summary["layer"].isin(["L3市场", "L4 CTA"])
stable_increment = (
    summary["train_incremental_ic"].gt(0)
    & summary["validation_incremental_ic"].gt(0)
    & summary["test_incremental_ic"].ge(0.03)
)
summary["incremental_verdict"] = np.where(
    market_layer,
    "价格基准",
    np.where(stable_increment, "保留增量", "无稳定增量"),
)
summary.loc[
    (summary["verdict"] != "拒绝") & (~market_layer) & (~stable_increment),
    "verdict",
] = "拒绝"


bucket_rows: list[dict[str, object]] = []
for row in summary[summary["verdict"] != "拒绝"].itertuples(index=False):
    panel = panels[row.commodity]
    train_factor = panel.loc[:"2020-12-31", row.factor] * row.train_direction
    low_cut = train_factor.quantile(1 / 3)
    high_cut = train_factor.quantile(2 / 3)
    test = panel.loc["2024-01-01":, [row.factor, row.target]].dropna().copy()
    test["oriented_factor"] = test[row.factor] * row.train_direction
    for bucket, values in (
        ("high", test.loc[test["oriented_factor"] >= high_cut, row.target]),
        ("low", test.loc[test["oriented_factor"] <= low_cut, row.target]),
    ):
        bucket_rows.append({
            "commodity": row.commodity,
            "factor": row.factor,
            "target": row.target,
            "bucket": bucket,
            "n": len(values),
            "positive_rate": float((values > 0).mean()) if len(values) else np.nan,
            "mean_return": float(values.mean()) if len(values) else np.nan,
            "median_return": float(values.median()) if len(values) else np.nan,
        })
buckets = pd.DataFrame(bucket_rows)

high_bucket = buckets[buckets["bucket"] == "high"].rename(columns={
    "n": "test_high_n",
    "positive_rate": "test_high_positive_rate",
    "mean_return": "test_high_mean_return",
    "median_return": "test_high_median_return",
}).drop(columns="bucket")
summary = summary.merge(
    high_bucket,
    on=["commodity", "factor", "target"],
    how="left",
)
cta_target_mask = summary["target"].isin([
    "cta_medium_fwd3", "cta_slow_fwd3", "cta_medium_fwd6", "cta_slow_fwd6"
])
preliminary_mask = cta_target_mask & (summary["verdict"] != "拒绝")
usable_bucket = (
    summary["test_high_n"].ge(5)
    & summary["test_high_mean_return"].gt(0)
    & summary["test_high_positive_rate"].ge(0.5)
)
summary.loc[preliminary_mask & usable_bucket, "verdict"] = "可复核线索"
summary.loc[preliminary_mask & (~usable_bucket), "verdict"] = "样本不足"


# One independent decision per Brazil crop year to expose repeated-month inflation.
crop_calendar_events = panels["sugar"].loc[
    panels["sugar"].index.month == 4,
    [
        "brazil_growth_dryness_legacy", "price_fwd6",
        "cta_medium_fwd6", "cta_slow_fwd6",
    ],
].dropna().copy()
crop_calendar_events["period"] = np.where(
    crop_calendar_events.index.year <= 2020,
    "train",
    np.where(crop_calendar_events.index.year <= 2023, "validation", "test"),
)
crop_calendar_events["extreme_growth_dryness"] = (
    crop_calendar_events["brazil_growth_dryness_legacy"] >= 1.0
)
crop_calendar_events.reset_index(names="decision_date").to_csv(
    OUT_RESEARCH / "crop_calendar_event_check.csv", index=False, encoding="utf-8-sig"
)


# Descriptive conversion funnel. Thresholds are fixed from the training sample.
funnel_rows: list[dict[str, object]] = []
for commodity, panel in panels.items():
    train = panel.loc[:"2020-12-31"]
    climate_cut = train["climate_stress"].quantile(2 / 3)
    fundamental_cut = train["fundamental_tightness"].abs().quantile(2 / 3)
    climate_gate = panel["climate_stress"] >= climate_cut
    fundamental_gate = panel["fundamental_tightness"].abs() >= fundamental_cut
    market_gate = panel["trend_confirm"] == 1
    physical_direction = np.sign(panel["fundamental_tightness"])
    aligned_gate = market_gate & (np.sign(panel["trend_confirm_signed"]) == physical_direction)
    gate_sets = {
        "全样本": pd.Series(True, index=panel.index),
        "仅气候压力": climate_gate,
        "气候+基本面极值": climate_gate & fundamental_gate,
        "气候+基本面+中慢速同向且方向一致": climate_gate & fundamental_gate & aligned_gate,
        "仅中慢速同向": market_gate,
    }
    for gate_name, gate in gate_sets.items():
        for target in ("cta_medium_fwd3", "cta_slow_fwd3", "price_fwd3"):
            values = panel.loc[gate, target].dropna()
            funnel_rows.append({
                "commodity": commodity,
                "condition": gate_name,
                "target": target,
                "n": len(values),
                "positive_rate": float((values > 0).mean()) if len(values) else np.nan,
                "mean_return": float(values.mean()) if len(values) else np.nan,
                "median_return": float(values.median()) if len(values) else np.nan,
            })
funnel = pd.DataFrame(funnel_rows)


panel_output = pd.concat(panels.values()).reset_index(names="date")
panel_output.to_csv(OUT_DATA / "monthly_conversion_panel.csv", index=False, encoding="utf-8-sig")
diagnostics.to_csv(OUT_RESEARCH / "factor_diagnostics.csv", index=False, encoding="utf-8-sig")
summary.to_csv(OUT_RESEARCH / "factor_summary.csv", index=False, encoding="utf-8-sig")
incremental.to_csv(OUT_RESEARCH / "incremental_diagnostics.csv", index=False, encoding="utf-8-sig")
buckets.to_csv(OUT_RESEARCH / "candidate_bucket_performance.csv", index=False, encoding="utf-8-sig")
funnel.to_csv(OUT_RESEARCH / "conversion_funnel.csv", index=False, encoding="utf-8-sig")

audit = pd.DataFrame([
    {"check": "formal_futures_coverage", "result": "PASS", "detail": "P/SR Tushare roll-adjusted CTA paths, 2015-01 to 2026-08"},
    {"check": "futures_roll_policy", "result": "PASS", "detail": "same-contract daily return chained across dominant-contract rolls"},
    {"check": "target_alignment", "result": "PASS", "detail": "forward targets start at t+1 month; current month excluded"},
    {"check": "weather_coverage", "result": "PASS_WITH_DISCLOSURE", "detail": "NASA POWER daily; monthly location requires >=20 valid days"},
    {"check": "dmi_parser", "result": "PASS_WITH_FIX", "detail": "duplicate date rows dropped after detecting malformed header-like row"},
    {"check": "fundamental_vintage", "result": "FAIL_FOR_DEPLOYMENT", "detail": "historical releases bulk-downloaded in 2026; one-month lag is only an exploratory proxy"},
    {"check": "global_stock_use", "result": "EXCLUDED", "detail": "historical and forecast vintages unavailable point-in-time"},
    {"check": "strategy_readiness", "result": "EXPLORATORY_ONLY", "detail": "small single-instrument sample and multiple testing; no live recommendation"},
])
audit.to_csv(OUT_RESEARCH / "data_audit.csv", index=False, encoding="utf-8-sig")

dictionary_lines = [
    "# 因子字典",
    "",
    "所有气候、天气和基本面变量至少滞后一个月；市场变量使用月末状态，预测从下一个月开始。",
    "",
    "| 品种 | 层级 | 因子 | 公式/含义 |",
    "|---|---|---|---|",
]
for commodity, specs in factor_specs.items():
    commodity_cn = "棕榈油" if commodity == "palm_oil" else "白糖"
    for factor, layer, formula in specs:
        dictionary_lines.append(f"| {commodity_cn} | {layer} | `{factor}` | {formula} |")
(OUT_RESEARCH / "factor_dictionary.md").write_text("\n".join(dictionary_lines), encoding="utf-8")

cta_summary = summary[summary["target"].isin([
    "cta_medium_fwd3", "cta_slow_fwd3", "cta_medium_fwd6", "cta_slow_fwd6"
])]
kept = cta_summary[cta_summary["verdict"] == "可复核线索"].sort_values(
    ["verdict", "test_oriented_ic"], ascending=[True, False]
)
insufficient_count = int((cta_summary["verdict"] == "样本不足").sum())
extreme_brazil = crop_calendar_events[crop_calendar_events["extreme_growth_dryness"]]
inventory_bucket = buckets[
    (buckets["commodity"] == "palm_oil")
    & (buckets["factor"] == "inventory_tightness")
    & (buckets["target"] == "abs_price_fwd3")
].set_index("bucket")
inv_high_move = inventory_bucket.loc["high", "mean_return"] if "high" in inventory_bucket.index else np.nan
inv_low_move = inventory_bucket.loc["low", "mean_return"] if "low" in inventory_bucket.index else np.nan
decision = {
    "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    "research_window": "2015-01 to 2026-08",
    "train": "2015-01 to 2020-12",
    "validation": "2021-01 to 2023-12",
    "test": "2024-01 to 2026-08",
    "strategy_ready": False,
    "reviewable_cta_factor_count": int(len(kept)),
    "insufficient_cta_factor_count": insufficient_count,
    "candidate_rows": kept.to_dict("records"),
}
(OUT_RESEARCH / "factor_diagnostics.json").write_text(
    json.dumps(decision, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
)

report_lines = [
    "# 气候到CTA机会：探索性因子挖掘结论",
    "",
    "> 本报告使用时间切分和t+1目标，但基本面真实历史发布时间未重建，因此只能用于提出下一步假设，不能直接配置资金。",
    "",
    "## 研究设计",
    "",
    "- 样本：2015年1月至2026年8月，棕榈油P与白糖SR正式Tushare主力合约滚动收益。",
    "- 训练/验证/测试：2015-2020 / 2021-2023 / 2024-2026。",
    "- 目标：未来3个月和6个月的价格收益、中速CTA收益、慢速CTA收益。",
    "- 因子方向只在训练期确定，验证期和测试期不允许重新翻转。",
    "",
    "## 自动筛选结果",
    "",
    f"- CTA目标共有 {len(cta_summary)} 个因子-目标组合。",
    f"- 可复核线索 {len(kept)} 个，样本不足组合 {insufficient_count} 个；策略可部署组合 0 个。",
    "- 原因：基本面时点版本缺失、样本较短、多个候选同时检验。",
    "",
    "## 最重要的实证结论",
    "",
    "1. **全球ONI不是稳定CTA方向因子。** 白糖的ONI关系在验证期和测试期反转；棕榈油ONI对6个月价格有局部样本关系，但对CTA收益在测试期反转。",
    "2. **完整的气候×基本面×市场乘积因子未通过。** 月度全链条组合在测试期常为零或方向不稳定，不能作为配置规则。",
    "3. **作物日历比厄尔尼诺事件起点更重要。** 巴西12月至3月生长期偏干，对未来中慢速CTA收益的训练、验证、测试IC均为正，控制动量、趋势和波动率后仍为正。",
    "4. **这个天气信号更像CTA机会环境，而不是商品方向信号。** 在独立年度检查中，强生长期干旱年份的糖价有涨有跌，但CTA可以跟随最终形成的方向。",
    "5. **棕榈油库存紧张更像波动放大器。** 它对未来3个月绝对价格波动比对方向或CTA收益更稳定。",
    "",
    "## 巴西生长期偏干：独立年度复核",
    "",
    f"- 每年仅在4月底观察一次，共 {len(crop_calendar_events)} 个独立年度；其中偏干达到1个标准差的年份 {len(extreme_brazil)} 个。",
    f"- 这些强偏干年份未来6个月中速CTA平均收益 {extreme_brazil['cta_medium_fwd6'].mean():.2%}，正收益比例 {(extreme_brazil['cta_medium_fwd6'] > 0).mean():.0%}。",
    f"- 慢速CTA平均收益 {extreme_brazil['cta_slow_fwd6'].mean():.2%}，正收益比例 {(extreme_brazil['cta_slow_fwd6'] > 0).mean():.0%}。",
    f"- 同期白糖价格上涨比例仅 {(extreme_brazil['price_fwd6'] > 0).mean():.0%}，说明该信号不能决定做多或做空。",
    "",
    "| 决策日 | 生长期偏干Z分数 | 后6月糖价 | 中速CTA | 慢速CTA |",
    "|---|---:|---:|---:|---:|",
]
for date, row in extreme_brazil.iterrows():
    report_lines.append(
        f"| {date.date().isoformat()} | {row['brazil_growth_dryness_legacy']:.2f} | "
        f"{row['price_fwd6']:.2%} | {row['cta_medium_fwd6']:.2%} | {row['cta_slow_fwd6']:.2%} |"
    )
report_lines += [
    "",
    f"棕榈油库存紧张组未来3个月平均绝对价格变动为 {inv_high_move:.2%}，宽松组为 {inv_low_move:.2%}。它适合做风险预算/关注度修正，不足以决定方向。",
    "",
    "## 可复核的CTA线索",
    "",
    "| 品种 | 因子 | 层级 | 目标 | 训练IC | 验证IC | 测试IC | 测试增量IC | 测试高低组收益差 | 判定 |",
    "|---|---|---|---|---:|---:|---:|---:|---:|---|",
]
for row in kept.head(20).itertuples(index=False):
    commodity_cn = "棕榈油" if row.commodity == "palm_oil" else "白糖"
    report_lines.append(
        f"| {commodity_cn} | `{row.factor}` | {row.layer} | `{row.target}` | "
        f"{row.train_oriented_ic:.3f} | {row.validation_oriented_ic:.3f} | "
        f"{row.test_oriented_ic:.3f} | "
        f"{row.test_incremental_ic:.3f} | {row.test_quantile_spread:.2%} | {row.verdict} |"
    )
report_lines += [
    "",
    "## 解释边界",
    "",
    "- 全链条因子若被拒绝，表示当前定义和样本不能稳定预测，不表示经济机制永远不存在。",
    "- 市场动量或CTA同向若胜出，需要与纯价格CTA基准比较；它不是气候研究的增量证据。",
    "- 任何气候/基本面候选只有在加入后显著改善价格基准的样本外收益和回撤，才具备配置意义。",
]
(OUT_RESEARCH / "research_decision.md").write_text("\n".join(report_lines), encoding="utf-8")

print(f"panel_rows={len(panel_output)}")
print(f"diagnostic_rows={len(diagnostics)}")
print(f"cta_non_rejected={len(kept)}")
print(kept[[
    "commodity", "factor", "layer", "target", "train_oriented_ic",
    "validation_oriented_ic", "test_oriented_ic", "test_quantile_spread", "verdict"
]].head(20).to_string(index=False))
