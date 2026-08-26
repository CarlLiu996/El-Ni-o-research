from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import requests

from .quality import audit_timeseries
from .signals import LOOKBACKS, build_cta_proxy, build_roll_adjusted_dominant
from .historical_review import (
    build_event_cta_paths,
    build_event_evidence_panel,
    build_event_price_paths,
    build_external_climate_evidence,
    build_index_evidence,
    build_monthly_cta_proxy,
    build_weather_evidence,
    build_yield_evidence,
    identify_enso_events,
    make_pattern_input,
    summarize_event_cta,
    summarize_existing_daily_cta,
    summarize_patterns,
)
from .sources.faostat import fetch_faostat_crop_history
from .sources.futures import fetch_sina_continuous, fetch_tushare_research_data
from .sources.nasa_power import Location, fetch_daily_agroclimate
from .sources.noaa import fetch_dmi, fetch_oni, fetch_roni, fetch_soi
from .sources.world_bank import fetch_world_bank_prices


HISTORY_FILES = {
    "events": "enso_events.csv",
    "evidence": "event_evidence_panel.csv",
    "price_paths": "event_price_paths.csv",
    "cta_paths": "event_cta_paths.csv",
    "cta_summary": "event_cta_summary.csv",
    "daily_cta_summary": "existing_daily_cta_summary.csv",
    "patterns": "historical_patterns.csv",
    "current_context": "current_event_context.csv",
}


def refresh_historical_climate(
    output_dir: Path,
    start: str,
    end: str,
    regions_path: Path,
) -> dict[str, object]:
    climate_dir = output_dir / "climate"
    climate_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame] = {}
    source_status: dict[str, str] = {}
    for name, fetcher in (("oni", fetch_oni), ("soi", fetch_soi), ("dmi", fetch_dmi)):
        target = climate_dir / f"noaa_{name}.csv"
        try:
            frame = fetcher()
            temporary = target.with_suffix(".tmp")
            frame.to_csv(temporary, index=False)
            temporary.replace(target)
            source_status[name] = "LIVE"
        except requests.RequestException:
            if not target.exists():
                raise
            frame = pd.read_csv(target)
            source_status[name] = "CACHED_FALLBACK"
        frames[name] = frame
    regions = json.loads(regions_path.read_text(encoding="utf-8"))
    weather_frames: list[pd.DataFrame] = []
    weather_target = climate_dir / "nasa_power_precipitation.csv"
    try:
        for region, items in regions.items():
            for item in items:
                frame = fetch_daily_agroclimate(Location(**item), start, end)
                frame["region"] = region
                weather_frames.append(frame)
        if not weather_frames:
            raise ValueError("No historical weather locations configured")
        combined = pd.concat(weather_frames, ignore_index=True)
        weather_temporary = weather_target.with_suffix(".tmp")
        combined.to_csv(weather_temporary, index=False)
        weather_temporary.replace(weather_target)
        source_status["weather"] = "LIVE"
    except requests.RequestException:
        if not weather_target.exists():
            raise
        combined = pd.read_csv(weather_target)
        source_status["weather"] = "CACHED_FALLBACK"

    def coverage(frame: pd.DataFrame) -> dict[str, str | None]:
        dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
        return {
            "min_date": dates.min().date().isoformat() if not dates.empty else None,
            "max_date": dates.max().date().isoformat() if not dates.empty else None,
        }

    return {
        "start": start,
        "end": end,
        "source_status": source_status,
        "rows": {**{name: len(frame) for name, frame in frames.items()}, "weather": len(combined)},
        "coverage": {
            **{name: coverage(frame) for name, frame in frames.items()},
            "weather": coverage(combined),
        },
    }


def read_optional_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=columns)


def _history_source_bundle(
    output_dir: Path,
    as_of: str,
    regions_path: Path,
) -> dict[str, object]:
    refresh_historical_climate(
        output_dir=output_dir,
        start="1981-01-01",
        end=as_of,
        regions_path=regions_path,
    )
    daily_signals: dict[tuple[str, str], pd.DataFrame] = {}
    signal_dir = output_dir / "signals"
    for symbol in ("P", "SR"):
        for speed in ("fast", "medium", "slow"):
            path = signal_dir / f"{symbol}_{speed}_proxy_research.csv"
            if path.exists():
                daily_signals[(symbol, speed)] = pd.read_csv(path)
    return {
        "oni": pd.read_csv(output_dir / "climate" / "noaa_oni.csv"),
        "soi": pd.read_csv(output_dir / "climate" / "noaa_soi.csv"),
        "dmi": pd.read_csv(output_dir / "climate" / "noaa_dmi.csv"),
        "weather": pd.read_csv(output_dir / "climate" / "nasa_power_precipitation.csv"),
        "monsoon": read_optional_csv(
            output_dir / "climate" / "india_monsoon_cumulative.csv",
            ["date", "season_year", "cumulative_departure_pct", "valid_locations", "status", "source"],
        ),
        "ndvi": read_optional_csv(
            output_dir / "climate" / "india_sugar_ndvi.csv",
            ["product_date", "z_score", "valid_pixel_ratio", "status", "source"],
        ),
        "crops": fetch_faostat_crop_history(),
        "prices": fetch_world_bank_prices(),
        "daily_signals": daily_signals,
    }


def _frame_dates(frame: pd.DataFrame) -> pd.Series:
    columns = [
        column for column in ("date", "start_date", "peak_date", "observation_date", "product_date")
        if column in frame.columns
    ]
    if not columns:
        return pd.Series(dtype="datetime64[ns]")
    return pd.concat(
        [pd.to_datetime(frame[column], errors="coerce") for column in columns],
        ignore_index=True,
    ).dropna()


def refresh_history(
    output_dir: Path,
    as_of: str,
    source_bundle: dict[str, object] | None = None,
    regions_path: Path = Path("config/regions.json"),
) -> dict[str, object]:
    """Build an independent, no-lookahead historical review data branch."""
    bundle = source_bundle or _history_source_bundle(output_dir, as_of, regions_path)
    events = identify_enso_events(bundle["oni"])
    indices = build_index_evidence(events, bundle["oni"], bundle["soi"], bundle["dmi"])
    weather = build_weather_evidence(events, bundle["weather"])
    yields = build_yield_evidence(events, bundle["crops"])
    external_climate = build_external_climate_evidence(
        events, bundle["monsoon"], bundle["ndvi"]
    )
    evidence = build_event_evidence_panel(events, indices, weather, yields, external_climate)
    price_paths = build_event_price_paths(events, bundle["prices"])
    cta_frames: list[pd.DataFrame] = []
    for commodity, prices in bundle["prices"].groupby("commodity"):
        for speed in ("medium", "slow"):
            frame = build_monthly_cta_proxy(prices, speed)
            frame["commodity"] = commodity
            cta_frames.append(frame)
    monthly_cta = pd.concat(cta_frames, ignore_index=True) if cta_frames else pd.DataFrame()
    cta_paths = build_event_cta_paths(events, monthly_cta) if not monthly_cta.empty else pd.DataFrame()
    cta_summary = summarize_event_cta(cta_paths)
    daily_cta_summary = summarize_existing_daily_cta(
        events, bundle.get("daily_signals", {})
    )
    patterns = summarize_patterns(make_pattern_input(evidence, price_paths, cta_summary))
    current_context = evidence[evidence["status"].eq("ONGOING")].copy()
    outputs = {
        "events": events,
        "evidence": evidence,
        "price_paths": price_paths,
        "cta_paths": cta_paths,
        "cta_summary": cta_summary,
        "daily_cta_summary": daily_cta_summary,
        "patterns": patterns,
        "current_context": current_context,
    }
    history_dir = output_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for key, frame in outputs.items():
        target = history_dir / HISTORY_FILES[key]
        temporary = target.with_suffix(".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(target)
        files[key] = str(target)

    artifacts: dict[str, object] = {}
    for key, frame in outputs.items():
        target = Path(files[key])
        dates = _frame_dates(frame)
        sources = sorted({
            str(value)
            for column in frame.columns
            if column == "source"
            for value in frame[column].dropna().unique()
        })
        artifacts[key] = {
            "path": str(target),
            "rows": len(frame),
            "sources": sources,
            "min_date": dates.min().date().isoformat() if not dates.empty else None,
            "max_date": dates.max().date().isoformat() if not dates.empty else None,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    manifest = {
        "as_of": as_of,
        "downloaded_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "files": files,
        "rows": {key: len(frame) for key, frame in outputs.items()},
        "artifacts": artifacts,
        "boundary": "历史复盘为描述性旁路，不导入当前CTA门槛、仓位或置信度修正",
    }
    manifest_target = history_dir / "manifest.json"
    manifest_temporary = manifest_target.with_suffix(".tmp")
    manifest_temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest_temporary.replace(manifest_target)
    return manifest


def refresh(
    output_dir: Path,
    start: str,
    end: str,
    regions_path: Path,
    futures_provider: str = "auto",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "climate").mkdir(exist_ok=True)
    (output_dir / "futures").mkdir(exist_ok=True)
    (output_dir / "signals").mkdir(exist_ok=True)

    oni = fetch_oni()
    oni = oni[(oni["date"] >= pd.Timestamp(start)) & (oni["date"] <= pd.Timestamp(end))]
    oni.to_csv(output_dir / "climate" / "noaa_oni.csv", index=False)
    climate_indices = {
        "roni": fetch_roni(),
        "soi": fetch_soi(),
        "dmi": fetch_dmi(),
    }
    for name, frame in climate_indices.items():
        frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
        frame.to_csv(output_dir / "climate" / f"noaa_{name}.csv", index=False)
        climate_indices[name] = frame

    region_config = json.loads(regions_path.read_text(encoding="utf-8"))
    rain_frames: list[pd.DataFrame] = []
    for region, locations in region_config.items():
        for item in locations:
            frame = fetch_daily_agroclimate(Location(**item), start, end)
            frame["region"] = region
            rain_frames.append(frame)
    rain = pd.concat(rain_frames, ignore_index=True)
    rain.to_csv(output_dir / "climate" / "nasa_power_precipitation.csv", index=False)

    manifest: dict = {
        "period": {"start": start, "end": end},
        "datasets": {
            "oni": {"rows": len(oni), "provider": "NOAA CPC"},
            "roni": {"rows": len(climate_indices["roni"]), "provider": "NOAA CPC"},
            "soi": {"rows": len(climate_indices["soi"]), "provider": "NOAA CPC"},
            "dmi": {"rows": len(climate_indices["dmi"]), "provider": "NOAA PSL"},
            "agroclimate": {
                "rows": len(rain),
                "provider": "NASA POWER",
                "points": len(rain_frames),
                "parameters": ["PRECTOTCORR", "GWETROOT"],
            },
        },
        "warnings": [],
    }
    if futures_provider not in {"auto", "tushare", "sina"}:
        raise ValueError("futures_provider must be auto, tushare, or sina")
    use_tushare = futures_provider == "tushare"
    if futures_provider == "auto":
        import os

        use_tushare = bool(os.getenv("TUSHARE_TOKEN"))

    for symbol in ["P", "SR"]:
        if use_tushare:
            contracts, mapping, metadata = fetch_tushare_research_data(symbol, start, end)
            contracts.to_csv(output_dir / "futures" / f"{symbol}_contracts_tushare.csv", index=False)
            mapping.to_csv(output_dir / "futures" / f"{symbol}_dominant_mapping_tushare.csv", index=False)
            metadata.to_csv(output_dir / "futures" / f"{symbol}_contract_metadata_tushare.csv", index=False)
            futures = build_roll_adjusted_dominant(mapping, contracts)
            provider_label = "Tushare contract + dominant mapping"
            filename_label = "dominant_tushare"
        else:
            futures = fetch_sina_continuous(symbol)
            futures = futures[(futures["date"] >= pd.Timestamp(start)) & (futures["date"] <= pd.Timestamp(end))]
            provider_label = "AkShare/Sina continuous"
            filename_label = "continuous_exploratory"
            manifest["warnings"].append(
                "AkShare/Sina continuous futures are exploratory because roll/back-adjustment rules are not exposed."
            )
        futures.to_csv(output_dir / "futures" / f"{symbol}_{filename_label}.csv", index=False)
        manifest["datasets"][symbol] = {
            "rows": len(futures),
            "provider": provider_label,
            "quality": audit_timeseries(futures),
        }
        for speed in LOOKBACKS:
            signal = build_cta_proxy(futures, speed)
            suffix = "research" if use_tushare else "exploratory"
            signal.to_csv(output_dir / "signals" / f"{symbol}_{speed}_proxy_{suffix}.csv", index=False)

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return manifest
