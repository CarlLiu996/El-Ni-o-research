from pathlib import Path

import pandas as pd

from elnino_cta import pipeline


def _fake_tushare_bundle(symbol: str):
    dates = pd.bdate_range("2025-01-02", periods=320)
    suffix = "ZCE" if symbol in {"SR", "CF", "OI", "RM"} else "DCE"
    contract = f"{symbol}2701.{suffix}"
    contracts = pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "contract": contract,
            "open": range(1000, 1320),
            "high": range(1001, 1321),
            "low": range(999, 1319),
            "close": range(1000, 1320),
            "settle": range(1000, 1320),
            "volume": 100,
            "open_interest": 200,
            "provider": "tushare_contract",
        }
    )
    mapping = pd.DataFrame(
        {
            "ts_code": f"{symbol}.{suffix}",
            "trade_date": dates,
            "mapping_ts_code": contract,
        }
    )
    metadata = pd.DataFrame(
        {
            "ts_code": [contract],
            "fut_code": [symbol],
            "list_date": [dates.min()],
            "delist_date": [dates.max()],
        }
    )
    return contracts, mapping, metadata


def test_refresh_futures_universe_writes_eight_contracts_and_six_chains(
    tmp_path: Path, monkeypatch
):
    def fake_fetch(symbol, start, end, universe_path):
        return _fake_tushare_bundle(symbol)

    monkeypatch.setattr(pipeline, "fetch_tushare_research_data", fake_fetch)

    manifest = pipeline.refresh_futures_universe(
        output_dir=tmp_path,
        start="2025-01-01",
        end="2026-08-29",
        universe_path=Path("config/commodity_universe.json"),
        provider="tushare",
    )

    assert list(manifest["datasets"]) == ["P", "SR", "CF", "C", "M", "Y", "OI", "RM"]
    for symbol in manifest["datasets"]:
        assert (tmp_path / "futures" / f"{symbol}_dominant_tushare.csv").exists()
        assert (tmp_path / "signals" / f"{symbol}_slow_proxy_research.csv").exists()

    chains = pd.read_csv(tmp_path / "signals" / "chain_confirmations.csv")
    assert len(chains) == 6
    assert chains.loc[chains["chain_id"].eq("soybean"), "symbols"].item() == "M+Y"
    assert chains.loc[chains["chain_id"].eq("rapeseed"), "symbols"].item() == "OI+RM"
