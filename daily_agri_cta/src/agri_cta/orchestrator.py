"""Sequential single-writer daily collection with independent failure accounting."""
import json
import subprocess
import sys
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .store import now


def collect_daily(data,start=None,end=None):
    root=Path(data).resolve();root.mkdir(parents=True,exist_ok=True)
    local=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    start=start or (local-timedelta(days=7)).isoformat();end=end or (local-timedelta(days=1)).isoformat()
    run=root/'collection_runs'/datetime.now().strftime('%Y%m%dT%H%M%S%f');run.mkdir(parents=True)
    results=[]
    for source in ['mpob','noaa','noaa_psl','shfe']:
        command=[sys.executable,'-m','agri_cta.cli','collect','--data',str(root),'--source',source,'--start',start,'--end',end]
        timed_out=False
        with (run/(source+'.log')).open('w') as log:
            try:
                r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=900)
                code=r.returncode
            except subprocess.TimeoutExpired:code=-1;timed_out=True
        rows=[]
        for line in (run/(source+'.log')).read_text().splitlines():
            if line.startswith('{'):
                try:rows.append(json.loads(line))
                except ValueError:pass
        results.append(dict(source=source,exit_code=code,timed_out=timed_out,
          status='completed_with_gaps' if code or any(x.get('status')=='unverified' for x in rows) else 'parsed',
          results=rows,log=str(run/(source+'.log'))))
    command=[sys.executable,'-m','agri_cta.cli','watch-risk','--data',str(root),'--output',str(run/'risk')]
    with (run/'risk.log').open('w') as log:
        try:code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=900).returncode
        except subprocess.TimeoutExpired:code=-1
    file=run/'risk/risk_notice_candidates.json'
    risk=json.loads(file.read_text()) if file.exists() else []
    results.append(dict(source='sdic',exit_code=code,
      status='partial' if risk and not all(x.get('status')=='unverified' for x in risk) else 'unverified',results=risk))
    for source in ['CZCE','DCE','industrial_P0']:
        results.append(dict(source=source,status='not_connected_or_authorized_import_required'))
    manifest=dict(started_window=start,ended_window=end,completed_at=now(),sources=results,
                  all_nine_product_collection_complete=False)
    (run/'collection_run.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest
