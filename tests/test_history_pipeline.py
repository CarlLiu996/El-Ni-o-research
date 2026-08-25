from __future__ import annotations

import json

import pandas as pd

from elnino_cta.pipeline import refresh_history


EXPECTED_HISTORY_FILES = {
    "events", "evidence", "price_paths", "cta_paths", "cta_summary",
    "daily_cta_summary", "patterns", "current_context",
}


def synthetic_source_bundle() -> dict[str, object]:
    index_dates = pd.date_range("2009-01-01", "2012-12-01", freq="MS")
    oni_values = [0.0] * len(index_dates)
    for index, date in enumerate(index_dates):
        if pd.Timestamp("2009-07-01") <= date <= pd.Timestamp("2009-12-01"):
            oni_values[index] = 1.0
        if pd.Timestamp("2012-08-01") <= date <= pd.Timestamp("2012-12-01"):
            oni_values[index] = 0.8
    oni = pd.DataFrame({"date": index_dates, "oni": oni_values, "source": "NOAA CPC"})
    soi = pd.DataFrame({"date": index_dates, "soi": -1.0, "source": "NOAA CPC"})
    dmi = pd.DataFrame({"date": index_dates, "dmi": 0.2, "source": "NOAA PSL"})

    weather_rows = []
    for date in pd.date_range("1991-01-01", "2020-12-01", freq="MS"):
        for region in ("india_sugar", "malaysia_palm"):
            weather_rows.append({
                "date": date, "region": region, "precip_mm": 2.0,
                "root_zone_soil_wetness": 0.5, "source": "NASA POWER",
            })
    weather = pd.DataFrame(weather_rows)
    crops = pd.DataFrame([
        {
            "area": area, "item": item, "element": "Yield", "year": year,
            "unit": "index", "value": 100 + year - 2000, "flag": "A", "source": "FAOSTAT",
        }
        for area, item in (("India", "Sugar cane"), ("Malaysia", "Oil palm fruit"))
        for year in range(2004, 2014)
    ])
    price_dates = pd.date_range("2008-01-31", "2014-12-31", freq="ME")
    prices = pd.concat([
        pd.DataFrame({
            "date": price_dates,
            "commodity": commodity,
            "price": [base + step * index for index in range(len(price_dates))],
            "unit": unit,
            "source": "World Bank Pink Sheet",
        })
        for commodity, base, step, unit in (
            ("palm_oil", 500.0, 2.0, "USD/mt"),
            ("sugar", 0.20, 0.001, "USD/kg"),
        )
    ], ignore_index=True)
    return {
        "oni": oni,
        "soi": soi,
        "dmi": dmi,
        "weather": weather,
        "crops": crops,
        "prices": prices,
        "monsoon": pd.DataFrame(columns=[
            "date", "season_year", "cumulative_departure_pct", "valid_locations", "status", "source"
        ]),
        "ndvi": pd.DataFrame(columns=[
            "product_date", "z_score", "valid_pixel_ratio", "status", "source"
        ]),
        "daily_signals": {},
    }


def test_refresh_history_writes_expected_outputs_without_touching_monitor(tmp_path) -> None:
    monitor = tmp_path / "reports" / "monitoring" / "latest.json"
    monitor.parent.mkdir(parents=True)
    monitor.write_text('{"sentinel": true}', encoding="utf-8")

    result = refresh_history(
        output_dir=tmp_path / "data" / "processed",
        as_of="2026-08-25",
        source_bundle=synthetic_source_bundle(),
    )

    assert set(result["files"]) == EXPECTED_HISTORY_FILES
    assert monitor.read_text(encoding="utf-8") == '{"sentinel": true}'
    manifest = json.loads(
        (tmp_path / "data" / "processed" / "history" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["as_of"] == "2026-08-25"
    assert all(item["sha256"] for item in manifest["artifacts"].values())


def test_history_refresh_parser_is_independent_command() -> None:
    from elnino_cta.cli import build_parser

    args = build_parser().parse_args([
        "history-refresh", "--output", "somewhere", "--as-of", "2026-08-25"
    ])

    assert args.command == "history-refresh"
    assert args.as_of == "2026-08-25"
