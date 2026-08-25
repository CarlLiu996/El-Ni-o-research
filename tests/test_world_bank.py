from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest
from openpyxl import Workbook

from elnino_cta.sources.world_bank import parse_world_bank_prices


def make_pink_sheet_xlsx(*, include_sugar: bool = True) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Monthly Prices"
    sheet.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    sheet.append(["Updated monthly"])
    headers = ["Date", "Palm oil"]
    if include_sugar:
        headers.append("Sugar, world")
    sheet.append(headers)
    sheet.append(["2020M01", 800.0] + ([0.30] if include_sugar else []))
    sheet.append([pd.Timestamp("2020-02-01"), 820.0] + ([0.31] if include_sugar else []))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_world_bank_prices_returns_palm_and_sugar_monthly() -> None:
    frame = parse_world_bank_prices(
        make_pink_sheet_xlsx(), downloaded_at="2026-08-25T00:00:00Z"
    )

    assert set(frame["commodity"]) == {"palm_oil", "sugar"}
    assert list(frame.columns) == [
        "date", "commodity", "price", "unit", "source", "downloaded_at"
    ]
    assert not frame.duplicated(["date", "commodity"]).any()
    assert (frame["price"] > 0).all()
    assert set(frame["date"]) == {pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")}
    assert frame.set_index("commodity").loc["palm_oil", "unit"].iloc[0] == "USD/mt"
    assert frame.set_index("commodity").loc["sugar", "unit"].iloc[0] == "USD/kg"


def test_parse_world_bank_prices_rejects_missing_required_commodity() -> None:
    with pytest.raises(ValueError, match="sugar"):
        parse_world_bank_prices(make_pink_sheet_xlsx(include_sugar=False))


def test_parse_world_bank_prices_accepts_current_sheet_without_date_header() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Monthly Prices"
    sheet.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    sheet.append([None, "Palm oil", "Sugar, world"])
    sheet.append([None, "$/mt", "$/kg"])
    sheet.append(["2024M01", 900.0, 0.40])
    buffer = BytesIO()
    workbook.save(buffer)

    result = parse_world_bank_prices(buffer.getvalue())

    assert set(result["commodity"]) == {"palm_oil", "sugar"}
    assert result["date"].eq(pd.Timestamp("2024-01-31")).all()
