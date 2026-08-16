from __future__ import annotations

from io import StringIO

import pandas as pd
import requests

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

_MID_MONTH = {
    "DJF": 1,
    "JFM": 2,
    "FMA": 3,
    "MAM": 4,
    "AMJ": 5,
    "MJJ": 6,
    "JJA": 7,
    "JAS": 8,
    "ASO": 9,
    "SON": 10,
    "OND": 11,
    "NDJ": 12,
}


def fetch_oni(timeout: int = 30, session: requests.Session | None = None) -> pd.DataFrame:
    """Fetch NOAA CPC Oceanic Nino Index data.

    The date is the middle month of the overlapping three-month season. NOAA's
    TOTAL column is retained as the underlying Nino 3.4 SST value and ANOM as ONI.
    """
    client = session or requests.Session()
    response = client.get(ONI_URL, timeout=timeout)
    response.raise_for_status()
    raw = pd.read_csv(StringIO(response.text), sep=r"\s+")
    required = {"SEAS", "YR", "TOTAL", "ANOM"}
    if not required.issubset(raw.columns):
        raise ValueError(f"Unexpected NOAA ONI columns: {list(raw.columns)}")

    frame = raw.rename(
        columns={"SEAS": "season", "YR": "year", "TOTAL": "nino34_sst", "ANOM": "oni"}
    )
    frame["date"] = pd.to_datetime(
        {"year": frame["year"], "month": frame["season"].map(_MID_MONTH), "day": 1}
    )
    frame["source"] = ONI_URL
    return frame[["date", "season", "year", "nino34_sst", "oni", "source"]].sort_values("date")
