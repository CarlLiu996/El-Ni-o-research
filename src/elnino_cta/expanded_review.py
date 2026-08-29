from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


FOCAL_EVENT_IDS = ("1982-1983", "1997-1998", "2014-2016", "2023-2024")
CHAIN_NAMES = {
    "palm_oil": "棕榈油",
    "sugar": "白糖",
    "cotton": "棉花",
    "corn": "玉米",
    "soybean": "大豆产业链",
    "rapeseed": "菜籽产业链",
}
PRICE_CHAIN_MAP = {
    "palm_oil": "palm_oil",
    "sugar": "sugar",
    "cotton": "cotton",
    "corn": "corn",
    "soybeans": "soybean",
    "soybean_meal": "soybean",
    "soybean_oil": "soybean",
    "rapeseed_oil": "rapeseed",
}


def _mean_numeric(values: pd.Series) -> float | pd.NA:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return float(numeric.mean()) if not numeric.empty else pd.NA


def _event_chain_price_return(
    paths: pd.DataFrame,
    event_id: str,
    chain_id: str,
    horizon: int,
) -> tuple[float | pd.NA, int]:
    current = paths[paths["event_id"].eq(event_id)].copy()
    current["chain_id"] = current["commodity"].map(PRICE_CHAIN_MAP)
    current = current[current["chain_id"].eq(chain_id)]
    returns: list[float] = []
    for _, proxy in current.groupby("commodity"):
        indexed = proxy.drop_duplicates("relative_month").set_index("relative_month")
        if 0 not in indexed.index or horizon not in indexed.index:
            continue
        base = pd.to_numeric(pd.Series([indexed.loc[0, "price_index"]]), errors="coerce").iloc[0]
        end = pd.to_numeric(pd.Series([indexed.loc[horizon, "price_index"]]), errors="coerce").iloc[0]
        if pd.notna(base) and pd.notna(end) and base != 0:
            returns.append(float(end / base - 1.0))
    return (float(pd.Series(returns).mean()), len(returns)) if returns else (pd.NA, 0)


def _event_chain_cta_return(
    summary: pd.DataFrame,
    event_id: str,
    chain_id: str,
    speed: str,
    horizon: int,
) -> tuple[float | pd.NA, int]:
    current = summary[
        summary["event_id"].eq(event_id)
        & summary["speed"].eq(speed)
        & summary["horizon_months"].eq(horizon)
    ].copy()
    current["chain_id"] = current["commodity"].map(PRICE_CHAIN_MAP)
    current = current[current["chain_id"].eq(chain_id)]
    return _mean_numeric(current["net_return"]), int(current["commodity"].nunique())


def build_focal_event_matrix(
    events: pd.DataFrame,
    evidence: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
    *,
    focal_event_ids: Iterable[str] = FOCAL_EVENT_IDS,
) -> pd.DataFrame:
    focal_ids = tuple(focal_event_ids)
    event_index = events.drop_duplicates("event_id").set_index("event_id")
    rows: list[dict[str, object]] = []
    for event_id in focal_ids:
        if event_id not in event_index.index:
            continue
        event = event_index.loc[event_id]
        for chain_id, chain_name in CHAIN_NAMES.items():
            current = evidence[
                evidence["event_id"].eq(event_id)
                & evidence["commodity"].eq(chain_id)
            ].copy()
            metric_ids = current.get("metric_id", pd.Series(dtype=str)).astype(str)
            weather = current[
                metric_ids.str.startswith("weather:")
                & metric_ids.str.endswith(":precip_anomaly_pct")
            ]
            yields = current[
                metric_ids.str.contains(":Yield:", regex=False)
                & metric_ids.str.endswith(":deviation_vs_prior5_median")
            ]
            price_6m, proxy_count_6m = _event_chain_price_return(
                price_paths, event_id, chain_id, 6
            )
            price_12m, proxy_count_12m = _event_chain_price_return(
                price_paths, event_id, chain_id, 12
            )
            cta_medium_6m, cta_count = _event_chain_cta_return(
                cta_summary, event_id, chain_id, "medium", 6
            )
            cta_slow_6m, _ = _event_chain_cta_return(
                cta_summary, event_id, chain_id, "slow", 6
            )
            rows.append({
                "event_id": event_id,
                "chain_id": chain_id,
                "chain_name_cn": chain_name,
                "peak_oni": event.get("peak_oni", pd.NA),
                "weather_precip_anomaly": _mean_numeric(weather.get("value", pd.Series(dtype=float))),
                "yield_deviation": _mean_numeric(yields.get("value", pd.Series(dtype=float))),
                "price_6m": price_6m,
                "price_12m": price_12m,
                "cta_medium_6m": cta_medium_6m,
                "cta_slow_6m": cta_slow_6m,
                "price_proxy_count": max(proxy_count_6m, proxy_count_12m),
                "cta_proxy_count": cta_count,
            })
    result = pd.DataFrame(rows)
    if not result.empty and result.duplicated(["event_id", "chain_id"]).any():
        raise ValueError("Duplicate focal event-chain rows")
    return result


def _share(values: pd.Series, direction: str) -> float | pd.NA:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return pd.NA
    return float(numeric.gt(0).mean() if direction == "positive" else numeric.lt(0).mean())


def summarize_focal_patterns(matrix: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for chain_id, group in matrix.groupby("chain_id", sort=False):
        valid_events = int(group["event_id"].nunique())
        yield_negative_share = _share(group.get("yield_deviation", pd.Series(dtype=float)), "negative")
        price_positive_share = _share(group.get("price_6m", pd.Series(dtype=float)), "positive")
        cta_positive_share = _share(group.get("cta_medium_6m", pd.Series(dtype=float)), "positive")
        conversion_columns = [
            "weather_precip_anomaly", "yield_deviation", "price_6m", "cta_medium_6m"
        ]
        if set(conversion_columns).issubset(group.columns):
            conversion = group[conversion_columns].apply(pd.to_numeric, errors="coerce").dropna()
        else:
            conversion = pd.DataFrame(columns=conversion_columns)
        conversion_success = (
            conversion["weather_precip_anomaly"].lt(0)
            & conversion["yield_deviation"].lt(0)
            & conversion["price_6m"].gt(0)
            & conversion["cta_medium_6m"].gt(0)
        )
        conversion_count = len(conversion)
        conversion_success_count = int(conversion_success.sum())
        conversion_share = (
            float(conversion_success.mean()) if conversion_count else pd.NA
        )
        if conversion_count >= 4 and conversion_share >= 0.70:
            strength = "STABLE_CONVERSION"
        elif conversion_count == 3 and conversion_share >= 2 / 3:
            strength = "LIMITED_CONVERSION"
        elif conversion_count >= 3:
            strength = "NO_STABLE_CONVERSION"
        else:
            strength = "INSUFFICIENT_EVIDENCE"

        cta_values = pd.to_numeric(
            group.get("cta_medium_6m", pd.Series(dtype=float)), errors="coerce"
        ).dropna()
        if len(cta_values) >= 4 and cta_values.gt(0).mean() >= 0.70:
            cta_tendency = "CTA_TENDENCY"
        elif len(cta_values) == 3 and cta_values.gt(0).mean() >= 2 / 3:
            cta_tendency = "LIMITED_CTA_TENDENCY"
        elif len(cta_values) >= 3:
            cta_tendency = "MIXED_CTA"
        else:
            cta_tendency = "INSUFFICIENT_CTA_DATA"
        rows.append({
            "chain_id": chain_id,
            "chain_name_cn": CHAIN_NAMES.get(chain_id, chain_id),
            "valid_events": valid_events,
            "yield_negative_share": yield_negative_share,
            "price_positive_share": price_positive_share,
            "cta_medium_positive_share": cta_positive_share,
            "conversion_valid_events": conversion_count,
            "conversion_success_count": conversion_success_count,
            "conversion_success_share": conversion_share,
            "cta_valid_events": len(cta_values),
            "cta_tendency": cta_tendency,
            "evidence_strength": strength,
        })
    return pd.DataFrame(rows)
