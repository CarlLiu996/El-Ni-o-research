from __future__ import annotations

import pandas as pd

from elnino_cta.fundamental_summary import build_l2_details, build_l2_summary


def _frame(
    dates: list[str],
    values: list[float],
    *,
    indicator_name: str,
    unit: str = "tonnes",
    note: str = "",
) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "value": values,
        "unit": unit,
        "source": "official source",
        "published_at": pd.to_datetime(["2026-08-01"] * len(dates)),
        "indicator_name": indicator_name,
        "note": note,
    })


def fundamental_frames() -> dict[str, pd.DataFrame]:
    dates = ["2024-07-31", "2025-07-31", "2026-07-31"]
    frames = {
        "mpob_production": _frame(dates, [90, 100, 110], indicator_name="棕榈油产量"),
        "ffb_yield": _frame(dates, [0.9, 1.0, 1.1], indicator_name="鲜果串单产"),
        "mpob_exports": _frame(dates, [90, 100, 110], indicator_name="棕榈油出口"),
        "mpob_inventory": _frame(dates, [90, 100, 110], indicator_name="棕榈油库存"),
        "india_sugar_output": _frame(dates, [90, 100, 110], indicator_name="印度食糖产量"),
        "unica_crush": _frame(
            ["2024-07-15", "2025-07-15", "2026-07-15"],
            [180, 200, 220],
            indicator_name="巴西中南部甘蔗压榨量",
        ),
        "unica_sugar_mix": _frame(
            ["2024-07-01", "2025-07-01", "2026-07-20"],
            [40, 45, 99],
            indicator_name="巴西中南部制糖比",
            unit="pct",
        ),
        "global_sugar_total_use_source": _frame(
            ["2024-12-31", "2025-12-31", "2026-12-31"],
            [180, 190, 200],
            indicator_name="全球食糖消费",
            note="forecast",
        ),
        "global_sugar_ending_stocks_source": _frame(
            ["2024-12-31", "2025-12-31", "2026-12-31"],
            [50, 45, 40],
            indicator_name="全球食糖期末库存",
            note="forecast",
        ),
        "global_sugar_stock_use": _frame(
            ["2024-12-31", "2025-12-31", "2026-12-31"],
            [0.28, 0.24, 0.20],
            indicator_name="全球食糖库销比",
            unit="ratio",
            note="forecast",
        ),
    }
    palm = _frame(
        ["2024-12-31", "2025-12-31", "2026-12-31"],
        [0.20, 0.22, 0.24],
        indicator_name="全球棕榈油库销比",
        unit="ratio",
        note="forecast",
    )
    palm["ending_stocks_1000t"] = [15, 16, 17]
    palm["total_use_1000t"] = [70, 73, 75]
    frames["global_palm_stock_use"] = palm
    return frames


def test_build_l2_summary_has_two_commodities_times_three_categories() -> None:
    summary = build_l2_summary(fundamental_frames(), as_of="2026-08-25")

    assert list(summary[["commodity", "category"]].itertuples(index=False, name=None)) == [
        ("palm_oil", "supply"), ("palm_oil", "demand"), ("palm_oil", "inventory"),
        ("sugar", "supply"), ("sugar", "demand"), ("sugar", "inventory"),
    ]
    assert summary["primary_value"].notna().all()
    assert set(summary["value_type"]) <= {"actual", "forecast"}


def test_sugar_cane_for_sugar_proxy_is_not_named_sugar_output() -> None:
    details = build_l2_details(fundamental_frames())
    row = details.query("metric_id == 'brazil_cane_for_sugar_proxy'").iloc[0]

    assert "甘蔗" in row["label"]
    assert "食糖产量" not in row["label"]


def test_sugar_mix_is_merged_backward_without_future_information() -> None:
    details = build_l2_details(fundamental_frames())
    row = details.query("metric_id == 'brazil_cane_for_sugar_proxy'").iloc[0]

    assert row["value"] == 220 * 45 / 100


def test_forecast_scoring_is_insufficient_and_background_does_not_override_actual() -> None:
    summary = build_l2_summary(fundamental_frames(), as_of="2026-08-25").set_index(
        ["commodity", "category"]
    )

    assert summary.loc[("sugar", "demand"), "direction"] == "INSUFFICIENT"
    assert summary.loc[("palm_oil", "demand"), "direction"] != "INSUFFICIENT"
    assert "forecast" in summary.loc[("palm_oil", "demand"), "driver_value_types"]
