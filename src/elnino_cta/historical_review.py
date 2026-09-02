from __future__ import annotations

import math
from typing import Any

import pandas as pd


EVENT_COLUMNS = [
    "event_id",
    "start_date",
    "peak_date",
    "end_date",
    "peak_oni",
    "duration_seasons",
    "status",
    "source",
]
EVIDENCE_COLUMNS = [
    "event_id",
    "status",
    "commodity",
    "layer",
    "metric_id",
    "value",
    "unit",
    "coverage_pct",
    "observation_date",
    "source",
    "source_flag",
    "limitation",
]
CHAIN_IDS = ("palm_oil", "sugar", "cotton", "corn", "soybean", "rapeseed")
CROP_CHAIN_MAP = {
    "Oil palm fruit": "palm_oil",
    "Sugar cane": "sugar",
    "Seed cotton, unginned": "cotton",
    "Maize (corn)": "corn",
    "Soya beans": "soybean",
    "Rape or colza seed": "rapeseed",
}
REGION_CHAIN_MAP = {
    "malaysia_palm": "palm_oil",
    "indonesia_palm": "palm_oil",
    "india_sugar": "sugar",
    "brazil_cs_sugar": "sugar",
    "us_cotton": "cotton",
    "india_cotton": "cotton",
    "china_cotton": "cotton",
    "us_corn": "corn",
    "brazil_corn": "corn",
    "argentina_corn": "corn",
    "china_corn": "corn",
    "us_soybean": "soybean",
    "brazil_soybean": "soybean",
    "argentina_soybean": "soybean",
    "canada_rapeseed": "rapeseed",
    "australia_rapeseed": "rapeseed",
    "china_rapeseed": "rapeseed",
}

CHAIN_NAMES_CN = {
    "palm_oil": "棕榈油", "sugar": "白糖", "cotton": "棉花",
    "corn": "玉米", "soybean": "大豆产业链", "rapeseed": "菜籽产业链",
}


def build_current_weather_monitor(
    weather: pd.DataFrame,
    as_of: str | pd.Timestamp,
    climatology_start: int = 1991,
    climatology_end: int = 2020,
    window_days: int = 30,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a representative-point 30-day weather monitor by crop chain.

    Monthly normals keep the calculation stable across leap years.  The output is
    an observation proxy, not a crop-mask or phenology-specific yield model.
    """
    required = {"date", "region", "precip_mm", "root_zone_soil_wetness"}
    missing = required - set(weather.columns)
    if missing:
        raise ValueError(f"Missing current-weather columns: {sorted(missing)}")
    daily = weather.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["precip_mm"] = pd.to_numeric(daily["precip_mm"], errors="coerce")
    daily["root_zone_soil_wetness"] = pd.to_numeric(
        daily["root_zone_soil_wetness"], errors="coerce"
    )
    daily = daily.dropna(subset=["date"])
    daily = daily[daily["region"].isin(REGION_CHAIN_MAP)].copy()
    daily["month"] = daily["date"].dt.month
    climatology = daily[daily["date"].dt.year.between(climatology_start, climatology_end)]
    normals = climatology.groupby(["region", "month"], as_index=False).agg(
        precip_normal=("precip_mm", "mean"),
        soil_normal=("root_zone_soil_wetness", "mean"),
    )
    joined = daily.merge(normals, on=["region", "month"], how="left")
    joined["chain_id"] = joined["region"].map(REGION_CHAIN_MAP)
    joined["soil_delta"] = joined["root_zone_soil_wetness"] - joined["soil_normal"]
    joined["valid"] = joined["precip_mm"].notna().astype(float)
    chain_day = joined.groupby(["chain_id", "date"], as_index=False).agg(
        actual_precip=("precip_mm", "sum"),
        normal_precip=("precip_normal", "sum"),
        soil_wetness_anomaly=("soil_delta", "mean"),
        coverage_pct=("valid", "mean"),
    )
    paths: list[pd.DataFrame] = []
    for chain_id, group in chain_day.groupby("chain_id"):
        group = group.sort_values("date").copy()
        group["segment"] = group["date"].diff().dt.days.ne(1).cumsum()
        group["actual_30d"] = group.groupby("segment")["actual_precip"].transform(
            lambda values: values.rolling(window_days, min_periods=1).sum()
        )
        group["normal_30d"] = group.groupby("segment")["normal_precip"].transform(
            lambda values: values.rolling(window_days, min_periods=1).sum()
        )
        group["precip_anomaly_pct"] = group["actual_30d"] / group["normal_30d"] - 1
        group["soil_wetness_anomaly"] = group.groupby("segment")["soil_wetness_anomaly"].transform(
            lambda values: values.rolling(window_days, min_periods=1).mean()
        )
        group["coverage_pct"] = group.groupby("segment")["coverage_pct"].transform(
            lambda values: values.rolling(window_days, min_periods=1).mean()
        )
        paths.append(group)
    path = pd.concat(paths, ignore_index=True) if paths else pd.DataFrame()
    if path.empty:
        return pd.DataFrame(), path
    cutoff = pd.Timestamp(as_of)
    latest = path[path["date"] <= cutoff].sort_values("date").groupby(
        "chain_id", as_index=False
    ).tail(1).copy()
    latest["chain_name_cn"] = latest["chain_id"].map(CHAIN_NAMES_CN)
    latest["state"] = latest["precip_anomaly_pct"].map(
        lambda value: "DRY" if value <= -0.10 else "WET" if value >= 0.10 else "NEAR_NORMAL"
    )
    latest["source"] = "NASA POWER representative points"
    latest["limitation"] = "30日代表点代理；尚未按作物生育期或种植区权重聚合"
    columns = [
        "chain_id", "chain_name_cn", "date", "precip_anomaly_pct",
        "soil_wetness_anomaly", "coverage_pct", "state", "source", "limitation",
    ]
    return latest[columns].sort_values("chain_id").reset_index(drop=True), path


def identify_enso_events(
    oni: pd.DataFrame,
    threshold: float = 0.5,
    min_seasons: int = 5,
) -> pd.DataFrame:
    required = {"date", "oni", "source"}
    missing = required.difference(oni.columns)
    if missing:
        raise ValueError(f"Missing ONI event columns: {sorted(missing)}")
    ordered = oni.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
    ordered["oni"] = pd.to_numeric(ordered["oni"], errors="coerce")
    ordered = ordered.dropna(subset=["date", "oni"]).sort_values("date").drop_duplicates("date")
    runs: list[pd.DataFrame] = []
    current_indices: list[int] = []
    previous_date: pd.Timestamp | None = None
    for index, row in ordered.iterrows():
        qualifies = float(row["oni"]) >= threshold
        date = pd.Timestamp(row["date"])
        consecutive = previous_date is not None and date.to_period("M") == previous_date.to_period("M") + 1
        if qualifies:
            if current_indices and not consecutive:
                runs.append(ordered.loc[current_indices])
                current_indices = []
            current_indices.append(index)
        elif current_indices:
            runs.append(ordered.loc[current_indices])
            current_indices = []
        previous_date = date
    if current_indices:
        runs.append(ordered.loc[current_indices])

    records: list[dict[str, object]] = []
    used_ids: dict[str, int] = {}
    last_date = ordered["date"].max() if not ordered.empty else pd.NaT
    for group in runs:
        if len(group) < min_seasons:
            continue
        group = group.sort_values("date")
        start = group.iloc[0]
        end = group.iloc[-1]
        peak = group.loc[group["oni"].idxmax()]
        base_id = f"{pd.Timestamp(start['date']).year}-{pd.Timestamp(end['date']).year}"
        used_ids[base_id] = used_ids.get(base_id, 0) + 1
        event_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
        records.append({
            "event_id": event_id,
            "start_date": start["date"],
            "peak_date": peak["date"],
            "end_date": end["date"],
            "peak_oni": float(peak["oni"]),
            "duration_seasons": len(group),
            "status": "ONGOING" if pd.Timestamp(end["date"]) == last_date else "COMPLETED",
            "source": start["source"],
        })
    return pd.DataFrame(records, columns=EVENT_COLUMNS)


def build_index_evidence(
    events: pd.DataFrame,
    oni: pd.DataFrame,
    soi: pd.DataFrame,
    dmi: pd.DataFrame,
) -> pd.DataFrame:
    series = {"soi_mean_t0_t12": (soi, "soi"), "dmi_mean_t0_t12": (dmi, "dmi")}
    oni_source = oni.get("source", pd.Series(dtype=str)).dropna()
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.start_date)
        end = start + pd.DateOffset(months=12)
        rows.extend([
            {
                "event_id": event.event_id,
                "status": event.status,
                "commodity": "both",
                "metric_id": "oni_peak",
                "value": event.peak_oni,
                "unit": "degC",
                "coverage_pct": 1.0,
                "observation_date": pd.Timestamp(event.peak_date),
                "source": oni_source.iloc[0] if not oni_source.empty else event.source,
                "limitation": "事后完整事件峰值；仅描述，不作为T0实时信号",
            },
            {
                "event_id": event.event_id,
                "status": event.status,
                "commodity": "both",
                "metric_id": "oni_duration_seasons",
                "value": event.duration_seasons,
                "unit": "overlapping_seasons",
                "coverage_pct": 1.0,
                "observation_date": pd.Timestamp(event.end_date),
                "source": oni_source.iloc[0] if not oni_source.empty else event.source,
                "limitation": "事后完整事件持续期；ONGOING事件不进入规律统计",
            },
        ])
        for metric_id, (frame, value_column) in series.items():
            current = frame.copy()
            current["date"] = pd.to_datetime(current["date"], errors="coerce")
            current[value_column] = pd.to_numeric(current[value_column], errors="coerce")
            window = current[current["date"].between(start, end)].sort_values("date")
            expected_months = len(pd.period_range(start, end, freq="M"))
            sources = window.get("source", pd.Series(dtype=str)).dropna()
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "commodity": "both",
                "metric_id": metric_id,
                "value": window[value_column].mean(),
                "unit": "index",
                "coverage_pct": min(window[value_column].notna().sum() / expected_months, 1.0),
                "observation_date": window["date"].max() if not window.empty else pd.NaT,
                "source": sources.iloc[0] if not sources.empty else pd.NA,
                "limitation": "固定T0至T+12月均值；不读取窗口后数据",
            })
    return pd.DataFrame(rows)


def build_weather_evidence(
    events: pd.DataFrame,
    weather: pd.DataFrame,
    climatology_start: int = 1991,
    climatology_end: int = 2020,
) -> pd.DataFrame:
    daily = weather.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    for column in ("precip_mm", "root_zone_soil_wetness"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    climatology = daily[daily["date"].dt.year.between(climatology_start, climatology_end)].copy()
    climatology["month"] = climatology["date"].dt.month
    normals = climatology.groupby(["region", "month"]).agg(
        precip_normal=("precip_mm", "mean"),
        soil_normal=("root_zone_soil_wetness", "mean"),
    ).reset_index()
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.start_date)
        end = start + pd.DateOffset(months=12)
        window = daily[daily["date"].between(start, end)].copy()
        window["month"] = window["date"].dt.month
        joined = window.merge(normals, on=["region", "month"], how="left")
        for region, group in joined.groupby("region"):
            if region not in REGION_CHAIN_MAP:
                continue
            actual_rain = group["precip_mm"].sum(min_count=1)
            normal_rain = group["precip_normal"].sum(min_count=1)
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "commodity": REGION_CHAIN_MAP[region],
                "region": region,
                "precip_anomaly_pct": actual_rain / normal_rain - 1 if normal_rain > 0 else pd.NA,
                "soil_wetness_anomaly": (group["root_zone_soil_wetness"] - group["soil_normal"]).mean(),
                "coverage_pct": group["precip_mm"].notna().mean(),
                "observation_date": group["date"].max(),
                "source": group.get("source", pd.Series([pd.NA])).iloc[0],
            })
    return pd.DataFrame(rows)


def build_yield_evidence(events: pd.DataFrame, crops: pd.DataFrame) -> pd.DataFrame:
    crop_frame = crops.copy()
    crop_frame["year"] = pd.to_numeric(crop_frame["year"], errors="coerce")
    crop_frame["value"] = pd.to_numeric(crop_frame["value"], errors="coerce")
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start_year = pd.Timestamp(event.start_date).year
        for year_offset in (0, 1):
            year = start_year + year_offset
            for keys, group in crop_frame.groupby(["area", "item", "element"]):
                if keys[1] not in CROP_CHAIN_MAP:
                    continue
                values = group.set_index("year")["value"].sort_index()
                if year not in values.index:
                    continue
                prior = values.loc[(values.index >= year - 5) & (values.index <= year - 1)]
                previous = values.get(year - 1, pd.NA)
                value = float(values.loc[year])
                source_row = group.loc[group["year"].eq(year)].iloc[0]
                prior_median = prior.median()
                rows.append({
                    "event_id": event.event_id,
                    "status": event.status,
                    "commodity": CROP_CHAIN_MAP[keys[1]],
                    "year_offset": year_offset,
                    "year": year,
                    "area": keys[0],
                    "item": keys[1],
                    "element": keys[2],
                    "value": value,
                    "yoy": value / previous - 1 if pd.notna(previous) and previous != 0 else pd.NA,
                    "deviation_vs_prior5_median": (
                        value / prior_median - 1 if len(prior) == 5 and prior_median != 0 else pd.NA
                    ),
                    "baseline_years": len(prior),
                    "baseline_end_year": year - 1,
                    "observation_date": pd.Timestamp(year=year, month=12, day=31),
                    "calendar_crop_year_caveat": True,
                    "source": source_row.get("source", pd.NA),
                    "source_flag": source_row.get("flag", pd.NA),
                })
    return pd.DataFrame(rows)


def build_external_climate_evidence(
    events: pd.DataFrame,
    monsoon: pd.DataFrame,
    ndvi: pd.DataFrame,
) -> pd.DataFrame:
    monsoon_frame = monsoon.copy()
    if "date" not in monsoon_frame:
        monsoon_frame["date"] = pd.Series(dtype="datetime64[ns]")
    monsoon_frame["date"] = pd.to_datetime(monsoon_frame["date"], errors="coerce")
    ndvi_frame = ndvi.copy()
    if "product_date" not in ndvi_frame:
        ndvi_frame["product_date"] = pd.Series(dtype="datetime64[ns]")
    ndvi_frame["product_date"] = pd.to_datetime(ndvi_frame["product_date"], errors="coerce")
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.start_date)
        end = start + pd.DateOffset(months=12)
        monsoon_status = monsoon_frame.get("status", pd.Series(index=monsoon_frame.index, dtype=str))
        monsoon_window = monsoon_frame[
            monsoon_frame["date"].between(start, end) & monsoon_status.eq("OK")
        ]
        for season_year, group in monsoon_window.groupby("season_year"):
            latest = group.sort_values("date").iloc[-1]
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "commodity": "sugar",
                "metric_id": f"india_monsoon_departure_y{int(season_year)}",
                "value": latest["cumulative_departure_pct"],
                "unit": "ratio",
                "coverage_pct": float(latest["valid_locations"]) / 3,
                "observation_date": latest["date"],
                "source": latest.get("source", pd.NA),
                "limitation": f"NASA POWER三点等权代理；季风年{int(season_year)}",
            })
        ndvi_status = ndvi_frame.get("status", pd.Series(index=ndvi_frame.index, dtype=str))
        ndvi_window = ndvi_frame[
            ndvi_frame["product_date"].between(start, end) & ndvi_status.eq("OK")
        ]
        if not ndvi_window.empty:
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "commodity": "sugar",
                "metric_id": "india_sugar_ndvi_zscore",
                "value": pd.to_numeric(ndvi_window["z_score"], errors="coerce").mean(),
                "unit": "z-score",
                "coverage_pct": pd.to_numeric(ndvi_window["valid_pixel_ratio"], errors="coerce").mean(),
                "observation_date": ndvi_window["product_date"].max(),
                "source": ndvi_window.get("source", pd.Series([pd.NA])).iloc[0],
                "limitation": "T0至T+12月平均；主产区代表点邻域植被代理，非糖蔗专属",
            })
    return pd.DataFrame(rows, columns=[
        "event_id", "status", "commodity", "metric_id", "value", "unit",
        "coverage_pct", "observation_date", "source", "limitation",
    ])


def build_event_evidence_panel(
    events: pd.DataFrame,
    index_evidence: pd.DataFrame,
    weather_evidence: pd.DataFrame,
    yield_evidence: pd.DataFrame,
    external_climate_evidence: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in index_evidence.itertuples(index=False):
        for commodity in CHAIN_IDS:
            rows.append({
                "event_id": item.event_id, "status": item.status, "commodity": commodity,
                "layer": "L1", "metric_id": item.metric_id, "value": item.value,
                "unit": item.unit, "coverage_pct": item.coverage_pct,
                "observation_date": item.observation_date, "source": item.source,
                "source_flag": pd.NA, "limitation": item.limitation,
            })
    for item in weather_evidence.itertuples(index=False):
        for field, unit in (("precip_anomaly_pct", "ratio"), ("soil_wetness_anomaly", "index")):
            rows.append({
                "event_id": item.event_id, "status": item.status, "commodity": item.commodity,
                "layer": "L1", "metric_id": f"weather:{item.region}:{field}",
                "value": getattr(item, field), "unit": unit,
                "coverage_pct": item.coverage_pct, "observation_date": item.observation_date,
                "source": item.source, "source_flag": pd.NA,
                "limitation": "T0至T+12月；按1991-2020同月均值计算",
            })
    for item in yield_evidence.itertuples(index=False):
        for field in ("yoy", "deviation_vs_prior5_median"):
            rows.append({
                "event_id": item.event_id, "status": item.status, "commodity": item.commodity,
                "layer": "L2",
                "metric_id": f"crop:{item.area}:{item.item}:{item.element}:y{item.year_offset}:{field}",
                "value": getattr(item, field), "unit": "ratio",
                "coverage_pct": 1.0 if item.baseline_years == 5 else item.baseline_years / 5,
                "observation_date": item.observation_date, "source": item.source,
                "source_flag": item.source_flag, "limitation": "FAOSTAT日历年口径；不等同作物年度",
            })
    for item in external_climate_evidence.itertuples(index=False):
        rows.append({
            "event_id": item.event_id, "status": item.status, "commodity": item.commodity,
            "layer": "L1", "metric_id": item.metric_id, "value": item.value,
            "unit": item.unit, "coverage_pct": item.coverage_pct,
            "observation_date": item.observation_date, "source": item.source,
            "source_flag": pd.NA, "limitation": item.limitation,
        })
    result = pd.DataFrame(rows, columns=EVIDENCE_COLUMNS)
    keys = ["event_id", "commodity", "metric_id"]
    if result.duplicated(keys).any():
        raise ValueError("Duplicate historical evidence keys")
    return result


def build_event_price_paths(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    before_months: int = 6,
    after_months: int = 18,
) -> pd.DataFrame:
    ordered = prices.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce") + pd.offsets.MonthEnd(0)
    ordered["price"] = pd.to_numeric(ordered["price"], errors="coerce")
    ordered = ordered.dropna(subset=["date", "price"])
    records: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        t0 = pd.Timestamp(event.start_date) + pd.offsets.MonthEnd(0)
        for commodity, group in ordered.groupby("commodity"):
            indexed = group.sort_values("date").drop_duplicates("date").set_index("date")
            if t0 not in indexed.index:
                continue
            base = float(indexed.loc[t0, "price"])
            if base <= 0:
                continue
            for relative_month in range(-before_months, after_months + 1):
                date = t0 + pd.DateOffset(months=relative_month) + pd.offsets.MonthEnd(0)
                if date not in indexed.index:
                    continue
                price = float(indexed.loc[date, "price"])
                records.append({
                    "event_id": event.event_id,
                    "status": event.status,
                    "commodity": commodity,
                    "date": date,
                    "relative_month": relative_month,
                    "price": price,
                    "price_index": price / base * 100.0,
                    "source": indexed.loc[date].get("source", pd.NA),
                })
    return pd.DataFrame(records, columns=[
        "event_id", "status", "commodity", "date", "relative_month",
        "price", "price_index", "source",
    ])


MONTHLY_LOOKBACKS = {
    "medium": ((3, 6), 3),
    "slow": ((6, 12), 6),
}


def _max_drawdown_from_equity(equity: pd.Series) -> float:
    curve = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)], ignore_index=True)
    return float((curve / curve.cummax() - 1.0).min())


def build_monthly_cta_proxy(
    prices: pd.DataFrame,
    speed: str,
    annual_vol_target: float = 0.20,
    max_leverage: float = 2.0,
    cost_bps_per_turnover: float = 2.0,
) -> pd.DataFrame:
    if speed not in MONTHLY_LOOKBACKS:
        raise ValueError(f"speed must be one of {sorted(MONTHLY_LOOKBACKS)}")
    windows, vol_window = MONTHLY_LOOKBACKS[speed]
    result = prices[["date", "price"]].sort_values("date").drop_duplicates("date").copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce") + pd.offsets.MonthEnd(0)
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    if result["price"].dropna().le(0).any():
        raise ValueError("Monthly CTA prices must be positive")
    result["return"] = result["price"].pct_change(fill_method=None)
    directions = [
        result["price"].pct_change(window, fill_method=None).apply(
            lambda value: 1.0 if value > 0 else -1.0 if value < 0 else 0.0
        )
        for window in windows
    ]
    result["signal"] = sum(directions) / len(directions)
    result["realized_vol"] = result["return"].rolling(vol_window).std() * math.sqrt(12)
    scaled = result["signal"] * annual_vol_target / result["realized_vol"]
    result["target_position"] = scaled.clip(-max_leverage, max_leverage).fillna(0.0)
    result["position"] = result["target_position"].shift(1).fillna(0.0)
    result["turnover"] = result["position"].diff().abs().fillna(result["position"].abs())
    result["gross_return"] = result["position"] * result["return"].fillna(0.0)
    result["cost"] = result["turnover"] * cost_bps_per_turnover / 10_000
    result["net_return"] = result["gross_return"] - result["cost"]
    result["equity"] = (1.0 + result["net_return"]).cumprod()
    result["speed"] = speed
    return result.reset_index(drop=True)


def build_event_cta_paths(
    events: pd.DataFrame,
    cta: pd.DataFrame,
    before_months: int = 6,
    after_months: int = 18,
) -> pd.DataFrame:
    ordered = cta.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce") + pd.offsets.MonthEnd(0)
    records: list[pd.DataFrame] = []
    for event in events.itertuples(index=False):
        t0 = pd.Timestamp(event.start_date) + pd.offsets.MonthEnd(0)
        for _, series in ordered.groupby(["commodity", "speed"]):
            start = t0 - pd.DateOffset(months=before_months) + pd.offsets.MonthEnd(0)
            end = t0 + pd.DateOffset(months=after_months) + pd.offsets.MonthEnd(0)
            window = series[series["date"].between(start, end)].sort_values("date").copy()
            if window.empty:
                continue
            window["event_id"] = event.event_id
            window["status"] = event.status
            window["relative_month"] = (
                (window["date"].dt.year - t0.year) * 12 + window["date"].dt.month - t0.month
            )
            window["event_equity"] = (1.0 + window["net_return"]).cumprod()
            records.append(window)
    result = pd.concat(records, ignore_index=True) if records else pd.DataFrame()
    keys = ["event_id", "commodity", "speed", "date"]
    if not result.empty and result.duplicated(keys).any():
        raise ValueError("Duplicate event CTA path keys")
    return result


def summarize_event_cta(paths: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if paths.empty:
        return pd.DataFrame()
    for keys, group in paths.groupby(["event_id", "commodity", "speed"]):
        for horizon in (3, 6, 12, 18):
            window = group[group["relative_month"].between(0, horizon)].sort_values("date")
            if window.empty:
                continue
            equity = (1.0 + window["net_return"]).cumprod()
            rows.append({
                "event_id": keys[0],
                "status": window["status"].iloc[0],
                "commodity": keys[1],
                "speed": keys[2],
                "horizon_months": horizon,
                "net_return": equity.iloc[-1] - 1.0,
                "max_drawdown": _max_drawdown_from_equity(equity),
                "positive_month_share": window["net_return"].gt(0).mean(),
                "average_abs_position": window["position"].abs().mean(),
                "turnover": window["turnover"].sum(),
            })
    return pd.DataFrame(rows)


def make_pattern_input(
    evidence: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Combine completed-event evidence, pricing, and CTA returns without advice fields."""
    required = ["event_id", "status", "commodity", "metric_id", "value"]
    rows = [evidence.loc[evidence["status"].eq("COMPLETED"), required].copy()]
    completed_prices = price_paths[price_paths["status"].eq("COMPLETED")]
    for (event_id, commodity), group in completed_prices.groupby(["event_id", "commodity"]):
        indexed = group.drop_duplicates("relative_month").set_index("relative_month")["price_index"]
        for horizon in (3, 6, 12, 18):
            if 0 in indexed.index and horizon in indexed.index:
                rows.append(pd.DataFrame([{
                    "event_id": event_id,
                    "status": "COMPLETED",
                    "commodity": commodity,
                    "metric_id": f"price_return_{horizon}m",
                    "value": float(indexed.loc[horizon] / indexed.loc[0] - 1.0),
                }]))
    completed_cta = cta_summary[cta_summary["status"].eq("COMPLETED")].copy()
    if not completed_cta.empty:
        completed_cta["metric_id"] = (
            "cta_" + completed_cta["speed"].astype(str) + "_"
            + completed_cta["horizon_months"].astype(str) + "m_net_return"
        )
        completed_cta = completed_cta.rename(columns={"net_return": "value"})
        rows.append(completed_cta[required])
    result = pd.concat(rows, ignore_index=True)
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    return result.dropna(subset=["value"]).drop_duplicates(
        ["event_id", "commodity", "metric_id"], keep="last"
    )


def summarize_patterns(event_results: pd.DataFrame) -> pd.DataFrame:
    """Describe completed-event regularities; deliberately emits no allocation advice."""
    rows: list[dict[str, object]] = []
    for keys, group in event_results.groupby(["commodity", "metric_id"]):
        values = pd.to_numeric(group["value"], errors="coerce").dropna()
        count = len(values)
        positive_share = values.gt(0).mean() if count else pd.NA
        same_direction_share = max(positive_share, 1 - positive_share) if count else pd.NA
        strength = (
            "STABLE_HISTORY"
            if count >= 4 and same_direction_share >= 0.70
            else "LIMITED_SAMPLE"
            if count == 3
            else "INSUFFICIENT_EVIDENCE"
        )
        rows.append({
            "commodity": keys[0],
            "metric_id": keys[1],
            "sample_size": count,
            "positive_count": int(values.gt(0).sum()),
            "negative_count": int(values.lt(0).sum()),
            "flat_count": int(values.eq(0).sum()),
            "same_direction_share": same_direction_share,
            "median": values.median() if count else pd.NA,
            "q25": values.quantile(0.25) if count else pd.NA,
            "q75": values.quantile(0.75) if count else pd.NA,
            "pattern_strength": strength,
        })
    return pd.DataFrame(rows)


def summarize_existing_daily_cta(
    events: pd.DataFrame,
    signal_frames: dict[tuple[str, str], pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.start_date)
        end = start + pd.DateOffset(months=18)
        for (symbol, speed), frame in signal_frames.items():
            current = frame.copy()
            current["date"] = pd.to_datetime(current["date"], errors="coerce")
            window = current[current["date"].between(start, end)].sort_values("date").copy()
            if window.empty:
                rows.append({
                    "event_id": event.event_id,
                    "status": event.status,
                    "symbol": symbol,
                    "speed": speed,
                    "coverage_status": "UNAVAILABLE",
                    "net_return": pd.NA,
                    "max_drawdown": pd.NA,
                    "start_date": pd.NaT,
                    "end_date": pd.NaT,
                })
                continue
            net_returns = pd.to_numeric(window["net_return"], errors="coerce").fillna(0.0)
            equity = (1.0 + net_returns).cumprod()
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "symbol": symbol,
                "speed": speed,
                "coverage_status": "AVAILABLE",
                "net_return": equity.iloc[-1] - 1.0,
                "max_drawdown": _max_drawdown_from_equity(equity),
                "start_date": window["date"].iloc[0],
                "end_date": window["date"].iloc[-1],
            })
    return pd.DataFrame(rows)
