from __future__ import annotations

import numpy as np
import pandas as pd


def _spearman(left: pd.Series, right: pd.Series) -> float:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < 2 or paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
        return float("nan")
    return paired.iloc[:, 0].rank().corr(paired.iloc[:, 1].rank())


def forward_compound_return(returns: pd.Series, horizon_months: int) -> pd.Series:
    """Compound returns from t+1 through t+h, excluding the decision month."""
    if horizon_months < 1:
        raise ValueError("horizon_months must be positive")
    future = pd.concat(
        [returns.shift(-step) for step in range(1, horizon_months + 1)],
        axis=1,
    )
    return future.add(1.0).prod(axis=1, min_count=horizon_months).sub(1.0)


def lag_monthly_feature(values: pd.Series, lag_months: int = 1) -> pd.Series:
    """Move an observation to its conservative month-level availability date."""
    if lag_months < 0:
        raise ValueError("lag_months must be non-negative")
    return values.shift(lag_months)


def past_seasonal_zscore(
    values: pd.Series,
    *,
    lookback_years: int = 5,
    min_periods: int = 3,
) -> pd.Series:
    """Standardize each month using only earlier observations from that month."""
    series = values.copy().sort_index()
    series.index = pd.to_datetime(series.index)
    result = pd.Series(float("nan"), index=series.index, dtype=float)
    for month in range(1, 13):
        same_month = series.loc[series.index.month == month]
        history = same_month.shift(1)
        mean = history.rolling(lookback_years, min_periods=min_periods).mean()
        std = history.rolling(lookback_years, min_periods=min_periods).std()
        result.loc[same_month.index] = (same_month - mean) / std.mask(std == 0)
    return result.sort_index()


def monthly_cta_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a daily CTA path to month returns and observable month-end state."""
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").set_index("date")

    returns = frame[["return", "net_return"]].add(1.0).resample("ME").prod().sub(1.0)
    returns = returns.rename(columns={"return": "price_return", "net_return": "cta_return"})
    state = frame[["signal", "position", "realized_vol"]].resample("ME").last()
    return returns.join(state)


def evaluate_factor_splits(
    factor: pd.Series,
    target: pd.Series,
    *,
    train_end: str = "2020-12-31",
    validation_end: str = "2023-12-31",
    min_observations: int = 12,
) -> pd.DataFrame:
    """Evaluate one time-series factor using a direction fixed on training data."""
    aligned = pd.concat(
        [factor.rename("factor"), target.rename("target")], axis=1
    ).dropna()
    aligned.index = pd.to_datetime(aligned.index)
    aligned = aligned.sort_index()

    train_end_ts = pd.Timestamp(train_end)
    validation_end_ts = pd.Timestamp(validation_end)
    masks = {
        "train": aligned.index <= train_end_ts,
        "validation": (aligned.index > train_end_ts) & (aligned.index <= validation_end_ts),
        "test": aligned.index > validation_end_ts,
    }
    train = aligned.loc[masks["train"]]
    train_ic = _spearman(train["factor"], train["target"])
    direction = 1.0 if pd.isna(train_ic) or train_ic >= 0 else -1.0

    oriented_train = train["factor"] * direction
    low_cut = oriented_train.quantile(1.0 / 3.0) if not train.empty else float("nan")
    high_cut = oriented_train.quantile(2.0 / 3.0) if not train.empty else float("nan")

    rows: list[dict[str, float | int | str]] = []
    for split, mask in masks.items():
        sample = aligned.loc[mask].copy()
        raw_ic = _spearman(sample["factor"], sample["target"])
        if len(sample) < min_observations:
            raw_ic = float("nan")
        oriented = sample["factor"] * direction
        top = sample.loc[oriented >= high_cut, "target"]
        bottom = sample.loc[oriented <= low_cut, "target"]
        spread = top.mean() - bottom.mean() if len(top) and len(bottom) else float("nan")
        state = pd.Series(0.0, index=sample.index)
        state.loc[oriented >= high_cut] = 1.0
        state.loc[oriented <= low_cut] = -1.0
        rows.append({
            "split": split,
            "n": len(sample),
            "train_direction": direction,
            "raw_ic": raw_ic,
            "oriented_ic": raw_ic * direction,
            "quantile_spread": spread,
            "top_n": len(top),
            "bottom_n": len(bottom),
            "turnover": state.diff().abs().dropna().mean(),
        })
    return pd.DataFrame(rows)


def _partial_spearman(frame: pd.DataFrame) -> float:
    ranked = frame.rank().dropna()
    if len(ranked) < 3:
        return float("nan")
    controls = ranked.drop(columns=["factor", "target"])
    controls = controls.loc[:, controls.nunique() > 1]
    design = np.column_stack([np.ones(len(ranked)), controls.to_numpy(dtype=float)])
    factor_values = ranked["factor"].to_numpy(dtype=float)
    target_values = ranked["target"].to_numpy(dtype=float)
    factor_residual = factor_values - design @ np.linalg.lstsq(
        design, factor_values, rcond=None
    )[0]
    target_residual = target_values - design @ np.linalg.lstsq(
        design, target_values, rcond=None
    )[0]
    if np.std(factor_residual) < 1e-12 or np.std(target_residual) < 1e-12:
        return float("nan")
    return float(np.corrcoef(factor_residual, target_residual)[0, 1])


def evaluate_incremental_splits(
    factor: pd.Series,
    target: pd.Series,
    controls: pd.DataFrame,
    *,
    train_end: str = "2020-12-31",
    validation_end: str = "2023-12-31",
    min_observations: int = 12,
) -> pd.DataFrame:
    """Partial rank correlation after market controls, oriented on training only."""
    aligned = pd.concat(
        [factor.rename("factor"), target.rename("target"), controls], axis=1
    ).dropna()
    aligned.index = pd.to_datetime(aligned.index)
    aligned = aligned.sort_index()
    train_end_ts = pd.Timestamp(train_end)
    validation_end_ts = pd.Timestamp(validation_end)
    masks = {
        "train": aligned.index <= train_end_ts,
        "validation": (aligned.index > train_end_ts) & (aligned.index <= validation_end_ts),
        "test": aligned.index > validation_end_ts,
    }
    train_ic = _partial_spearman(aligned.loc[masks["train"]])
    direction = 1.0 if pd.isna(train_ic) or train_ic >= 0 else -1.0
    rows: list[dict[str, float | int | str]] = []
    for split, mask in masks.items():
        sample = aligned.loc[mask]
        raw_ic = _partial_spearman(sample) if len(sample) >= min_observations else float("nan")
        rows.append({
            "split": split,
            "n": len(sample),
            "train_direction": direction,
            "raw_incremental_ic": raw_ic,
            "oriented_incremental_ic": raw_ic * direction,
        })
    return pd.DataFrame(rows)
