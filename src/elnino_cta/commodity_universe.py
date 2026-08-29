from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class CommodityContract:
    symbol: str
    name_cn: str
    chain_id: str
    chain_name_cn: str
    exchange: str
    continuous_code: str
    sina_code: str


def load_commodity_universe(path: Path) -> list[CommodityContract]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    chain_names = {
        str(item["chain_id"]): str(item["name_cn"])
        for item in payload.get("chains", [])
    }
    contracts: list[CommodityContract] = []
    seen: set[str] = set()
    for item in payload.get("contracts", []):
        symbol = str(item["symbol"]).upper()
        chain_id = str(item["chain_id"])
        if symbol in seen:
            raise ValueError(f"Duplicate commodity symbol: {symbol}")
        if chain_id not in chain_names:
            raise ValueError(f"Unknown chain_id for {symbol}: {chain_id}")
        seen.add(symbol)
        contracts.append(
            CommodityContract(
                symbol=symbol,
                name_cn=str(item["name_cn"]),
                chain_id=chain_id,
                chain_name_cn=chain_names[chain_id],
                exchange=str(item["exchange"]),
                continuous_code=str(item["continuous_code"]),
                sina_code=str(item["sina_code"]),
            )
        )
    if not contracts:
        raise ValueError("Commodity universe contains no contracts")
    return contracts


def _direction(value: object) -> int:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric) or numeric == 0:
        return 0
    return 1 if numeric > 0 else -1


def summarize_chain_confirmations(
    signals: pd.DataFrame,
    contracts: list[CommodityContract],
) -> pd.DataFrame:
    required = {"symbol", "medium_direction", "slow_direction"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Missing chain-confirmation columns: {sorted(missing)}")
    latest = signals.copy()
    latest["symbol"] = latest["symbol"].astype(str).str.upper()
    latest = latest.drop_duplicates("symbol", keep="last").set_index("symbol")

    chain_order = list(dict.fromkeys(contract.chain_id for contract in contracts))
    rows: list[dict[str, object]] = []
    for chain_id in chain_order:
        members = [contract for contract in contracts if contract.chain_id == chain_id]
        available = [contract for contract in members if contract.symbol in latest.index]
        statuses: list[str] = []
        directions: list[int] = []
        for contract in available:
            row = latest.loc[contract.symbol]
            medium = _direction(row["medium_direction"])
            slow = _direction(row["slow_direction"])
            if medium != slow:
                statuses.append("SPEED_DIVERGENCE")
            elif medium == 0:
                statuses.append("UNCONFIRMED")
            else:
                statuses.append("CONFIRMED")
                directions.append(medium)

        if len(available) < len(members):
            status = "INCOMPLETE"
        elif "SPEED_DIVERGENCE" in statuses:
            status = "SPEED_DIVERGENCE"
        elif "UNCONFIRMED" in statuses:
            status = "UNCONFIRMED"
        elif len(set(directions)) > 1:
            status = "CHAIN_DIVERGENCE"
        elif directions and directions[0] > 0:
            status = "LONG_CONFIRMED"
        elif directions:
            status = "SHORT_CONFIRMED"
        else:
            status = "UNCONFIRMED"

        rows.append(
            {
                "chain_id": chain_id,
                "chain_name_cn": members[0].chain_name_cn,
                "symbols": "+".join(contract.symbol for contract in members),
                "contract_count": len(members),
                "available_contracts": len(available),
                "status": status,
            }
        )
    return pd.DataFrame(rows)
