from __future__ import annotations

import json

import pandas as pd


def parse_wind_metric_response(
    stdout: str,
    *,
    expected_code: str,
    expected_name: str,
) -> pd.DataFrame:
    """Parse Wind CLI output and reject natural-language indicator drift."""
    envelope = json.loads(stdout)
    content = envelope.get("content", [])
    if not content or "text" not in content[0]:
        raise ValueError("Wind response contains no metric payload")
    payload = json.loads(content[0]["text"])
    metrics = payload.get("metrics") or payload.get("data", {}).get("data") or []
    if len(metrics) != 1:
        raise ValueError(f"Expected one Wind metric, received {len(metrics)}")
    metric = metrics[0]
    meta = metric.get("meta", {})
    if meta.get("code") != expected_code or meta.get("name") != expected_name:
        raise ValueError(
            "Wind indicator mismatch: "
            f"expected {expected_code}/{expected_name}, "
            f"received {meta.get('code')}/{meta.get('name')}"
        )
    dates = metric.get("date", [])
    values = metric.get("value", [])
    if len(dates) != len(values):
        raise ValueError("Wind metric date/value length mismatch")
    frame = pd.DataFrame({
        "date": pd.to_datetime(dates, format="%Y%m%d", errors="coerce"),
        "close": pd.to_numeric(pd.Series(values), errors="coerce"),
    }).dropna(subset=["date", "close"])
    frame["indicator_code"] = meta.get("code")
    frame["indicator_name"] = meta.get("name")
    frame["unit"] = meta.get("unit")
    frame["source"] = meta.get("source")
    frame["frequency"] = meta.get("freq")
    frame["source_updated_at"] = pd.to_datetime(
        meta.get("updateDate"), format="%Y%m%d", errors="coerce"
    )
    frame["provider"] = "Wind EDB"
    return frame.reset_index(drop=True)
