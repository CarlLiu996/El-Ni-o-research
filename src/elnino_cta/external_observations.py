from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
import requests

from .sources.earthdata import (
    NDVI_COLUMNS,
    fetch_india_sugar_ndvi,
    fetch_india_sugar_ndvi_ornl,
)
from .sources.imd import IMD_COLUMNS, fetch_imd_monsoon_events
from .sources.nasa_power import Location, fetch_daily_agroclimate


MONSOON_START = (6, 1)
MONSOON_END = (9, 30)
INDIA_REGION = "india_sugar"
MONSOON_COLUMNS = [
    "date",
    "season_year",
    "season_day",
    "current_cumulative_mm",
    "climatology_cumulative_mm",
    "cumulative_departure_pct",
    "historical_percentile",
    "valid_locations",
    "required_locations",
    "status",
    "data_as_of",
    "published_at",
    "downloaded_at",
    "source",
]
EXTERNAL_FILES = {
    "monsoon_cumulative": "india_monsoon_cumulative.csv",
    "imd_events": "india_monsoon_events.csv",
    "ndvi": "india_sugar_ndvi.csv",
}


def _timestamp(year: int, month_day: tuple[int, int]) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month_day[0], day=month_day[1])


def _not_in_season(cutoff: pd.Timestamp, downloaded_at: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "date": cutoff,
            "season_year": cutoff.year,
            "season_day": 0,
            "current_cumulative_mm": pd.NA,
            "climatology_cumulative_mm": pd.NA,
            "cumulative_departure_pct": pd.NA,
            "historical_percentile": pd.NA,
            "valid_locations": 0,
            "required_locations": 2,
            "status": "NOT_IN_SEASON",
            "data_as_of": cutoff,
            "published_at": pd.NA,
            "downloaded_at": downloaded_at,
            "source": "NASA POWER PRECTOTCORR",
        }],
        columns=MONSOON_COLUMNS,
    )


def build_india_monsoon_cumulative(
    weather: pd.DataFrame,
    as_of: str | pd.Timestamp,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    """Build equal-weight India monsoon progress using only complete point histories."""
    required = {"date", "region", "location", "precip_mm", "source"}
    missing = required.difference(weather.columns)
    if missing:
        raise ValueError(f"Missing monsoon input columns: {sorted(missing)}")

    cutoff = pd.Timestamp(as_of).normalize()
    downloaded = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    if cutoff < _timestamp(cutoff.year, MONSOON_START):
        return _not_in_season(cutoff, downloaded)

    daily = weather.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
    daily["precip_mm"] = pd.to_numeric(daily["precip_mm"], errors="coerce")
    daily = daily[
        daily["region"].eq(INDIA_REGION)
        & daily["date"].notna()
        & daily["date"].le(cutoff)
    ].copy()
    if daily.duplicated(["date", "region", "location"]).any():
        raise ValueError("Found duplicate date/region/location observations")

    years = sorted(set(daily["date"].dt.year.tolist()) | {cutoff.year})
    records: list[dict[str, Any]] = []
    for year in years:
        start = _timestamp(year, MONSOON_START)
        end = _timestamp(year, MONSOON_END)
        if year == cutoff.year:
            end = min(end, cutoff)
        if end < start:
            continue
        season = daily[
            daily["date"].between(start, end)
            & daily["date"].dt.month.between(MONSOON_START[0], MONSOON_END[0])
        ]
        dates = pd.date_range(start, end, freq="D")
        cumulative_by_point: dict[str, pd.Series] = {}
        for location, group in season.groupby("location"):
            values = group.set_index("date")["precip_mm"].sort_index().reindex(dates)
            complete = values.notna().cumprod().astype(bool)
            cumulative_by_point[str(location)] = values.fillna(0.0).cumsum().where(complete)
        cumulative = pd.DataFrame(cumulative_by_point, index=dates)
        valid_locations = cumulative.notna().sum(axis=1)
        equal_weight_mean = cumulative.mean(axis=1).where(valid_locations.ge(2))
        for season_day, date in enumerate(dates, start=1):
            records.append({
                "date": date,
                "season_year": year,
                "season_day": season_day,
                "current_cumulative_mm": equal_weight_mean.loc[date],
                "valid_locations": int(valid_locations.loc[date]),
            })

    result = pd.DataFrame(records)
    baseline = result[
        result["season_year"].between(1991, 2020)
        & result["current_cumulative_mm"].notna()
    ].copy()
    baseline["current_cumulative_mm"] = pd.to_numeric(
        baseline["current_cumulative_mm"], errors="coerce"
    )
    normals = baseline.groupby("season_day")["current_cumulative_mm"].mean()
    distributions = baseline.groupby("season_day")["current_cumulative_mm"].apply(list)
    result["climatology_cumulative_mm"] = result["season_day"].map(normals)
    numeric_current = pd.to_numeric(result["current_cumulative_mm"], errors="coerce")
    numeric_normal = pd.to_numeric(result["climatology_cumulative_mm"], errors="coerce")
    result["cumulative_departure_pct"] = numeric_current / numeric_normal - 1.0

    def percentile(row: pd.Series) -> float | Any:
        value = pd.to_numeric(pd.Series([row["current_cumulative_mm"]]), errors="coerce").iloc[0]
        history = pd.to_numeric(
            pd.Series(distributions.get(row["season_day"], [])), errors="coerce"
        ).dropna()
        if pd.isna(value) or history.empty:
            return pd.NA
        return float(history.le(float(value)).mean())

    result["historical_percentile"] = result.apply(percentile, axis=1)
    result["required_locations"] = 2
    result["status"] = result["current_cumulative_mm"].notna().map(
        {True: "OK", False: "INSUFFICIENT_COVERAGE"}
    )
    current_mask = result["season_year"].eq(cutoff.year)
    usable_current = result[current_mask & result["valid_locations"].ge(2)]
    effective_cutoff = cutoff
    current_rows = result[current_mask]
    latest_location_count = int(current_rows.iloc[-1]["valid_locations"]) if not current_rows.empty else 0
    # An API publication lag leaves every configured point absent for the trailing
    # dates.  A partially reported date (one point present) is a real coverage
    # failure and must remain visible instead of being silently trimmed.
    if latest_location_count == 0 and not usable_current.empty:
        effective_cutoff = pd.Timestamp(usable_current["date"].max()).normalize()
        result = result[~(current_mask & result["date"].gt(effective_cutoff))].copy()
    result["data_as_of"] = effective_cutoff
    result["published_at"] = pd.NA
    result["downloaded_at"] = downloaded
    sources = sorted(str(value) for value in daily["source"].dropna().unique())
    result["source"] = "; ".join(sources) or "NASA POWER PRECTOTCORR"
    return result[MONSOON_COLUMNS].sort_values(["season_year", "season_day"]).reset_index(drop=True)


def atomic_write_csv(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(target)


def sanitized_error_status(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "AUTH_REQUIRED"
    if isinstance(exc, FileNotFoundError):
        return "NOT_PUBLISHED"
    if isinstance(exc, requests.RequestException):
        return "FETCH_ERROR"
    return "REVIEW_REQUIRED"


def sanitized_error_details(exc: Exception, dataset: str) -> dict[str, object]:
    status = sanitized_error_status(exc)
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    response_url = getattr(response, "url", None)
    host = urlsplit(str(response_url)).hostname if response_url else None
    actions = {
        "monsoon_cumulative": "重试 NASA POWER，并在报告中沿用最后一个完整数据日",
        "imd_events": "跳过故障入口，继续读取其他 IMD 官方公告入口并人工复核",
        "ndvi": "改走 Earthdata 官方备用接口；需让当前任务进程读取 EARTHDATA_TOKEN",
    }
    impacts = {
        "monsoon_cumulative": "缺少最新季风进度验证，L1产区证据不完整",
        "imd_events": "无法确认有效雨季起止长度，L1产区证据不完整",
        "ndvi": "缺少印度甘蔗长势验证，L1产区证据不完整",
    }
    if status_code:
        reason_code = f"HTTP_{status_code}"
        if status_code == 500:
            reason_cn = "官方数据服务返回 HTTP 500（服务器内部错误）"
        else:
            reason_cn = f"官方数据服务返回 HTTP {status_code}"
    elif isinstance(exc, requests.Timeout):
        reason_code = "TIMEOUT"
        reason_cn = "官方数据服务请求超时"
    elif isinstance(exc, PermissionError):
        reason_code = "AUTH_REQUIRED"
        reason_cn = "当前任务进程未检测到所需访问令牌"
    elif isinstance(exc, FileNotFoundError):
        reason_code = "NOT_PUBLISHED"
        reason_cn = "截至本次刷新，官方文件尚未发布"
    elif isinstance(exc, requests.RequestException):
        reason_code = "NETWORK_ERROR"
        reason_cn = "官方数据服务网络请求失败"
    else:
        reason_code = "REVIEW_REQUIRED"
        reason_cn = "返回结构异常，需要人工复核采集规则"
    return {
        "reason_code": reason_code,
        "reason_cn": reason_cn,
        "source_host": host,
        "next_action": actions[dataset],
        "conclusion_impact": impacts[dataset],
    }


def _placeholder(dataset: str, status: str, as_of: str) -> pd.DataFrame:
    columns = {
        "monsoon_cumulative": MONSOON_COLUMNS,
        "imd_events": IMD_COLUMNS,
        "ndvi": NDVI_COLUMNS,
    }[dataset]
    row = {column: pd.NA for column in columns}
    row["status"] = status
    if "data_as_of" in row:
        row["data_as_of"] = as_of
    if "downloaded_at" in row:
        row["downloaded_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    return pd.DataFrame([row], columns=columns)


def _latest_status(dataset: str, frame: pd.DataFrame) -> str:
    if frame.empty or "status" not in frame:
        return "NOT_PUBLISHED"
    statuses = set(frame["status"].dropna().astype(str))
    if "REVIEW_REQUIRED" in statuses:
        return "REVIEW_REQUIRED"
    if dataset == "imd_events" and statuses.intersection({"OK", "NOT_ANNOUNCED"}):
        return "OK"
    return str(frame.iloc[-1]["status"])


def _artifact_metadata(
    dataset: str,
    target: Path,
    status: str,
    diagnostic: dict[str, object] | None = None,
) -> dict[str, object]:
    frame = pd.read_csv(target) if target.exists() else pd.DataFrame()
    data_date = None
    for column in ("data_as_of", "date", "official_date", "product_date"):
        if column in frame:
            dates = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not dates.empty:
                data_date = dates.max().date().isoformat()
                break
    published_at = None
    for column in ("published_at", "announcement_date"):
        if column in frame:
            dates = pd.to_datetime(frame[column], errors="coerce", utc=True).dropna()
            if not dates.empty:
                published_at = dates.max().isoformat()
                break
    default_sources = {
        "monsoon_cumulative": "https://power.larc.nasa.gov/api/temporal/daily/point",
        "imd_events": "https://mausam.imd.gov.in/responsive/monsooninformation_onset.php",
        "ndvi": "https://modis.ornl.gov/data/modis_webservice.html",
    }
    source_values: list[str] = []
    for column in ("source", "source_url"):
        if column in frame:
            source_values.extend(
                str(value) for value in frame[column].dropna().unique() if str(value).strip()
            )
    coverage_pct = None
    if not frame.empty and dataset == "monsoon_cumulative":
        latest = frame.iloc[-1]
        required = pd.to_numeric(pd.Series([latest.get("required_locations")]), errors="coerce").iloc[0]
        valid = pd.to_numeric(pd.Series([latest.get("valid_locations")]), errors="coerce").iloc[0]
        if pd.notna(required) and required > 0 and pd.notna(valid):
            coverage_pct = min(float(valid / required), 1.0)
    elif not frame.empty and dataset == "ndvi" and "valid_pixel_ratio" in frame:
        coverage = pd.to_numeric(frame["valid_pixel_ratio"], errors="coerce").dropna()
        coverage_pct = float(coverage.iloc[-1]) if not coverage.empty else None
    metadata = {
        "status": status,
        "path": target.as_posix(),
        "rows": len(frame),
        "data_as_of": data_date,
        "published_at": published_at,
        "coverage_pct": coverage_pct,
        "source": "; ".join(sorted(set(source_values))) or default_sources[dataset],
        "reason": "" if status == "OK" else status,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None,
    }
    if diagnostic:
        metadata.update(diagnostic)
    return metadata


def _fetch_india_weather(
    locations: list[dict[str, object]],
    end: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for item in locations:
        frame = fetch_daily_agroclimate(Location(**item), "1991-01-01", end)
        frame["region"] = INDIA_REGION
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def refresh_external_observations(
    output_dir: Path,
    as_of: str,
    regions_path: Path,
    normals_path: Path,
    ndvi_start: str = "2001-01-01",
    source_bundle: dict[str, object] | None = None,
    refreshed_at: str | None = None,
) -> dict[str, object]:
    regions = json.loads(regions_path.read_text(encoding="utf-8"))
    locations = regions[INDIA_REGION]
    climate_dir = output_dir / "climate"
    climate_dir.mkdir(parents=True, exist_ok=True)
    refreshed = refreshed_at or pd.Timestamp.now(tz="UTC").isoformat()
    dataset_meta: dict[str, dict[str, object]] = {}
    failures = 0

    def run_dataset(dataset: str) -> pd.DataFrame:
        if dataset == "monsoon_cumulative":
            if source_bundle is not None and "weather" in source_bundle:
                weather = source_bundle["weather"]
            else:
                weather = _fetch_india_weather(locations, as_of)
            if not isinstance(weather, pd.DataFrame):
                raise TypeError("weather source must be a DataFrame")
            return build_india_monsoon_cumulative(weather, as_of, refreshed)
        if dataset == "imd_events":
            if source_bundle is not None and "imd" in source_bundle:
                imd = source_bundle["imd"]
            else:
                imd = fetch_imd_monsoon_events(pd.Timestamp(as_of).year, normals_path)
            if not isinstance(imd, pd.DataFrame):
                raise TypeError("IMD source must be a DataFrame")
            return imd
        if source_bundle is not None and "ndvi" in source_bundle:
            ndvi = source_bundle["ndvi"]
        else:
            if os.getenv("EARTHDATA_HDF_FALLBACK") == "1":
                ndvi = fetch_india_sugar_ndvi(
                    locations,
                    start=ndvi_start,
                    end=as_of,
                    cache_dir=output_dir.parent / "raw" / "earthdata",
                )
            else:
                try:
                    ndvi = fetch_india_sugar_ndvi_ornl(
                        locations,
                        start=ndvi_start,
                        end=as_of,
                    )
                except requests.RequestException:
                    if not os.getenv("EARTHDATA_TOKEN"):
                        raise
                    ndvi = fetch_india_sugar_ndvi(
                        locations,
                        start=ndvi_start,
                        end=as_of,
                        cache_dir=output_dir.parent / "raw" / "earthdata",
                    )
        if not isinstance(ndvi, pd.DataFrame):
            raise TypeError("NDVI source must be a DataFrame")
        return ndvi

    error_keys = {
        "monsoon_cumulative": "weather_error",
        "imd_events": "imd_error",
        "ndvi": "ndvi_error",
    }
    for dataset, filename in EXTERNAL_FILES.items():
        target = climate_dir / filename
        diagnostic: dict[str, object] | None = None
        try:
            if source_bundle is not None and error_keys[dataset] in source_bundle:
                error = source_bundle[error_keys[dataset]]
                if isinstance(error, Exception):
                    raise error
                raise RuntimeError("Injected source error")
            frame = run_dataset(dataset)
            status = _latest_status(dataset, frame)
            atomic_write_csv(frame, target)
        except Exception as exc:
            failures += 1
            status = sanitized_error_status(exc)
            diagnostic = sanitized_error_details(exc, dataset)
            if not target.exists():
                atomic_write_csv(_placeholder(dataset, status, as_of), target)
        dataset_meta[dataset] = _artifact_metadata(dataset, target, status, diagnostic)

    manifest: dict[str, object] = {
        "overall": "PARTIAL" if failures else "OK",
        "as_of": as_of,
        "refreshed_at": refreshed,
        "datasets": dataset_meta,
    }
    manifest_path = climate_dir / "external_manifest.json"
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(manifest_path)
    return manifest
