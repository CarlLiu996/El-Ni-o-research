from __future__ import annotations

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
    region_commodity = {
        "malaysia_palm": "palm_oil",
        "indonesia_palm": "palm_oil",
        "india_sugar": "sugar",
        "brazil_cs_sugar": "sugar",
    }
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
            if region not in region_commodity:
                continue
            actual_rain = group["precip_mm"].sum(min_count=1)
            normal_rain = group["precip_normal"].sum(min_count=1)
            rows.append({
                "event_id": event.event_id,
                "status": event.status,
                "commodity": region_commodity[region],
                "region": region,
                "precip_anomaly_pct": actual_rain / normal_rain - 1 if normal_rain > 0 else pd.NA,
                "soil_wetness_anomaly": (group["root_zone_soil_wetness"] - group["soil_normal"]).mean(),
                "coverage_pct": group["precip_mm"].notna().mean(),
                "observation_date": group["date"].max(),
                "source": group.get("source", pd.Series([pd.NA])).iloc[0],
            })
    return pd.DataFrame(rows)


def build_yield_evidence(events: pd.DataFrame, crops: pd.DataFrame) -> pd.DataFrame:
    item_commodity = {"Oil palm fruit": "palm_oil", "Sugar cane": "sugar"}
    crop_frame = crops.copy()
    crop_frame["year"] = pd.to_numeric(crop_frame["year"], errors="coerce")
    crop_frame["value"] = pd.to_numeric(crop_frame["value"], errors="coerce")
    rows: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        start_year = pd.Timestamp(event.start_date).year
        for year_offset in (0, 1):
            year = start_year + year_offset
            for keys, group in crop_frame.groupby(["area", "item", "element"]):
                if keys[1] not in item_commodity:
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
                    "commodity": item_commodity[keys[1]],
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
                "limitation": "T0至T+12月平均；50公里缓冲区植被代理，非糖蔗专属",
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
        for commodity in ("palm_oil", "sugar"):
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
