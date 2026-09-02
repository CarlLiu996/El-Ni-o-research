from __future__ import annotations

import re
from io import BytesIO

import pandas as pd
import requests


WORLD_BANK_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
COMMODITY_ALIASES = {
    "palm_oil": {"Palm oil"},
    "sugar": {"Sugar, world"},
    "cotton": {"Cotton, A Index"},
    "corn": {"Maize"},
    "soybeans": {"Soybeans"},
    "soybean_meal": {"Soybean meal"},
    "soybean_oil": {"Soybean oil"},
    "rapeseed_oil": {"Rapeseed oil"},
}
COMMODITY_UNITS = {
    "palm_oil": "USD/mt",
    "sugar": "USD/kg",
    "cotton": "USD/kg",
    "corn": "USD/mt",
    "soybeans": "USD/mt",
    "soybean_meal": "USD/mt",
    "soybean_oil": "USD/mt",
    "rapeseed_oil": "USD/mt",
}
WORLD_BANK_COLUMNS = ["date", "commodity", "price", "unit", "source", "downloaded_at"]


def parse_month(value: object) -> pd.Timestamp:
    match = re.fullmatch(r"(\d{4})M(\d{2})", str(value).strip())
    if match:
        parsed = pd.Timestamp(year=int(match.group(1)), month=int(match.group(2)), day=1)
    else:
        parsed = pd.to_datetime(value, errors="coerce")
    return parsed + pd.offsets.MonthEnd(0) if pd.notna(parsed) else pd.NaT


def parse_world_bank_prices(
    content: bytes,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    raw = pd.read_excel(BytesIO(content), sheet_name="Monthly Prices", header=None, engine="openpyxl")
    header_matches = raw.index[raw.iloc[:, 0].astype(str).str.strip().eq("Date")].tolist()
    data_offset = 1
    if len(header_matches) == 1:
        header_row = header_matches[0]
    else:
        alias_names = set().union(*COMMODITY_ALIASES.values())
        candidates = [
            index for index, row in raw.iterrows()
            if alias_names.issubset({str(value).strip() for value in row.dropna()})
        ]
        if len(candidates) != 1:
            raise ValueError("Could not identify World Bank monthly header row")
        header_row = candidates[0]
        data_offset = 2
    data = raw.iloc[header_row + data_offset:].copy()
    data.columns = [str(value).strip() for value in raw.iloc[header_row]]
    data = data.rename(columns={data.columns[0]: "date"})
    records: list[pd.DataFrame] = []
    for commodity, aliases in COMMODITY_ALIASES.items():
        matches = [column for column in data.columns if column in aliases]
        if len(matches) != 1:
            raise ValueError(f"Expected one World Bank column for {commodity}, found {matches}")
        subset = data[["date", matches[0]]].rename(columns={matches[0]: "price"}).copy()
        subset["commodity"] = commodity
        records.append(subset)
    result = pd.concat(records, ignore_index=True)
    result["date"] = result["date"].map(parse_month)
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    result = result.dropna(subset=["date", "price"])
    result = result[result["price"] > 0].copy()
    result["unit"] = result["commodity"].map(COMMODITY_UNITS)
    result["source"] = WORLD_BANK_MONTHLY_URL
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    if result.duplicated(["date", "commodity"]).any():
        raise ValueError("Duplicate World Bank monthly commodity keys")
    return result[WORLD_BANK_COLUMNS].sort_values(["commodity", "date"]).reset_index(drop=True)


def fetch_world_bank_prices(
    timeout: int = 120,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    response = client.get(WORLD_BANK_MONTHLY_URL, timeout=timeout)
    response.raise_for_status()
    return parse_world_bank_prices(response.content)
