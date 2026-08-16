from __future__ import annotations

import math

import pandas as pd

LOOKBACKS = {
    "fast": ((20, 60), 20),
    "medium": ((60, 120), 60),
    "slow": ((120, 250), 120),
}


def build_cta_proxy(
    frame: pd.DataFrame,
    speed: str,
    annual_vol_target: float = 0.10,
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
