from __future__ import annotations

import math

import pandas as pd

LOOKBACKS = {
    "fast": ((20, 60), 20),
    "medium": ((60, 120), 60),
    "slow": ((120, 250), 120),
}


def build_roll_adjusted_dominant(
    mapping: pd.DataFrame,
    contracts: pd.DataFrame,
) -> pd.DataFrame:
    """Chain same-contract daily returns across dominant-contract rolls.

    On a roll day the return uses the new contract's own previous close, not
    the price ratio between two different contracts.  ``raw_close`` remains
    available for market-level reporting while ``close`` is the adjusted
    research series used by the CTA proxy.
    """
    required_mapping = {"trade_date", "mapping_ts_code"}
    required_contracts = {"date", "contract", "close"}
    if missing := required_mapping.difference(mapping.columns):
        raise ValueError(f"Missing dominant mapping columns: {sorted(missing)}")
    if missing := required_contracts.difference(contracts.columns):
        raise ValueError(f"Missing contract columns: {sorted(missing)}")

    selected = mapping.rename(
        columns={"trade_date": "date", "mapping_ts_code": "contract"}
    ).copy()
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce")
    quotes = contracts.copy()
    quotes["date"] = pd.to_datetime(quotes["date"], errors="coerce")
    quotes["close"] = pd.to_numeric(quotes["close"], errors="coerce")
    quotes = quotes.dropna(subset=["date", "contract", "close"]).sort_values(
        ["contract", "date"]
    )
    quotes["previous_contract_close"] = quotes.groupby("contract")["close"].shift(1)
    merged = selected.merge(quotes, on=["date", "contract"], how="left").sort_values(
        "date"
    ).reset_index(drop=True)
    merged = merged.rename(columns={"close": "raw_close"})
    merged["roll_flag"] = merged["contract"].ne(merged["contract"].shift(1))
    if not merged.empty:
        merged.loc[0, "roll_flag"] = False
    merged["contract_return"] = (
        merged["raw_close"] / merged["previous_contract_close"] - 1.0
    )
    if not merged.empty:
        merged.loc[0, "contract_return"] = 0.0
    usable_returns = merged["contract_return"].fillna(0.0)
    start = float(merged.loc[0, "raw_close"]) if not merged.empty else 1.0
    merged["close"] = start * (1.0 + usable_returns).cumprod()
    merged["price_series_method"] = "same_contract_return_chain"
    return merged


def build_cta_proxy(
    frame: pd.DataFrame,
    speed: str,
    annual_vol_target: float = 0.20,
    max_leverage: float = 2.0,
    cost_bps_per_turnover: float = 2.0,
) -> pd.DataFrame:
    """Build a lagged, volatility-scaled time-series momentum proxy.

    Position decided on t-1 is applied to close-to-close return on t. Costs are
    charged on absolute position change, preventing same-bar look-ahead.
    """
    if speed not in LOOKBACKS:
        raise ValueError(f"speed must be one of {sorted(LOOKBACKS)}")
    windows, vol_window = LOOKBACKS[speed]
    result = frame[["date", "close"]].sort_values("date").drop_duplicates("date").copy()
    result["return"] = result["close"].pct_change(fill_method=None)
    directions = [result["close"].pct_change(window, fill_method=None).apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0) for window in windows]
    result["signal"] = sum(directions) / len(directions)
    result["realized_vol"] = result["return"].rolling(vol_window).std() * math.sqrt(252)
    raw_position = result["signal"] * annual_vol_target / result["realized_vol"]
    result["target_position"] = raw_position.clip(-max_leverage, max_leverage)
    result["position"] = result["target_position"].shift(1).fillna(0.0)
    result["turnover"] = result["position"].diff().abs().fillna(result["position"].abs())
    result["gross_return"] = result["position"] * result["return"].fillna(0.0)
    result["cost"] = result["turnover"] * cost_bps_per_turnover / 10_000
    result["net_return"] = result["gross_return"] - result["cost"]
    result["equity"] = (1 + result["net_return"]).cumprod()
    result["speed"] = speed
    return result
