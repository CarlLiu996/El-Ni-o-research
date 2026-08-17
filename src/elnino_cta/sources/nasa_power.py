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


def fetch_daily_precipitation(
    location: Location,
    start: str,
    end: str,
    timeout: int = 60,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch daily corrected precipitation for one representative point."""
    params = {
        "parameters": "PRECTOTCORR",
        "community": "AG",
        "longitude": location.longitude,
        "latitude": location.latitude,
        "start": start.replace("-", ""),
        "end": end.replace("-", ""),
        "format": "JSON",
    }
    client = session or requests.Session()
    response = client.get(POWER_DAILY_URL, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    values = payload.get("properties", {}).get("parameter", {}).get("PRECTOTCORR")
    if not isinstance(values, dict):
        raise ValueError(f"Unexpected NASA POWER response: {payload.get('messages', payload)}")

    frame = pd.DataFrame({"date": pd.to_datetime(list(values)), "precip_mm": list(values.values())})
    frame["precip_mm"] = pd.to_numeric(frame["precip_mm"], errors="coerce").replace(-999, pd.NA)
    frame["location"] = location.name
    frame["latitude"] = location.latitude
    frame["longitude"] = location.longitude
    frame["source"] = POWER_DAILY_URL
    return frame
