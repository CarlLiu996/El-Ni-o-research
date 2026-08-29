from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests


FAOSTAT_CROPS_URL = (
    "https://bulks-faostat.fao.org/production/"
    "Crops_Livestock_E_All_Data_(Normalized).zip"
)
FAOSTAT_CATALOG_URL = "https://data.fao.org/catalog/iso/d24a448b-3b62-4c09-8c1d-4a39bb599876"
FAOSTAT_SMART_CSV_API = "https://api.data.apps.fao.org/api/v2/bigquery"
FAOSTAT_SQL_URL = (
    "https://data.apps.fao.org/catalog/dataset/8f56158c-b8de-4b72-84ba-458415e2cab0/"
    "resource/49452189-c6eb-4061-b718-964ec4474980/download/"
    "crop-production-yield-harvest-qcl-query.sql"
)
ITEM_CODES = {
    "Maize (corn)": 56,
    "Sugar cane": 156,
    "Soya beans": 236,
    "Oil palm fruit": 254,
    "Rape or colza seed": 270,
    "Seed cotton, unginned": 328,
}
TARGETS = {
    ("India", "Sugar cane"),
    ("Brazil", "Sugar cane"),
    ("Indonesia", "Oil palm fruit"),
    ("Malaysia", "Oil palm fruit"),
    ("United States of America", "Seed cotton, unginned"),
    ("India", "Seed cotton, unginned"),
    ("Pakistan", "Seed cotton, unginned"),
    ("China, mainland", "Seed cotton, unginned"),
    ("United States of America", "Maize (corn)"),
    ("China, mainland", "Maize (corn)"),
    ("Brazil", "Maize (corn)"),
    ("Argentina", "Maize (corn)"),
    ("United States of America", "Soya beans"),
    ("Brazil", "Soya beans"),
    ("Argentina", "Soya beans"),
    ("Canada", "Rape or colza seed"),
    ("Australia", "Rape or colza seed"),
    ("China, mainland", "Rape or colza seed"),
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
    if session is None:
        client.trust_env = False
    frames: list[pd.DataFrame] = []
    downloaded_at = pd.Timestamp.now(tz="UTC").isoformat()
    for item_code in ITEM_CODES.values():
        response = client.get(
            FAOSTAT_SMART_CSV_API,
            params={"sql_url": FAOSTAT_SQL_URL, "item_code": item_code},
            timeout=timeout,
        )
        response.raise_for_status()
        frames.append(parse_faostat_catalog_csv(response.content, downloaded_at=downloaded_at))
    result = pd.concat(frames, ignore_index=True)
    keys = ["area", "item", "element", "year"]
    if result.duplicated(keys).any():
        raise ValueError("Duplicate FAOSTAT crop keys")
    present = set(result[["area", "item", "element"]].itertuples(index=False, name=None))
    required_series = {(area, item, element) for area, item in TARGETS for element in ELEMENTS}
    if present != required_series:
        raise ValueError(f"Missing FAOSTAT targets: {sorted(required_series - present)}")
    return result.sort_values(keys).reset_index(drop=True)


def parse_faostat_catalog_csv(
    content: bytes,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    raw = pd.read_csv(BytesIO(content))
    required = {
        "country_name_en", "item", "year",
        "yield_kg_ha", "yield_kg_ha_flag",
        "production_tonnes", "production_tonnes_flag",
        "area_harvested_ha", "area_harvested_ha_flag",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"Unexpected FAOSTAT catalog columns: {sorted(missing)}")
    measures = (
        ("Yield", "yield_kg_ha", "yield_kg_ha_flag", "kg/ha"),
        ("Production", "production_tonnes", "production_tonnes_flag", "t"),
        ("Area harvested", "area_harvested_ha", "area_harvested_ha_flag", "ha"),
    )
    frames: list[pd.DataFrame] = []
    for element, value_column, flag_column, unit in measures:
        frame = raw[["country_name_en", "item", "year", value_column, flag_column]].rename(
            columns={"country_name_en": "area", value_column: "value", flag_column: "flag"}
        )
        frame["element"] = element
        frame["unit"] = unit
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    target_pairs = pd.MultiIndex.from_frame(result[["area", "item"]]).isin(TARGETS)
    result = result.loc[target_pairs].copy()
    result["year"] = pd.to_numeric(result["year"], errors="raise").astype(int)
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["value"])
    result["source"] = FAOSTAT_CATALOG_URL
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    keys = ["area", "item", "element", "year"]
    if result.duplicated(keys).any():
        raise ValueError("Duplicate FAOSTAT crop keys")
    return result[[
        "area", "item", "element", "year", "unit", "value", "flag", "source", "downloaded_at"
    ]].sort_values(keys).reset_index(drop=True)
