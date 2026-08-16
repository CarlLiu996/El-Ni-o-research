from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class FuturesSpec:
    symbol: str
    exchange: str
    continuous_code: str
    sina_code: str


SPECS = {
    "P": FuturesSpec("P", "DCE", "P.DCE", "P0"),
    "SR": FuturesSpec("SR", "CZCE", "SR.ZCE", "SR0"),
}


def _normalize(frame: pd.DataFrame, symbol: str, provider: str) -> pd.DataFrame:
    aliases = {"trade_date": "date", "vol": "volume", "hold": "open_interest", "oi": "open_interest"}
    frame = frame.rename(columns=aliases).copy()
    required = {"date", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing futures columns: {sorted(required - set(frame.columns))}")
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close", "settle", "volume", "open_interest"]:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = symbol
    frame["provider"] = provider
    columns = [
        "date", "symbol", "contract", "open", "high", "low", "close", "settle",
        "volume", "open_interest", "provider",
    ]
    if "contract" not in frame:
        frame["contract"] = pd.NA
    return frame[columns].sort_values("date")


def fetch_sina_continuous(symbol: str) -> pd.DataFrame:
    """Fetch a keyless continuous series via AkShare's Sina adapter.

    This source is suitable for pipeline smoke tests only: the vendor's roll and
    back-adjustment rules are not exposed, so research outputs must stay exploratory.
    """
    import akshare as ak

    spec = SPECS[symbol.upper()]
    return _normalize(ak.futures_zh_daily_sina(symbol=spec.sina_code), spec.symbol, "akshare_sina_continuous")


def _tushare_client():
    import tushare as ts

    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError(
            "TUSHARE_TOKEN is not configured. Set it to enable contract-level research data."
        )
    pro = ts.pro_api(token)
    http_url = os.getenv("TUSHARE_HTTP_URL")
    if http_url:
        pro._DataApi__http_url = http_url
    return pro


def _year_chunks(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start_year, end_year = int(start_date[:4]), int(end_date[:4])
    return [
        (max(start_date, f"{year}0101"), min(end_date, f"{year}1231"))
        for year in range(start_year, end_year + 1)
    ]


def _fetch_contract_daily(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    for attempt in range(6):
        try:
            daily = _tushare_client().fut_daily(
                ts_code=code, start_date=start_date, end_date=end_date
            )
            if not daily.empty:
                daily["contract"] = code
            time.sleep(0.75)
            return daily
        except Exception:
            if attempt == 5:
                raise
            time.sleep(min(10 * (attempt + 1), 30))
    return pd.DataFrame()


def fetch_tushare_research_data(
    symbol: str, start: str, end: str, max_workers: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch all listed monthly contracts, dominant mapping, and contract metadata.

    Mapping is requested one calendar year at a time to stay below row limits.
    Contract daily data is downloaded for every contract whose listed life overlaps
    the requested period, not only contracts that became dominant.
    """
    spec = SPECS[symbol.upper()]
    pro = _tushare_client()
    start_date, end_date = start.replace("-", ""), end.replace("-", "")

    mapping_parts = [
        pro.fut_mapping(ts_code=spec.continuous_code, start_date=chunk_start, end_date=chunk_end)
        for chunk_start, chunk_end in _year_chunks(start_date, end_date)
    ]
    mapping = pd.concat([part for part in mapping_parts if not part.empty], ignore_index=True)
    if mapping.empty:
        raise ValueError(f"Tushare returned no dominant-contract mapping for {spec.continuous_code}")
    mapping["trade_date"] = pd.to_datetime(mapping["trade_date"])
    mapping = mapping.drop_duplicates(["trade_date"], keep="first").sort_values("trade_date")

    metadata = pro.fut_basic(exchange=spec.exchange, fut_type="1", fut_code=spec.symbol)
    if metadata.empty:
        raise ValueError(f"Tushare returned no contract metadata for {spec.symbol}")
    metadata = metadata[metadata["fut_code"].astype(str).str.upper() == spec.symbol].copy()
    metadata["list_date"] = pd.to_datetime(metadata["list_date"], errors="coerce")
    metadata["delist_date"] = pd.to_datetime(metadata["delist_date"], errors="coerce")
    period_start, period_end = pd.Timestamp(start), pd.Timestamp(end)
    metadata = metadata[
        (metadata["list_date"].fillna(period_start) <= period_end)
        & (metadata["delist_date"].fillna(period_end) >= period_start)
    ].sort_values("list_date")

    requests: list[tuple[str, str, str]] = []
    for row in metadata.itertuples(index=False):
        contract_start = max(period_start, row.list_date if pd.notna(row.list_date) else period_start)
        contract_end = min(period_end, row.delist_date if pd.notna(row.delist_date) else period_end)
        requests.append((row.ts_code, contract_start.strftime("%Y%m%d"), contract_end.strftime("%Y%m%d")))

    pieces: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_contract_daily, code, contract_start, contract_end): code
            for code, contract_start, contract_end in requests
        }
        for future in as_completed(futures):
            daily = future.result()
            if not daily.empty:
                pieces.append(daily)
    if not pieces:
        raise ValueError(f"Tushare returned no contract data for {spec.symbol}")
    contracts = pd.concat(pieces, ignore_index=True)
    contracts = _normalize(contracts, spec.symbol, "tushare_contract")
    contracts = contracts.drop_duplicates(["date", "contract"], keep="first")
    return contracts, mapping, metadata.reset_index(drop=True)


def fetch_tushare_contracts(symbol: str, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backward-compatible wrapper returning contract daily data and mapping."""
    contracts, mapping, _ = fetch_tushare_research_data(symbol, start, end)
    return contracts, mapping
