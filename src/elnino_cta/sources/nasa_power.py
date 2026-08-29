from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import requests

POWER_DAILY_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float


def fetch_daily_agroclimate(
    location: Location,
    start: str,
    end: str,
    timeout: int = 60,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch daily precipitation and root-zone soil wetness for one point."""
    params = {
        "parameters": "PRECTOTCORR,GWETROOT",
        "community": "AG",
        "longitude": location.longitude,
        "latitude": location.latitude,
        "start": start.replace("-", ""),
        "end": end.replace("-", ""),
        "format": "JSON",
    }
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    response = client.get(POWER_DAILY_URL, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    parameters = payload.get("properties", {}).get("parameter", {})
    precipitation = parameters.get("PRECTOTCORR")
    soil_wetness = parameters.get("GWETROOT")
    if not isinstance(precipitation, dict) or not isinstance(soil_wetness, dict):
        raise ValueError(f"Unexpected NASA POWER response: {payload.get('messages', payload)}")

    dates = sorted(set(precipitation) | set(soil_wetness))
    frame = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "precip_mm": [precipitation.get(day) for day in dates],
        "root_zone_soil_wetness": [soil_wetness.get(day) for day in dates],
    })
    frame["precip_mm"] = pd.to_numeric(frame["precip_mm"], errors="coerce").replace(-999, pd.NA)
    frame["root_zone_soil_wetness"] = pd.to_numeric(
        frame["root_zone_soil_wetness"], errors="coerce"
    ).replace(-999, pd.NA)
    frame["location"] = location.name
    frame["latitude"] = location.latitude
    frame["longitude"] = location.longitude
    frame["source"] = POWER_DAILY_URL
    return frame


def fetch_daily_precipitation(
    location: Location,
    start: str,
    end: str,
    timeout: int = 60,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Backward-compatible alias retaining the richer agroclimate columns."""
    return fetch_daily_agroclimate(location, start, end, timeout=timeout, session=session)
