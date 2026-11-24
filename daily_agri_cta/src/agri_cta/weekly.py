"""Optional weekly price context from documented Sina fixed-contract daily bars.

Provider-original prices remain provisional and use separate sina_* metrics;
they never replace exchange observations or imply industry P0 completion.
Endpoint provenance: akfamily/akshare, futures/futures_zh_sina.py,
futures_zh_daily_sina (public source inspected 2026-11-24).
"""
import argparse
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .store import Store, row

BASE_URL = ('https://stock2.finance.sina.com.cn/futures/api/jsonp.php/'
            'var%20_V21052021_4_12=/InnerFuturesNewService.getDailyKLine')
SYMBOLS = ('SR', 'CF', 'RU', 'NR', 'M', 'C', 'P', 'Y', 'OI', 'RM')
# Explicit research contracts; no implicit continuous-series rollover.
DEFAULT_CONTRACTS = tuple(s + month for s in SYMBOLS for month in ('2701', '2705'))


def parse_bars(body, contract, end):
    """Fail closed on schema, duplicate dates, zeros, or inconsistent OHLC."""
    if not re.fullmatch(r'(SR|CF|RU|NR|M|C|P|Y|OI|RM)\d{4}', contract):
        raise ValueError('explicit supported fixed contract required')
    cutoff = date.fromisoformat(end)
    text = body.decode('utf-8-sig')
    match = re.fullmatch(r'\s*(?:/\*.*?\*/\s*)?var _V21052021_4_12=\((\[.*\])\);\s*', text, re.S)
    if not match:
        raise ValueError('unexpected daily-bar JSONP schema')
    records = json.loads(match.group(1))
    seen, rows = set(), []
    commodity = re.match(r'[A-Z]+', contract).group()
    for record in records:
        day = date.fromisoformat(record['d'])
        if day in seen:
            raise ValueError('duplicate daily coordinate')
        seen.add(day)
        if day > cutoff:
            continue  # Current/incomplete session is excluded, never relabeled.
        values = {key: float(record[key]) for key in ('o', 'h', 'l', 'c', 's', 'v', 'p')}
        if not all(math.isfinite(v) and v >= 0 for v in values.values()):
            raise ValueError('nonfinite or negative daily field')
        if min(values[k] for k in ('o', 'h', 'l', 'c', 's')) <= 0:
            raise ValueError('zero or missing daily price')
        if not values['l'] <= min(values['o'], values['c']) <= max(values['o'], values['c']) <= values['h']:
            raise ValueError('inconsistent OHLC bounds')
        for key, metric, kind, unit in (
            ('c', 'sina_futures_close', 'close', 'CNY/t'),
            ('s', 'sina_futures_settlement', 'settlement', 'CNY/t'),
            ('v', 'sina_futures_volume', 'non_price', 'contracts'),
            ('p', 'sina_futures_open_interest', 'non_price', 'contracts'),
        ):
            rows.append(row(metric, commodity, day.isoformat(), values[key], unit,
                            frequency='daily', contract=contract, price_type=kind,
                            published_at=None, data_status='provisional',
                            source_flag='provider_original_pending_exchange_crosscheck;fixed_contract_no_roll'))
    if len({r['observation_date'] for r in rows}) < 2:
        raise ValueError('insufficient completed daily history')
    return rows


def collect(store, end, contracts=DEFAULT_CONTRACTS):
    """One attempt per contract; source denials halt further requests."""
    result = []
    consecutive_unavailable = 0
    stopped = False
    for contract in contracts:
        url = BASE_URL + '?' + urlencode(dict(symbol=contract, type='2021_04_12'))
        if stopped:
            result.append(dict(contract=contract, status='skipped_source_unavailable'))
            continue
        doc = None
        try:
            doc, body = store.fetch('SINA_FIXED_CONTRACT', url)
            rows = parse_bars(body, contract, end)
            added = store.ingest(doc, rows)
            latest_day = max(r['observation_date'] for r in rows)
            result.append(dict(contract=contract, status='provider_provisional',
                               document_id=doc, added=added, latest_day=latest_day,
                               fresh_through_requested_day=latest_day == end))
            consecutive_unavailable = 0
        except Exception as exc:
            if doc:
                store.reject(doc, exc)
            attempt = store.db.execute('SELECT http_status FROM fetch_attempt WHERE source_url=? ORDER BY fetched_at DESC LIMIT 1', [url]).fetchone()
            status = attempt[0] if attempt else None
            consecutive_unavailable = consecutive_unavailable + 1 if status == 503 else 0
            stopped = status in (403, 412, 429) or consecutive_unavailable >= 3
            result.append(dict(contract=contract, status='unverified', http_status=status, error=str(exc)))
    store.export()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--end', required=True, help='Latest completed exchange trading date, YYYY-MM-DD')
    parser.add_argument('--contracts', nargs='+', default=DEFAULT_CONTRACTS)
    args = parser.parse_args()
    if date.fromisoformat(args.end) >= datetime.now(ZoneInfo('Asia/Shanghai')).date():
        parser.error('--end must precede the current Beijing date; incomplete sessions are excluded')
    store = Store(args.data)
    try:
        result = collect(store, args.end, args.contracts)
    finally:
        store.db.close()
    output = Path(args.data) / 'weekly_prices'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'latest.json').write_text(json.dumps(dict(
        requested_end=args.end, results=result, official_industry_coverage_unchanged=True,
        exchange_crosscheck_complete=False), ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
