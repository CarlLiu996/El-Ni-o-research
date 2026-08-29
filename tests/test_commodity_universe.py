from pathlib import Path

import pandas as pd

from elnino_cta.commodity_universe import (
    load_commodity_universe,
    summarize_chain_confirmations,
)


UNIVERSE_PATH = Path("config/commodity_universe.json")


def test_configured_contracts_collapse_to_six_independent_crop_chains():
    contracts = load_commodity_universe(UNIVERSE_PATH)

    assert [contract.symbol for contract in contracts] == [
        "P", "SR", "CF", "C", "M", "Y", "OI", "RM"
    ]
    assert len({contract.chain_id for contract in contracts}) == 6


def test_chain_confirmation_does_not_double_count_oilseed_contracts():
    contracts = load_commodity_universe(UNIVERSE_PATH)
    signals = pd.DataFrame(
        [
            {"symbol": "P", "medium_direction": 1, "slow_direction": 1},
            {"symbol": "SR", "medium_direction": -1, "slow_direction": -1},
            {"symbol": "CF", "medium_direction": 1, "slow_direction": -1},
            {"symbol": "C", "medium_direction": 0, "slow_direction": 0},
            {"symbol": "M", "medium_direction": 1, "slow_direction": 1},
            {"symbol": "Y", "medium_direction": 1, "slow_direction": 1},
            {"symbol": "OI", "medium_direction": 1, "slow_direction": 1},
            {"symbol": "RM", "medium_direction": -1, "slow_direction": -1},
        ]
    )

    result = summarize_chain_confirmations(signals, contracts).set_index("chain_id")

    assert len(result) == 6
    assert result.loc["soybean", "status"] == "LONG_CONFIRMED"
    assert result.loc["rapeseed", "status"] == "CHAIN_DIVERGENCE"
    assert result.loc["cotton", "status"] == "SPEED_DIVERGENCE"
    assert result.loc["palm_oil", "contract_count"] == 1
    assert result.loc["soybean", "contract_count"] == 2


def test_missing_contract_signal_prevents_chain_confirmation():
    contracts = load_commodity_universe(UNIVERSE_PATH)
    signals = pd.DataFrame(
        [{"symbol": "M", "medium_direction": 1, "slow_direction": 1}]
    )

    result = summarize_chain_confirmations(signals, contracts).set_index("chain_id")

    assert result.loc["soybean", "status"] == "INCOMPLETE"
    assert result.loc["soybean", "available_contracts"] == 1
