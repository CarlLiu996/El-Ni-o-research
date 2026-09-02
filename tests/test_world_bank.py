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
    headers = [
        "Date", "Palm oil", "Cotton, A Index", "Maize", "Soybeans",
        "Soybean meal", "Soybean oil", "Rapeseed oil",
    ]
    if include_sugar:
        headers.insert(2, "Sugar, world")
    sheet.append(headers)
    first = [800.0, 1.70, 170.0, 380.0, 340.0, 760.0, 820.0]
    second = [820.0, 1.75, 175.0, 385.0, 345.0, 770.0, 830.0]
    if include_sugar:
        first.insert(1, 0.30)
        second.insert(1, 0.31)
    sheet.append(["2020M01", *first])
    sheet.append([pd.Timestamp("2020-02-01"), *second])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_world_bank_prices_returns_eight_contract_proxies_monthly() -> None:
    frame = parse_world_bank_prices(
        make_pink_sheet_xlsx(), downloaded_at="2026-08-25T00:00:00Z"
    )

    assert set(frame["commodity"]) == {
        "palm_oil", "sugar", "cotton", "corn", "soybeans",
        "soybean_meal", "soybean_oil", "rapeseed_oil",
    }
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
    sheet.append([
        None, "Palm oil", "Sugar, world", "Cotton, A Index", "Maize",
        "Soybeans", "Soybean meal", "Soybean oil", "Rapeseed oil",
    ])
    sheet.append([None, "$/mt", "$/kg", "$/kg", "$/mt", "$/mt", "$/mt", "$/mt", "$/mt"])
    sheet.append(["2024M01", 900.0, 0.40, 1.80, 180.0, 400.0, 350.0, 800.0, 850.0])
    buffer = BytesIO()
    workbook.save(buffer)

    result = parse_world_bank_prices(buffer.getvalue())

    assert len(set(result["commodity"])) == 8
    assert result["date"].eq(pd.Timestamp("2024-01-31")).all()
