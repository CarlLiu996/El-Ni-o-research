"""Verify an official warehouse bulletin sequence, independently of weekdays.

The exchange's cumulative bulletin numbers certify published-issue continuity;
this does not invent holiday classifications or missing factory quantities.
"""
import re
from datetime import date
from .store import digest


def warehouse_sequence(store,start,end):
    start=date.fromisoformat(start).isoformat();end=date.fromisoformat(end).isoformat()
    if start>end:
        raise ValueError('unordered coverage window')
    docs=store.db.execute("""SELECT document_id,source_url,raw_path,sha256 FROM source_document
       WHERE source_id='SHFE_RECEIPTS' AND parse_status='parsed'
       QUALIFY row_number() OVER(PARTITION BY source_url ORDER BY fetched_at DESC)=1""").fetchall()
    records=[]
    for doc,url,path,sha in docs:
        match=re.search(r'/dailystock_(\d{4})(\d{2})(\d{2})/',url)
        if not match:
            continue
        day='-'.join(match.groups())
        if not start<=day<=end:
            continue
        body=(store.root/path).read_bytes()
        if digest(body)!=sha:
            raise ValueError('raw snapshot checksum mismatch')
        serial=re.search(r'总第\s*(\d+)\s*期',body.decode('utf-8'))
        records.append(dict(document_id=doc,date=day,serial=int(serial[1]) if serial else None))
    records.sort(key=lambda x:x['date'])
    numbers=[r['serial'] for r in records if r['serial'] is not None]
    missing=sorted(set(range(min(numbers),max(numbers)+1))-set(numbers)) if numbers else []
    chronological=all(b==a+1 for a,b in zip(numbers,numbers[1:]))
    boundaries=bool(records and records[0]['date']==start and records[-1]['date']==end)
    complete=bool(numbers and len(numbers)==len(records)==len(set(numbers)) and not missing and chronological and boundaries)
    return dict(start_date=start,end_date=end,documents=len(records),serials_found=len(numbers),
        first_serial=min(numbers) if numbers else None,last_serial=max(numbers) if numbers else None,
        missing_serials=missing,boundary_dates_present=boundaries,chronological=chronological,
        complete_published_bulletin_sequence=complete,exchange_holiday_classifications='not_inferred',
        factory_scope='only explicitly reported factory rows; absence is not zero',date_document_map=records)
