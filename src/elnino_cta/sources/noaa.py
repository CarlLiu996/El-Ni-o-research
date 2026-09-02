from __future__ import annotations

from html.parser import HTMLParser
from io import StringIO

import pandas as pd
import requests

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
RONI_URL = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/"
SOI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/soi"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"

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


class _RoniTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_target = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "roni-v5-table2":
            self.in_target = True
        elif self.in_target and tag == "tr":
            self.current_row = []
        elif self.in_target and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.in_target and self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_target and tag in {"th", "td"} and self.in_cell:
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.in_cell = False
        elif self.in_target and tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
        elif self.in_target and tag == "table":
            if self.current_row and self.current_row not in self.rows:
                self.rows.append(self.current_row)
            self.in_target = False


def fetch_roni(timeout: int = 30, session: requests.Session | None = None) -> pd.DataFrame:
    """Fetch NOAA's official Relative Oceanic Nino Index table."""
    client = session or requests.Session()
    response = client.get(RONI_URL, timeout=timeout)
    response.raise_for_status()
    parser = _RoniTableParser()
    parser.feed(response.text)
    if len(parser.rows) < 2:
        raise ValueError("Unexpected NOAA RONI table")
    seasons = [value[:3] for value in parser.rows[0][1:]]
    records: list[dict] = []
    for row in parser.rows[1:]:
        try:
            year = int(row[0])
        except (ValueError, IndexError):
            continue
        for season, value in zip(seasons, row[1:]):
            numeric = pd.to_numeric(value, errors="coerce")
            if pd.isna(numeric) or season not in _MID_MONTH:
                continue
            records.append({
                "date": pd.Timestamp(year=year, month=_MID_MONTH[season], day=1),
                "season": season,
                "year": year,
                "roni": float(numeric),
                "source": RONI_URL,
            })
    if not records:
        raise ValueError("NOAA RONI table contained no observations")
    return pd.DataFrame(records).sort_values("date")


def _parse_monthly_year_rows(text: str, value_name: str, source: str) -> pd.DataFrame:
    records: list[dict] = []
    for line in text.splitlines():
        fields = line.split()
        if not fields or len(fields[0]) != 4 or not fields[0].isdigit():
            continue
        year = int(fields[0])
        for month, raw in enumerate(fields[1:13], 1):
            value = pd.to_numeric(raw, errors="coerce")
            if pd.isna(value) or float(value) <= -99:
                continue
            records.append({
                "date": pd.Timestamp(year=year, month=month, day=1),
                value_name: float(value),
                "source": source,
            })
    if not records:
        raise ValueError(f"Unexpected monthly index payload for {value_name}")
    return pd.DataFrame(records).sort_values("date")


def fetch_soi(timeout: int = 30, session: requests.Session | None = None) -> pd.DataFrame:
    """Fetch NOAA CPC standardized Southern Oscillation Index."""
    client = session or requests.Session()
    response = client.get(SOI_URL, timeout=timeout)
    response.raise_for_status()
    marker = "(STAND TAHITI - STAND DARWIN)"
    sections = response.text.split(marker)
    standardized_section = sections[1] if len(sections) >= 3 else response.text
    return _parse_monthly_year_rows(standardized_section, "soi", SOI_URL)


def fetch_dmi(timeout: int = 30, session: requests.Session | None = None) -> pd.DataFrame:
    """Fetch NOAA PSL Dipole Mode Index, a monthly Indian Ocean Dipole proxy."""
    client = session or requests.Session()
    response = client.get(DMI_URL, timeout=timeout)
    response.raise_for_status()
    return _parse_monthly_year_rows(response.text, "dmi", DMI_URL)
