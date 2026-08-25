from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

from elnino_cta.sources.faostat import parse_faostat_crop_history


def make_faostat_zip(*, duplicate: bool = False) -> bytes:
    rows: list[dict[str, object]] = []
    targets = (
        ("India", "Sugar cane"),
        ("Brazil", "Sugar cane"),
        ("Indonesia", "Oil palm fruit"),
        ("Malaysia", "Oil palm fruit"),
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


def test_parse_faostat_crop_history_filters_required_series() -> None:
    frame = parse_faostat_crop_history(
        make_faostat_zip(), downloaded_at="2026-08-25T00:00:00Z"
    )

    assert set(frame["area"]) == {"India", "Brazil", "Indonesia", "Malaysia"}
    assert set(frame["item"]) == {"Sugar cane", "Oil palm fruit"}
    assert set(frame["element"]) == {"Production", "Area harvested", "Yield"}
    assert not frame.duplicated(["area", "item", "element", "year"]).any()
    assert {"unit", "value", "flag", "source", "downloaded_at"}.issubset(frame.columns)
    assert set(frame["downloaded_at"]) == {"2026-08-25T00:00:00Z"}


def test_parse_faostat_crop_history_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="Duplicate FAOSTAT"):
        parse_faostat_crop_history(make_faostat_zip(duplicate=True))
