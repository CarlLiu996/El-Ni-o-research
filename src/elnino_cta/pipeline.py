from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .quality import audit_timeseries
from .signals import LOOKBACKS, build_cta_proxy
from .sources.futures import fetch_sina_continuous, fetch_tushare_research_data
from .sources.nasa_power import Location, fetch_daily_precipitation
from .sources.noaa import fetch_oni


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

    region_config = json.loads(regions_path.read_text(encoding="utf-8"))
    rain_frames: list[pd.DataFrame] = []
    for region, locations in region_config.items():
        for item in locations:
            frame = fetch_daily_precipitation(Location(**item), start, end)
            frame["region"] = region
            rain_frames.append(frame)
    rain = pd.concat(rain_frames, ignore_index=True)
    rain.to_csv(output_dir / "climate" / "nasa_power_precipitation.csv", index=False)

    manifest: dict = {
        "period": {"start": start, "end": end},
        "datasets": {
            "oni": {"rows": len(oni), "provider": "NOAA CPC"},
            "precipitation": {"rows": len(rain), "provider": "NASA POWER", "points": len(rain_frames)},
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
            mapping_for_join = mapping.rename(columns={"trade_date": "date", "mapping_ts_code": "contract"})
            futures = mapping_for_join.merge(contracts, on=["date", "contract"], how="left")
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
