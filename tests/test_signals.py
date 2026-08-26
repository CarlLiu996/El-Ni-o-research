import pandas as pd

from elnino_cta.signals import build_cta_proxy, build_roll_adjusted_dominant


def test_signal_is_lagged_before_return_application():
    frame = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=300), "close": range(100, 400)})
    result = build_cta_proxy(frame, "fast", annual_vol_target=0.1)
    first_signal = result["target_position"].first_valid_index()
    assert first_signal is not None
    assert result.loc[first_signal, "position"] == 0
    assert result.loc[first_signal + 1, "position"] == result.loc[first_signal, "target_position"]


def test_cost_is_charged_on_turnover():
    frame = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=300), "close": range(100, 400)})
    result = build_cta_proxy(frame, "medium", cost_bps_per_turnover=5)
    assert (result["cost"] >= 0).all()
    assert result["cost"].sum() > 0


def test_default_target_volatility_is_twenty_percent():
    frame = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=300), "close": range(100, 400)})
    default = build_cta_proxy(frame, "medium")
    explicit = build_cta_proxy(frame, "medium", annual_vol_target=0.20)
    pd.testing.assert_series_equal(default["target_position"], explicit["target_position"])


def test_roll_adjusted_dominant_uses_new_contract_own_return_on_roll_day():
    dates = pd.to_datetime(["2026-08-11", "2026-08-12", "2026-08-13"])
    mapping = pd.DataFrame({
        "trade_date": dates,
        "mapping_ts_code": ["SR2609.ZCE", "SR2609.ZCE", "SR2701.ZCE"],
    })
    contracts = pd.DataFrame({
        "date": [dates[0], dates[1], dates[1], dates[2]],
        "contract": ["SR2609.ZCE", "SR2609.ZCE", "SR2701.ZCE", "SR2701.ZCE"],
        "close": [5000.0, 5100.0, 5300.0, 5406.0],
    })

    result = build_roll_adjusted_dominant(mapping, contracts)

    assert result.loc[2, "raw_close"] == 5406.0
    assert result.loc[2, "roll_flag"]
    assert result.loc[2, "contract_return"] == 5406.0 / 5300.0 - 1
    assert result.loc[2, "close"] == 5100.0 * (5406.0 / 5300.0)
