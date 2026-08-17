import pandas as pd

from elnino_cta.signals import build_cta_proxy


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
