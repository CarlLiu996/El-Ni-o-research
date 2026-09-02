from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from elnino_cta.sources.faostat import (
    parse_faostat_catalog_csv,
    parse_faostat_crop_history,
)


def make_faostat_zip(*, duplicate: bool = False) -> bytes:
    rows: list[dict[str, object]] = []
    targets = (
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
    )
    for area, item in targets:
        for element, unit, value in (
            ("Production", "t", 1000.0),
            ("Area harvested", "ha", 100.0),
            ("Yield", "100 mg/ha", 10000.0),
        ):
            rows.append({
                "Area": area,
                "Item": item,
                "Element": element,
                "Year": 2020,
                "Unit": unit,
                "Value": value,
                "Flag": "A",
            })
    rows.append({
        "Area": "France", "Item": "Wheat", "Element": "Production",
        "Year": 2020, "Unit": "t", "Value": 20.0, "Flag": "A",
    })
    if duplicate:
        rows.append(dict(rows[0]))
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("Crops_Livestock_E_All_Data_Normalized.csv", pd.DataFrame(rows).to_csv(index=False))
    return buffer.getvalue()


def test_parse_faostat_crop_history_filters_six_crop_chain_inputs() -> None:
    frame = parse_faostat_crop_history(
        make_faostat_zip(), downloaded_at="2026-08-25T00:00:00Z"
    )

    assert set(frame[["area", "item"]].itertuples(index=False, name=None)) == {
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
    assert set(frame["item"]) == {
        "Sugar cane", "Oil palm fruit", "Seed cotton, unginned",
        "Maize (corn)", "Soya beans", "Rape or colza seed",
    }
    assert set(frame["element"]) == {"Production", "Area harvested", "Yield"}
    assert not frame.duplicated(["area", "item", "element", "year"]).any()
    assert {"unit", "value", "flag", "source", "downloaded_at"}.issubset(frame.columns)
    assert set(frame["downloaded_at"]) == {"2026-08-25T00:00:00Z"}


def test_parse_faostat_crop_history_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="Duplicate FAOSTAT"):
        parse_faostat_crop_history(make_faostat_zip(duplicate=True))


def test_parse_faostat_catalog_csv_converts_wide_measures_and_flags() -> None:
    content = pd.DataFrame({
        "country_name_en": ["India", "France"],
        "item_code": [156, 156],
        "item": ["Sugar cane", "Sugar cane"],
        "year": [2024, 2024],
        "yield_kg_ha": [80000.0, 70000.0],
        "yield_kg_ha_flag": ["A", "E"],
        "production_tonnes": [400000000.0, 100.0],
        "production_tonnes_flag": ["A", "E"],
        "area_harvested_ha": [5000000.0, 2.0],
        "area_harvested_ha_flag": ["A", "E"],
    }).to_csv(index=False).encode()

    result = parse_faostat_catalog_csv(content, downloaded_at="2026-08-25T00:00:00Z")

    assert set(result["element"]) == {"Production", "Area harvested", "Yield"}
    assert set(result["area"]) == {"India"}
    assert set(result["flag"]) == {"A"}
    assert set(result["source"]).pop().startswith("https://data.fao.org/")
