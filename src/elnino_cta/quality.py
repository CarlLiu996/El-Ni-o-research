from __future__ import annotations

import pandas as pd


def audit_timeseries(frame: pd.DataFrame, price_column: str = "close") -> dict[str, int | float]:
    """Return a compact, serializable quality report for a dated series."""
    if "date" not in frame:
        raise ValueError("Data must contain a date column")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    report: dict[str, int | float] = {
        "rows": int(len(frame)),
        "invalid_dates": int(dates.isna().sum()),
        "duplicate_dates": int(dates.duplicated().sum()),
        "missing_price": int(frame[price_column].isna().sum()) if price_column in frame else len(frame),
    }
    if price_column in frame:
        prices = pd.to_numeric(frame[price_column], errors="coerce")
        report["nonpositive_price"] = int((prices <= 0).sum())
    return report


def assert_research_ready(frame: pd.DataFrame, price_column: str = "close") -> None:
    report = audit_timeseries(frame, price_column)
    failures = {key: value for key, value in report.items() if key != "rows" and value}
    if failures:
        raise ValueError(f"Data quality gate failed: {failures}")
