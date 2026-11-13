"""Offline integration: reparse stored successful documents, require zero new vintages.
Run from project root: python scripts/replay.py --data data
"""
import argparse
import json
from pathlib import Path
from agri_cta.store import Store
from agri_cta import sources as s

p=argparse.ArgumentParser();p.add_argument('--data',default='data');p.add_argument('--year',type=int,default=2025);args=p.parse_args()
st=Store(args.data)
results=[]
for doc,source,url,path in st.db.execute("SELECT document_id,source_id,source_url,raw_path FROM source_document WHERE parse_status='parsed' ORDER BY fetched_at").fetchall():
    body=(Path(path) if Path(path).is_file() else st.root/path).read_bytes()
    if source=='MPOB':
        rows=s.mpob(body,int(url.split('val=')[1][:4]))
    elif source=='NOAA_ENSO':
        rows=s.noaa(body)
    elif source=='SHFE_ARCHIVE':
        rows=s.shfe_archive(body,args.year)
    elif source=='SHFE_RECEIPTS':
        d=url.split('dailystock_')[1][:8]
        rows=s.shfe_receipts(body,f'{d[:4]}-{d[4:6]}-{d[6:8]}')
    elif source=='SHFE':
        d=url.split('/kx')[1][:8]
        rows=s.shfe_day(body,f'{d[:4]}-{d[4:6]}-{d[6:8]}')
    else:
        continue
    added=st.ingest(doc,rows)
    results.append(dict(source=source,document_id=doc,rows=len(rows),added=added))
    print(json.dumps(results[-1]),flush=True)
    if added:
        raise AssertionError(f'replay created {added} rows')
st.export()
print(json.dumps(dict(replayed=len(results),added=0)))
