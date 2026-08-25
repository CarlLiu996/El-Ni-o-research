from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests


FAOSTAT_CROPS_URL = (
    "https://bulks-faostat.fao.org/production/"
    "Crops_Livestock_E_All_Data_(Normalized).zip"
)
TARGETS = {
    ("India", "Sugar cane"),
    ("Brazil", "Sugar cane"),
    ("Indonesia", "Oil palm fruit"),
    ("Malaysia", "Oil palm fruit"),
}
ELEMENTS = {"Production", "Area harvested", "Yield"}


def _select_data_csv(names: list[str]) -> str:
    candidates = [name for name in names if name.lower().endswith(".csv")]
    normalized = [
        name for name in candidates
        if "all_data_normalized" in name.lower() or "all_data_(normalized)" in name.lower()
    ]
    selected = normalized or candidates
    if len(selected) != 1:
        raise ValueError(f"Expected one FAOSTAT normalized CSV, found {selected}")
    return selected[0]


def parse_faostat_crop_history(
    content: bytes,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    with ZipFile(BytesIO(content)) as archive:
        data_name = _select_data_csv(archive.namelist())
        raw = pd.read_csv(archive.open(data_name), encoding="utf-8-sig", low_memory=False)
    renamed = raw.rename(columns={
        "Area": "area",
        "Item": "item",
        "Element": "element",
        "Year": "year",
        "Unit": "unit",
        "Value": "value",
        "Flag": "flag",
    })
    required = {"area", "item", "element", "year", "unit", "value", "flag"}
    if not required.issubset(renamed.columns):
        raise ValueError(f"Unexpected FAOSTAT columns: {list(raw.columns)}")
    target_pairs = pd.MultiIndex.from_frame(renamed[["area", "item"]]).isin(TARGETS)
    result = renamed.loc[target_pairs & renamed["element"].isin(ELEMENTS), list(required)].copy()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["year"] = pd.to_numeric(result["year"], errors="raise").astype(int)
    result = result.dropna(subset=["value"])
    result["source"] = FAOSTAT_CROPS_URL
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    keys = ["area", "item", "element", "year"]
    if result.duplicated(keys).any():
        raise ValueError("Duplicate FAOSTAT crop keys")
    present = set(result[["area", "item", "element"]].itertuples(index=False, name=None))
    required_series = {(area, item, element) for area, item in TARGETS for element in ELEMENTS}
    if present != required_series:
        raise ValueError(f"Missing FAOSTAT targets: {sorted(required_series - present)}")
    return result.sort_values(keys).reset_index(drop=True)


def fetch_faostat_crop_history(
    timeout: int = 120,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    client = session or requests.Session()
    response = client.get(FAOSTAT_CROPS_URL, timeout=timeout)
    response.raise_for_status()
    return parse_faostat_crop_history(response.content)
