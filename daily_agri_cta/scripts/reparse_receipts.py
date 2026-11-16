"""Repair failed HTTP-200 receipt parses from immutable raw snapshots, offline."""
import argparse
import json
from agri_cta.store import Store
from agri_cta.sources import shfe_receipts

p=argparse.ArgumentParser();p.add_argument('--data',default='data');args=p.parse_args()
st=Store(args.data);results=[]
for doc,url,path in st.db.execute("SELECT document_id,source_url,raw_path FROM source_document WHERE source_id='SHFE_RECEIPTS' AND http_status=200 AND parse_status='unverified' ORDER BY fetched_at").fetchall():
    d=url.split('dailystock_')[1][:8];day=f'{d[:4]}-{d[4:6]}-{d[6:8]}'
    try:
        rows=shfe_receipts((st.root/path).read_bytes(),day)
        n=st.ingest(doc,rows)
        r=dict(document_id=doc,date=day,status='reparsed',rows=len(rows),added=n)
    except Exception as e:
        st.reject(doc,e);r=dict(document_id=doc,date=day,status='unverified',error=str(e))
    results.append(r);print(json.dumps(r),flush=True)
st.export();print(json.dumps(dict(reparsed=sum(r['status']=='reparsed' for r in results),failed=sum(r['status']=='unverified' for r in results),added=sum(r.get('added',0) for r in results))))
