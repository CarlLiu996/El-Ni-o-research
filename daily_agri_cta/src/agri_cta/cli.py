import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from .store import Store
from . import sources as s


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command',choices=['collect','backfill','validate','report','audit','watch-risk'])
    p.add_argument('--data',default='data')
    p.add_argument('--source',choices=['all','mpob','noaa','shfe','shfe_daily','receipts'],default='all')
    p.add_argument('--start',default='2025-01-01')
    p.add_argument('--end',default='2025-12-31')
    p.add_argument('--freeze',default=None,help='ISO timestamp with timezone; required for report')
    p.add_argument('--previous-freeze',default=None)
    p.add_argument('--output',default='reports/validation')
    args = p.parse_args()
    st = Store(args.data)
    if args.command=='watch-risk':
        from .risk import discover
        out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
        try:
            result=discover(st)
        except Exception as e:
            result=[dict(status='unverified',error=str(e))]
        (out/'risk_notice_candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        st.export()
        return
    if args.command in ['validate','report']:
        from .report import validate, report
        if args.command=='report':
            if not args.freeze or not args.previous_freeze:
                p.error('report requires --freeze and --previous-freeze')
            print(json.dumps(report(st,args.output,args.freeze,args.previous_freeze),indent=2,default=str))
        else:
            print(json.dumps(validate(st),indent=2,default=str))
        return
    if args.command=='audit':
        from .audit import audit
        audit(st,args.output)
        return
    result = []
    def run(*a):
        r = s.collect_one(st,*a)
        result.append(r)
        print(json.dumps(r),flush=True)
        (st.root/'last_run.json').write_text(json.dumps(result,indent=2))
    if args.source in ['all','mpob']:
        for year,url in s.MPOB.items():
            run('MPOB',url,s.mpob,year)
            time.sleep(.3)
    if args.source in ['all','noaa']:
        run('NOAA_ENSO',s.NOAA,s.noaa)
    if args.source in ['all','shfe']:
        # Annual archive supplies actual trading dates, including INE NR.
        index_url=f'{s.SHFE}/reports/tradedata/datadownload/download.json'
        _,body=st.fetch('SHFE_INDEX',index_url)
        index=json.loads(body)['data']
        for year in range(date.fromisoformat(args.start).year,date.fromisoformat(args.end).year+1):
            matches=[x for x in index['historicalData']+index['latestData'] if x['type']=='1' and x['year']==str(year)]
            if len(matches)!=1:
                raise ValueError(f'official annual archive missing/ambiguous: {year}')
            run('SHFE_ARCHIVE',s.SHFE+matches[0]['url'],s.shfe_archive,year)
        # Warehouse backfill is a separate operation; never infer it from price coverage.
    if args.source in ['shfe_daily','receipts']:
        start,end=map(date.fromisoformat,[args.start,args.end])
        if (end-start).days>370:
            raise ValueError('limit a daily request batch to 370 days')
        while start<=end:
            d=start.strftime('%Y%m%d');day=start.isoformat()
            # Calendar days are request candidates only: 404 is NOT classified as a holiday.
            if args.source=='shfe_daily':
                run('SHFE',f'{s.SHFE}/data/tradedata/future/dailydata/kx{d}.dat',s.shfe_day,day)
            else:
                run('SHFE_RECEIPTS',f'{s.SHFE}/data/tradedata/future/stockdata/dailystock_{d}/ZH/all.html',s.shfe_receipts,day)
            time.sleep(.5)
            start+=timedelta(days=1)
    st.export()


if __name__=='__main__':
    main()
