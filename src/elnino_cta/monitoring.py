from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .quality import audit_timeseries


def _read_csv(path: Path, date_columns: tuple[str, ...] = ("date",)) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required monitoring input is missing: {path}")
    frame = pd.read_csv(path)
    for column in date_columns:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return frame


def _finite(value: Any) -> float | None:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return None
    return float(value)


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _compound(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return _finite((1.0 + clean).prod() - 1.0) if not clean.empty else None


def _drawdown(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return _finite((clean / clean.cummax() - 1.0).min())


def _same_sign_days(series: pd.Series) -> int:
    clean = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if clean.empty:
        return 0
    latest_sign = int(math.copysign(1, clean.iloc[-1])) if clean.iloc[-1] else 0
    count = 0
    for value in reversed(clean.tolist()):
        sign = int(math.copysign(1, value)) if value else 0
        if sign != latest_sign:
            break
        count += 1
    return count


def _staleness(last_date: Any, as_of: pd.Timestamp) -> int | None:
    if last_date is None or pd.isna(last_date):
        return None
    return int((as_of.normalize() - pd.Timestamp(last_date).normalize()).days)


def _cta_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = frame.sort_values("date").drop_duplicates("date")
    latest = ordered.iloc[-1]
    return {
        "date": _iso(latest["date"]),
        "target_position": _finite(latest.get("target_position")),
        "position": _finite(latest.get("position")),
        "realized_vol": _finite(latest.get("realized_vol")),
        "equity": _finite(latest.get("equity")),
        "net_return_5d": _compound(ordered["net_return"].tail(5)),
        "net_return_20d": _compound(ordered["net_return"].tail(20)),
        "net_return_60d": _compound(ordered["net_return"].tail(60)),
        "max_drawdown_60d": _drawdown(ordered["equity"].tail(60)),
        "position_persistence_days": _same_sign_days(ordered["position"]),
        "turnover_20d": _finite(pd.to_numeric(ordered["turnover"], errors="coerce").tail(20).sum()),
    }


def _market_metrics(frame: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, Any]:
    ordered = frame.sort_values("date").drop_duplicates("date")
    close = pd.to_numeric(ordered["close"], errors="coerce")
    latest = ordered.iloc[-1]
    metrics: dict[str, Any] = {
        "date": _iso(latest["date"]),
        "staleness_days": _staleness(latest["date"], as_of),
        "contract": latest.get("contract"),
        "close": _finite(latest["close"]),
        "provider": latest.get("provider"),
        "indicator_code": latest.get("indicator_code"),
        "indicator_name": latest.get("indicator_name"),
        "source": latest.get("source"),
        "realized_vol_20d": _finite(close.pct_change(fill_method=None).tail(20).std() * math.sqrt(252)),
        "max_price_drawdown_60d": _drawdown(close.tail(60)),
        "quality": audit_timeseries(ordered),
    }
    for window in (5, 20, 60, 120, 250):
        metrics[f"price_return_{window}d"] = (
            _finite(close.iloc[-1] / close.iloc[-window - 1] - 1.0)
            if len(close) > window and close.iloc[-window - 1] > 0
            else None
        )
    return metrics


def _delivery_key(contract: str) -> int:
    digits = "".join(character for character in str(contract).split(".")[0] if character.isdigit())
    if len(digits) < 3:
        return 999999
    yymm = digits[-4:] if len(digits) >= 4 else digits[-3:]
    if len(yymm) == 3:
        year_digit, month = int(yymm[0]), int(yymm[1:])
        current_decade = (date.today().year // 10) * 10
        year = current_decade + year_digit
    else:
        year, month = 2000 + int(yymm[:2]), int(yymm[2:])
    return year * 100 + month


def _term_structure(frame: pd.DataFrame) -> dict[str, Any]:
    ordered = frame.dropna(subset=["date", "contract", "close"]).copy()
    latest_date = ordered["date"].max()
    latest = ordered[ordered["date"] == latest_date].copy()
    latest["delivery"] = latest["contract"].map(_delivery_key)
    latest = latest.sort_values(["delivery", "contract"])
    liquid = latest[pd.to_numeric(latest["open_interest"], errors="coerce").fillna(0) > 0]
    if len(liquid) < 2:
        liquid = latest
    if len(liquid) < 2:
        return {"date": _iso(latest_date), "status": "INSUFFICIENT", "contracts": int(len(liquid))}
    front, second = liquid.iloc[0], liquid.iloc[1]
    spread_pct = float(front["close"] / second["close"] - 1.0)
    return {
        "date": _iso(latest_date),
        "status": "OK",
        "front_contract": front["contract"],
        "second_contract": second["contract"],
        "front_close": _finite(front["close"]),
        "second_close": _finite(second["close"]),
        "front_second_spread_pct": _finite(spread_pct),
        "curve": "backwardation" if spread_pct > 0 else "contango" if spread_pct < 0 else "flat",
    }


def _oni_metrics(frame: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, Any]:
    ordered = frame.sort_values("date").drop_duplicates("date")
    latest = ordered.iloc[-1]
    oni = float(latest["oni"])
    delta_3m = oni - float(ordered.iloc[-4]["oni"]) if len(ordered) >= 4 else None
    phase = "El Nino" if oni >= 0.5 else "La Nina" if oni <= -0.5 else "Neutral"
    return {
        "date": _iso(latest["date"]),
        "staleness_days": _staleness(latest["date"], as_of),
        "oni": oni,
        "change_3m": _finite(delta_3m),
        "phase": phase,
        "strengthening": bool(delta_3m is not None and delta_3m > 0),
    }


def _monthly_index_metrics(
    frame: pd.DataFrame, column: str, as_of: pd.Timestamp, positive_label: str, negative_label: str
) -> dict[str, Any]:
    ordered = frame.sort_values("date").drop_duplicates("date")
    latest = ordered.iloc[-1]
    value = float(latest[column])
    change_3m = value - float(ordered.iloc[-4][column]) if len(ordered) >= 4 else None
    state = positive_label if value >= 0.5 else negative_label if value <= -0.5 else "Neutral"
    return {
        "date": _iso(latest["date"]),
        "staleness_days": _staleness(latest["date"], as_of),
        column: value,
        "change_3m": _finite(change_3m),
        "state": state,
    }


def _rainfall_metrics(frame: pd.DataFrame, as_of: pd.Timestamp, dry_threshold: float) -> dict[str, Any]:
    point_counts = frame.groupby(["date", "region"], as_index=False)["precip_mm"].count()
    value_columns = ["precip_mm"]
    if "root_zone_soil_wetness" in frame:
        value_columns.append("root_zone_soil_wetness")
    daily = frame.groupby(["date", "region"], as_index=False)[value_columns].mean()
    latest_date = daily["date"].max()
    latest_counts = point_counts[point_counts["date"] == latest_date]
    results: dict[str, Any] = {
        "date": _iso(latest_date),
        "staleness_days": _staleness(latest_date, as_of),
        "regions": {},
        "minimum_latest_points": int(latest_counts["precip_mm"].min()),
        "missing_point_observations": int(frame["precip_mm"].isna().sum()),
    }
    for region, group in daily.groupby("region"):
        group = group.sort_values("date").copy()
        recent = group[group["date"] > latest_date - pd.Timedelta(days=30)]
        prior = group[group["date"] <= latest_date - pd.Timedelta(days=365)]
        month_day = latest_date.strftime("%m-%d")
        baselines: list[float] = []
        soil_baselines: list[float] = []
        for year in sorted(prior["date"].dt.year.unique()):
            end = pd.Timestamp(f"{year}-{month_day}")
            window = prior[(prior["date"] > end - pd.Timedelta(days=30)) & (prior["date"] <= end)]
            if len(window) >= 20:
                baselines.append(float(window["precip_mm"].sum()))
                if "root_zone_soil_wetness" in window:
                    soil_baselines.append(float(window["root_zone_soil_wetness"].mean()))
        recent_total = float(recent["precip_mm"].sum())
        baseline = float(pd.Series(baselines).median()) if baselines else None
        anomaly = recent_total / baseline - 1.0 if baseline and baseline > 0 else None
        recent_soil = (
            float(recent["root_zone_soil_wetness"].mean())
            if "root_zone_soil_wetness" in recent and recent["root_zone_soil_wetness"].notna().any()
            else None
        )
        baseline_soil = float(pd.Series(soil_baselines).median()) if soil_baselines else None
        soil_anomaly = (
            recent_soil / baseline_soil - 1.0
            if recent_soil is not None and baseline_soil and baseline_soil > 0
            else None
        )
        dry_run = 0
        for value in reversed(recent["precip_mm"].tolist()):
            if value >= dry_threshold:
                break
            dry_run += 1
        results["regions"][region] = {
            "rain_30d_mm": recent_total,
            "historical_median_30d_mm": _finite(baseline),
            "anomaly_pct": _finite(anomaly),
            "dry_days_30d": int((recent["precip_mm"] < dry_threshold).sum()),
            "consecutive_dry_days": dry_run,
            "root_zone_soil_wetness": _finite(recent_soil),
            "historical_median_root_zone_soil_wetness": _finite(baseline_soil),
            "root_zone_soil_wetness_anomaly_pct": _finite(soil_anomaly),
        }
    return results


def _fundamental_status(data_dir: Path, config: dict[str, Any], as_of: pd.Timestamp) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    default_max_stale = int(config["freshness_days"]["fundamentals"])
    for item in config["required_fundamentals"]:
        path = data_dir / "fundamentals" / f"{item['dataset']}.csv"
        result = dict(item)
        result["path"] = str(path.as_posix())
        if not path.exists():
            result.update({"status": "MISSING", "last_date": None, "staleness_days": None})
        else:
            frame = _read_csv(path, date_columns=("date", "published_at"))
            last_date = frame["date"].max() if "date" in frame else None
            last_published = frame["published_at"].max() if "published_at" in frame else None
            freshness_date = last_published if last_published is not None and not pd.isna(last_published) else last_date
            stale = _staleness(freshness_date, as_of)
            max_stale = int(item.get("max_stale_days", default_max_stale))
            result.update({
                "status": "STALE" if stale is None or stale > max_stale else "OK",
                "last_date": _iso(last_date),
                "last_published_at": _iso(last_published),
                "freshness_basis": "published_at" if last_published is not None and not pd.isna(last_published) else "date",
                "staleness_days": stale,
                "max_stale_days": max_stale,
                "rows": int(len(frame)),
            })
        items.append(result)
    ok = sum(item["status"] == "OK" for item in items)
    return {"status": "OK" if ok == len(items) else "INCOMPLETE", "available": ok, "required": len(items), "items": items}


def build_external_observation_status(
    data_dir: Path,
    as_of: str | date | pd.Timestamp,
    freshness_days: dict[str, int] | None = None,
) -> dict[str, Any]:
    climate_dir = data_dir / "climate"
    manifest_path = climate_dir / "external_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"overall": "PARTIAL", "refreshed_at": None, "datasets": {}}
    )
    thresholds = {
        "monsoon_cumulative": 2,
        "imd_events": 30,
        "ndvi": 24,
        **(freshness_days or {}),
    }
    cutoff = pd.Timestamp(as_of)

    def read_optional(filename: str, date_columns: tuple[str, ...]) -> pd.DataFrame:
        path = climate_dir / filename
        return _read_csv(path, date_columns=date_columns) if path.exists() else pd.DataFrame()

    def freshness(dataset: str, data_date: Any, status: str) -> tuple[int | None, str]:
        days = _staleness(data_date, cutoff)
        if status != "OK":
            return days, status
        return days, "STALE" if days is not None and days > thresholds[dataset] else "FRESH"

    monsoon = read_optional(
        "india_monsoon_cumulative.csv", ("date", "data_as_of", "published_at", "downloaded_at")
    )
    if monsoon.empty:
        monsoon_status: dict[str, Any] = {"status": "MISSING", "value": None}
    else:
        latest = monsoon.sort_values(["season_year", "season_day"]).iloc[-1]
        status = str(latest.get("status", "MISSING"))
        data_date = latest.get("date")
        stale_days, freshness_status = freshness("monsoon_cumulative", data_date, status)
        monsoon_status = {
            "status": status,
            "freshness_status": freshness_status,
            "date": _iso(data_date),
            "data_as_of": _iso(latest.get("data_as_of")),
            "published_at": _iso(latest.get("published_at")),
            "downloaded_at": str(latest.get("downloaded_at")) if pd.notna(latest.get("downloaded_at")) else None,
            "staleness_days": stale_days,
            "value": _finite(latest.get("cumulative_departure_pct")),
            "current_cumulative_mm": _finite(latest.get("current_cumulative_mm")),
            "climatology_cumulative_mm": _finite(latest.get("climatology_cumulative_mm")),
            "historical_percentile": _finite(latest.get("historical_percentile")),
            "valid_locations": int(latest.get("valid_locations", 0)),
            "required_locations": int(latest.get("required_locations", 2)),
            "source": latest.get("source"),
            "limitation": "NASA POWER三个代表点等权代理；不单独触发CTA配置动作",
        }

    imd = read_optional(
        "india_monsoon_events.csv", ("official_date", "normal_date", "announcement_date")
    )
    if imd.empty:
        imd_status: dict[str, Any] = {"status": "MISSING", "value": None, "events": []}
    else:
        events = []
        for row in imd.sort_values("event_type").itertuples(index=False):
            events.append({
                "event_type": getattr(row, "event_type", None),
                "official_date": _iso(getattr(row, "official_date", None)),
                "normal_date": _iso(getattr(row, "normal_date", None)),
                "departure_days": _finite(getattr(row, "departure_days", None)),
                "status": getattr(row, "status", None),
                "source": getattr(row, "source_url", None),
            })
        announced = imd[imd["official_date"].notna()] if "official_date" in imd else pd.DataFrame()
        data_date = announced["official_date"].max() if not announced.empty else pd.NaT
        manifest_status = manifest.get("datasets", {}).get("imd_events", {}).get("status")
        status = str(manifest_status or ("OK" if events else "MISSING"))
        stale_days, freshness_status = freshness("imd_events", data_date, status)
        imd_status = {
            "status": status,
            "freshness_status": freshness_status,
            "date": _iso(data_date),
            "published_at": _iso(imd["announcement_date"].max()) if "announcement_date" in imd else None,
            "downloaded_at": manifest.get("refreshed_at"),
            "staleness_days": stale_days,
            "value": events,
            "events": events,
            "source": "; ".join(sorted(imd.get("source_url", pd.Series(dtype=str)).dropna().astype(str).unique())),
            "limitation": "仅采信IMD正式宣布；预测文字不作为事件，不单独触发CTA配置动作",
        }

    ndvi = read_optional(
        "india_sugar_ndvi.csv", ("product_date", "published_at", "downloaded_at")
    )
    if ndvi.empty:
        ndvi_status: dict[str, Any] = {"status": "MISSING", "value": None}
    else:
        latest = ndvi.sort_values("product_date", na_position="first").iloc[-1]
        status = str(latest.get("status", "MISSING"))
        data_date = latest.get("product_date")
        stale_days, freshness_status = freshness("ndvi", data_date, status)
        ndvi_status = {
            "status": status,
            "freshness_status": freshness_status,
            "date": _iso(data_date),
            "published_at": _iso(latest.get("published_at")),
            "downloaded_at": str(latest.get("downloaded_at")) if pd.notna(latest.get("downloaded_at")) else None,
            "staleness_days": stale_days,
            "value": _finite(latest.get("ndvi")),
            "same_period_difference": _finite(latest.get("same_period_difference")),
            "z_score": _finite(latest.get("z_score")),
            "historical_percentile": _finite(latest.get("historical_percentile")),
            "valid_pixel_ratio": _finite(latest.get("valid_pixel_ratio")),
            "region_count": int(latest.get("region_count", 0)) if pd.notna(latest.get("region_count")) else 0,
            "source": latest.get("source"),
            "limitation": "主产区代表点邻域植被代理，非糖蔗专属；不单独触发CTA配置动作",
        }
    return {
        "monsoon_cumulative": monsoon_status,
        "imd_events": imd_status,
        "ndvi": ndvi_status,
    }


def assess_cta_confirmation(
    cta: dict[str, dict[str, dict[str, Any]]],
    trend_confirmation_days: int,
) -> dict[str, Any]:
    """Confirm each symbol independently; cross-symbol signs may differ."""
    details: dict[str, dict[str, Any]] = {}
    for symbol, speeds in cta.items():
        medium = speeds["medium"]
        slow = speeds["slow"]
        medium_position = _finite(medium.get("position"))
        slow_position = _finite(slow.get("position"))
        same_sign = (
            medium_position is not None
            and slow_position is not None
            and medium_position != 0
            and slow_position != 0
            and (medium_position > 0) == (slow_position > 0)
        )
        medium_profitable = (
            medium.get("net_return_20d") is not None
            and medium["net_return_20d"] > 0
        )
        slow_profitable = (
            slow.get("net_return_20d") is not None
            and slow["net_return_20d"] > 0
        )
        persistent = int(medium.get("position_persistence_days") or 0) >= int(
            trend_confirmation_days
        )
        confirmed = same_sign and medium_profitable and slow_profitable and persistent
        details[symbol] = {
            "confirmed": confirmed,
            "same_sign": same_sign,
            "direction": (
                "LONG" if same_sign and medium_position > 0
                else "SHORT" if same_sign and medium_position < 0
                else "MIXED_OR_FLAT"
            ),
            "medium_profitable_20d": medium_profitable,
            "slow_profitable_20d": slow_profitable,
            "medium_persistent": persistent,
        }
    confirmed_count = sum(item["confirmed"] for item in details.values())
    return {
        "status": "CONFIRMED" if details and confirmed_count == len(details) else "NOT_CONFIRMED",
        "symbols_confirmed": int(confirmed_count),
        "symbols_required": len(details),
        "details": details,
        "cross_symbol_same_sign_required": False,
    }


def build_snapshot(
    data_dir: Path,
    config_path: Path,
    as_of: str | date | pd.Timestamp | None = None,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    as_of_ts = pd.Timestamp(as_of or date.today())
    oni = _read_csv(data_dir / "climate" / "noaa_oni.csv")
    roni = _read_csv(data_dir / "climate" / "noaa_roni.csv")
    soi = _read_csv(data_dir / "climate" / "noaa_soi.csv")
    dmi = _read_csv(data_dir / "climate" / "noaa_dmi.csv")
    rain = _read_csv(data_dir / "climate" / "nasa_power_precipitation.csv")
    climate = {
        "oni": _oni_metrics(oni, as_of_ts),
        "roni": _monthly_index_metrics(roni, "roni", as_of_ts, "El Nino", "La Nina"),
        "soi": _monthly_index_metrics(soi, "soi", as_of_ts, "La Nina coupling", "El Nino coupling"),
        "dmi": _monthly_index_metrics(dmi, "dmi", as_of_ts, "Positive IOD", "Negative IOD"),
        "rainfall": _rainfall_metrics(rain, as_of_ts, float(config["dry_day_threshold_mm"])),
    }

    markets: dict[str, Any] = {}
    cta: dict[str, Any] = {}
    for symbol in ("P", "SR"):
        dominant = _read_csv(data_dir / "futures" / f"{symbol}_dominant_tushare.csv")
        wind_path = data_dir / "futures" / f"{symbol}_active_wind.csv"
        price_history = _read_csv(wind_path) if wind_path.exists() else dominant
        contracts = _read_csv(data_dir / "futures" / f"{symbol}_contracts_tushare.csv")
        markets[symbol] = _market_metrics(price_history, as_of_ts)
        markets[symbol]["term_structure"] = _term_structure(contracts)
        cta[symbol] = {}
        for speed in ("fast", "medium", "slow"):
            signal = _read_csv(data_dir / "signals" / f"{symbol}_{speed}_proxy_research.csv")
            cta[symbol][speed] = _cta_metrics(signal)

    fundamentals = _fundamental_status(data_dir, config, as_of_ts)
    alerts: list[dict[str, str]] = []
    freshness = config["freshness_days"]
    if climate["oni"]["staleness_days"] > int(freshness["oni"]):
        alerts.append({"severity": "warning", "code": "ONI_STALE", "message": "ONI 更新超过允许时滞"})
    if climate["rainfall"]["staleness_days"] > int(freshness["precipitation"]):
        alerts.append({"severity": "critical", "code": "RAIN_STALE", "message": "主产区降水数据已过期"})
    for symbol, metrics in markets.items():
        if metrics["staleness_days"] > int(freshness["futures"]):
            alerts.append({"severity": "critical", "code": f"{symbol}_PRICE_STALE", "message": f"{symbol} 期货数据已过期"})
        if any(value for key, value in metrics["quality"].items() if key != "rows"):
            alerts.append({"severity": "critical", "code": f"{symbol}_QUALITY", "message": f"{symbol} 主力序列未通过质量门禁"})
    for region, metrics in climate["rainfall"]["regions"].items():
        if metrics["anomaly_pct"] is not None and metrics["anomaly_pct"] <= float(config["rainfall_warning_pct"]):
            alerts.append({"severity": "warning", "code": f"{region.upper()}_DRY", "message": f"{region} 近 30 日降水显著低于历史同期"})
    for item in fundamentals["items"]:
        if item["status"] != "OK":
            alerts.append({"severity": "critical", "code": f"FUND_{item['dataset'].upper()}", "message": f"基本面指标缺失或过期：{item['label']}"})

    market_confirmation = assess_cta_confirmation(
        cta, int(config["trend_confirmation_days"])
    )
    research_gate = {
        "climate": (
            "CONFIRMED"
            if climate["roni"]["state"] == "El Nino" and climate["soi"]["soi"] < 0
            else "NOT_CONFIRMED"
        ),
        "fundamentals": fundamentals["status"],
        "market_cta": market_confirmation["status"],
    }
    research_gate["overall"] = (
        "READY_FOR_ALLOCATION_REVIEW"
        if research_gate == {"climate": "CONFIRMED", "fundamentals": "OK", "market_cta": "CONFIRMED"}
        else "KEEP_MONITORING"
    )
    external_observations = build_external_observation_status(
        data_dir,
        as_of_ts,
        config.get("external_freshness_days"),
    )
    climate["external_observations"] = external_observations
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of_ts.date().isoformat(),
        "data_as_of": max(metrics["date"] for metrics in markets.values()),
        "research_gate": research_gate,
        "market_confirmation": market_confirmation,
        "external_observations": external_observations,
        "layers": {"climate": climate, "fundamentals": fundamentals, "market": markets, "cta": cta},
        "alerts": alerts,
        "critical_alerts": sum(alert["severity"] == "critical" for alert in alerts),
        "warning_alerts": sum(alert["severity"] == "warning" for alert in alerts),
        "scope_gaps": [
            "境外 BMD FCPO、ICE Sugar No.11、ICE Coffee 长历史尚未接入；早期事件仅能用世界银行现货基准复盘，不能验证可交易期货与跨市场CTA",
            "管理人映射按项目目标延后至尽调数据可用后",
        ],
    }


def flatten_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "as_of": snapshot["as_of"],
        "data_as_of": snapshot["data_as_of"],
        "overall_gate": snapshot["research_gate"]["overall"],
        "climate_gate": snapshot["research_gate"]["climate"],
        "fundamental_gate": snapshot["research_gate"]["fundamentals"],
        "market_cta_gate": snapshot["research_gate"]["market_cta"],
        "critical_alerts": snapshot["critical_alerts"],
        "warning_alerts": snapshot["warning_alerts"],
        "oni": snapshot["layers"]["climate"]["oni"]["oni"],
        "roni": snapshot["layers"]["climate"]["roni"]["roni"],
        "soi": snapshot["layers"]["climate"]["soi"]["soi"],
        "dmi": snapshot["layers"]["climate"]["dmi"]["dmi"],
    }
    for region, metrics in snapshot["layers"]["climate"]["rainfall"]["regions"].items():
        row[f"{region}_rain_anomaly_30d"] = metrics["anomaly_pct"]
        row[f"{region}_dry_days_30d"] = metrics["dry_days_30d"]
    for symbol in ("P", "SR"):
        market = snapshot["layers"]["market"][symbol]
        row[f"{symbol}_close"] = market["close"]
        row[f"{symbol}_return_20d"] = market["price_return_20d"]
        row[f"{symbol}_term_spread"] = market["term_structure"].get("front_second_spread_pct")
        for speed in ("fast", "medium", "slow"):
            metrics = snapshot["layers"]["cta"][symbol][speed]
            row[f"{symbol}_{speed}_position"] = metrics["position"]
            row[f"{symbol}_{speed}_pnl_20d"] = metrics["net_return_20d"]
            row[f"{symbol}_{speed}_persistence"] = metrics["position_persistence_days"]
    return row


def compare_snapshots(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {
        "previous_as_of": previous.get("as_of"),
        "gate_changed": current["research_gate"] != previous.get("research_gate"),
        "added_alerts": sorted(
            {item["code"] for item in current["alerts"]}
            - {item["code"] for item in previous.get("alerts", [])}
        ),
        "cleared_alerts": sorted(
            {item["code"] for item in previous.get("alerts", [])}
            - {item["code"] for item in current["alerts"]}
        ),
    }
    current_oni = current["layers"]["climate"]["oni"]["oni"]
    previous_oni = previous["layers"]["climate"]["oni"]["oni"]
    changes["oni_delta"] = _finite(current_oni - previous_oni)
    for symbol in ("P", "SR"):
        current_close = current["layers"]["market"][symbol]["close"]
        previous_close = previous["layers"]["market"][symbol]["close"]
        changes[f"{symbol}_close_change_pct"] = (
            _finite(current_close / previous_close - 1.0) if previous_close else None
        )
        for speed in ("fast", "medium", "slow"):
            current_pnl = current["layers"]["cta"][symbol][speed]["net_return_20d"]
            previous_pnl = previous["layers"]["cta"][symbol][speed]["net_return_20d"]
            changes[f"{symbol}_{speed}_pnl_20d_delta"] = (
                _finite(current_pnl - previous_pnl)
                if current_pnl is not None and previous_pnl is not None
                else None
            )
    return changes


def write_snapshot(snapshot: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "latest.json"
    if latest_path.exists():
        previous = json.loads(latest_path.read_text(encoding="utf-8"))
        if previous.get("as_of") != snapshot["as_of"]:
            snapshot["changes"] = compare_snapshots(snapshot, previous)
    snapshot.setdefault("changes", None)
    latest_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    history_path = output_dir / "history.csv"
    current = pd.DataFrame([flatten_snapshot(snapshot)])
    if history_path.exists():
        history = pd.read_csv(history_path)
        history = history[history["as_of"].astype(str) != snapshot["as_of"]]
        current = pd.concat([history, current], ignore_index=True)
    current.sort_values("as_of").to_csv(history_path, index=False)
    return latest_path, history_path
