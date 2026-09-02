from __future__ import annotations

import warnings

import pandas as pd
import pytest

from elnino_cta.factor_mining import (
    evaluate_incremental_splits,
    evaluate_factor_splits,
    forward_compound_return,
    lag_monthly_feature,
    monthly_cta_frame,
    past_seasonal_zscore,
)


def test_forward_return_starts_after_decision_month() -> None:
    returns = pd.Series(
        [0.10, 0.20, -0.10, 0.05],
        index=pd.date_range("2020-01-31", periods=4, freq="ME"),
    )

    result = forward_compound_return(returns, horizon_months=2)

    assert result.iloc[0] == pytest.approx((1.20 * 0.90) - 1.0)
    assert result.iloc[1] == pytest.approx((0.90 * 1.05) - 1.0)
    assert pd.isna(result.iloc[2])
    assert pd.isna(result.iloc[3])


def test_monthly_feature_is_available_one_month_after_observation() -> None:
    observed = pd.Series(
        [100.0, 110.0, 120.0],
        index=pd.date_range("2020-01-31", periods=3, freq="ME"),
    )

    available = lag_monthly_feature(observed, lag_months=1)

    assert pd.isna(available.iloc[0])
    assert available.iloc[1] == 100.0
    assert available.iloc[2] == 110.0


def test_monthly_cta_frame_uses_month_end_state_and_compounds_daily_pnl() -> None:
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-03"]),
        "return": [0.01, 0.02, -0.01],
        "net_return": [0.009, 0.019, -0.011],
        "signal": [-1.0, 1.0, 1.0],
        "position": [-0.5, 0.5, 0.6],
        "realized_vol": [0.10, 0.11, 0.12],
    })

    result = monthly_cta_frame(daily)

    assert result.loc[pd.Timestamp("2020-01-31"), "price_return"] == pytest.approx(1.01 * 1.02 - 1.0)
    assert result.loc[pd.Timestamp("2020-01-31"), "cta_return"] == pytest.approx(1.009 * 1.019 - 1.0)
    assert result.loc[pd.Timestamp("2020-01-31"), "signal"] == 1.0
    assert result.loc[pd.Timestamp("2020-01-31"), "position"] == 0.5


def test_factor_direction_is_learned_on_train_and_not_flipped_in_test() -> None:
    dates = pd.to_datetime([
        "2018-12-31", "2019-12-31", "2020-06-30", "2020-12-31",
        "2021-06-30", "2022-06-30", "2024-06-30", "2025-06-30",
    ])
    factor = pd.Series([1, 2, 3, 4, 1, 2, 1, 2], index=dates, dtype=float)
    target = pd.Series([1, 2, 3, 4, 1, 2, -1, -2], index=dates, dtype=float)

    result = evaluate_factor_splits(
        factor,
        target,
        train_end="2020-12-31",
        validation_end="2023-12-31",
        min_observations=2,
    ).set_index("split")

    assert result.loc["train", "train_direction"] == 1.0
    assert result.loc["train", "oriented_ic"] == pytest.approx(1.0)
    assert result.loc["validation", "oriented_ic"] == pytest.approx(1.0)
    assert result.loc["test", "oriented_ic"] == pytest.approx(-1.0)


def test_constant_factor_returns_missing_ic_without_runtime_warning() -> None:
    dates = pd.date_range("2018-01-31", periods=8, freq="YE")
    factor = pd.Series(1.0, index=dates)
    target = pd.Series(range(8), index=dates, dtype=float)

    with warnings.catch_warnings(record=True) as caught:
        result = evaluate_factor_splits(
            factor,
            target,
            train_end="2020-12-31",
            validation_end="2023-12-31",
            min_observations=2,
        ).set_index("split")

    assert pd.isna(result.loc["train", "oriented_ic"])
    assert caught == []


def test_seasonal_zscore_uses_only_prior_values_from_same_calendar_month() -> None:
    dates = pd.to_datetime([
        "2017-01-31", "2018-01-31", "2019-01-31", "2020-01-31",
        "2017-02-28", "2018-02-28", "2019-02-28", "2020-02-29",
    ])
    values = pd.Series([100, 110, 120, 130, 200, 200, 200, 999], index=dates, dtype=float).sort_index()

    result = past_seasonal_zscore(values, lookback_years=5, min_periods=3)

    assert result.loc[pd.Timestamp("2020-01-31")] == pytest.approx(2.0)
    assert pd.isna(result.loc[pd.Timestamp("2019-01-31")])
    assert pd.isna(result.loc[pd.Timestamp("2020-02-29")])


def test_incremental_factor_direction_is_fixed_from_training_period() -> None:
    dates = pd.to_datetime([
        "2018-03-31", "2019-03-31", "2020-03-31",
        "2021-03-31", "2022-03-31", "2023-03-31",
        "2024-03-31", "2025-03-31", "2026-03-31",
    ])
    factor = pd.Series([1, 2, 3, 1, 2, 3, 1, 2, 3], index=dates, dtype=float)
    target = pd.Series([1, 2, 3, 1, 2, 3, -1, -2, -3], index=dates, dtype=float)
    controls = pd.DataFrame({"constant_control": 0.0}, index=dates)

    result = evaluate_incremental_splits(
        factor,
        target,
        controls,
        train_end="2020-12-31",
        validation_end="2023-12-31",
        min_observations=3,
    ).set_index("split")

    assert result.loc["train", "oriented_incremental_ic"] == pytest.approx(1.0)
    assert result.loc["validation", "oriented_incremental_ic"] == pytest.approx(1.0)
    assert result.loc["test", "oriented_incremental_ic"] == pytest.approx(-1.0)


def test_factor_identical_to_control_has_no_incremental_correlation() -> None:
    dates = pd.date_range("2015-01-31", periods=12, freq="YE")
    factor = pd.Series(range(12), index=dates, dtype=float)
    target = pd.Series(range(0, 24, 2), index=dates, dtype=float)
    controls = pd.DataFrame({"market": factor}, index=dates)

    result = evaluate_incremental_splits(
        factor,
        target,
        controls,
        train_end="2018-12-31",
        validation_end="2022-12-31",
        min_observations=3,
    )

    assert result["oriented_incremental_ic"].isna().all()
