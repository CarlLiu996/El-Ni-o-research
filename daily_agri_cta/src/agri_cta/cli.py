import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from .store import Store
from . import sources as s


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command',choices=['collect','backfill','validate','report','audit','watch-risk','daily','delivery-status','delivery-probe'])
    p.add_argument('--mode',choices=['parallel','production'],default='parallel')
    p.add_argument('--report-date')
    p.add_argument('--refresh',action='store_true')
    p.add_argument('--run-id')
    p.add_argument('--data',default='data')
    p.add_argument('--source',choices=['all','mpob','noaa','shfe','shfe_daily','receipts','noaa_psl'],default='all')
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--resume',action='store_true',help='Backfill only: skip prior successful raw snapshots after hash verification; does not check for revisions')
    p.add_argument('--freeze',default=None,help='ISO timestamp with timezone; required for report')
    p.add_argument('--previous-freeze',default=None)
    p.add_argument('--output',default='reports/validation')
    args = p.parse_args()
    if args.command in ['collect','daily']:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        args.start=args.start or (today-timedelta(days=7)).isoformat()
        args.end=args.end or (today-timedelta(days=1)).isoformat()
    else:
        args.start=args.start or '2025-01-01'
        args.end=args.end or '2025-12-31'
    if date.fromisoformat(args.start)>date.fromisoformat(args.end):
        p.error('start must not be after end')
    if args.command=='daily' and args.refresh:
        from .orchestrator import collect_daily
        collect_daily(args.data,args.start,args.end)
    st = Store(args.data)
    if args.command=='daily':
        from .daily import daily_report,cutoffs
        default_freeze,default_previous=cutoffs(args.report_date,args.mode) if not args.freeze else (args.freeze,None)
        freeze=args.freeze or default_freeze
        previous=args.previous_freeze or default_previous
        if not previous:p.error('explicit freeze requires --previous-freeze')
        result=daily_report(st,args.output,freeze,previous,args.mode)
        st.export()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if args.command=='delivery-status':
        from .daily import init_state
        init_state(st)
        if not args.run_id:p.error('delivery-status requires --run-id')
        records=st.db.execute('SELECT status,receipt_id,error_message FROM delivery_attempt WHERE run_id=? ORDER BY attempted_at DESC',[args.run_id]).fetchall()
        print(json.dumps(dict(run_id=args.run_id,attempts=records,delivery_confirmed=any(r[0]=='delivered' for r in records)),indent=2))
        return
    if args.command=='delivery-probe':
        from .delivery import send_bundle
        import uuid
        result=send_bundle(st,dict(kind='delivery_smoke_test',run_id='probe-'+uuid.uuid4().hex,message='CTA日报投递链路测试；不是正式简报'),probe=True)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if args.command=='watch-risk':
        from .risk import discover
        out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
        try:
            result=discover(st)
        except Exception as e:
            result=[dict(status='unverified',error=str(e))]
        (out/'risk_notice_candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        from .risk import SYMBOLS
        coverage=[dict(symbol=symbol,checked_articles=sum(r['status']=='parsed_text_baseline_incomplete' for r in result),matched_facts=sum(1 for r in result for f in r.get('facts',[]) if f['contract_code'].startswith(symbol) and f['contract_code'][len(symbol):].isdigit()),required_contracts=['main','2701','expiry_special'],rate_baseline_status='unverified',complete=False) for symbol in SYMBOLS]
        (out/'risk_qa.json').write_text(json.dumps(dict(article_failures=[r for r in result if r['status']=='unverified'],coverage=coverage,official_release_time_precision='date_only',conclusion=('已核查公告正文；完整费率基线未核实，不能宣称全站无变化' if any(r['status']=='parsed_text_baseline_incomplete' for r in result) else '未能取得可核查公告正文；完整费率基线未核实')),ensure_ascii=False,indent=2))
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
    if args.resume and args.command!='backfill':
        p.error('--resume is only for historical backfill, not revision monitoring')
    def run(*a):
        existing=st.db.execute("SELECT document_id,raw_path,sha256 FROM source_document WHERE source_url=? AND parse_status='parsed' ORDER BY fetched_at DESC LIMIT 1",[a[1]]).fetchone() if args.resume else None
        if existing:
            from .store import digest
            raw=st.root/existing[1]
            if raw.is_file() and digest(raw.read_bytes())==existing[2]:
                r=dict(source=a[0],url=a[1],status='skipped_verified_snapshot',document_id=existing[0],added=0)
            else:
                r=s.collect_one(st,*a)
        else:
            r=s.collect_one(st,*a)
        result.append(r)
        print(json.dumps(r),flush=True)
        (st.root/'last_run.json').write_text(json.dumps(result,indent=2))
        if r.get('http_status') in [403,412,429]:
            st.export()
            raise RuntimeError('Batch stopped on access denial/rate limit; no bypass or immediate retry')
        if len(result)>=3 and all(x.get('http_status')==503 for x in result[-3:]):
            st.export()
            raise RuntimeError('Batch stopped after three consecutive HTTP 503 responses')
    if args.source in ['all','mpob']:
        for year,url in s.MPOB.items():
            if args.command=='collect' and year!=max(s.MPOB):
                continue
            run('MPOB',url,s.mpob,year)
            time.sleep(.3)
    if args.source in ['all','noaa']:
        run('NOAA_ENSO',s.NOAA,s.noaa)
    if args.source=='noaa_psl' or (args.source=='all' and args.command=='collect'):
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        for dataset,(doc_url,data_url,version,metric) in s.PSL_SERIES.items():
            doc,body=st.fetch('NOAA_PSL_DOC',doc_url)
            links={urljoin(doc_url,a['href']) for a in BeautifulSoup(body,'html.parser').select('a[href]')}
            if data_url not in links:
                st.reject(doc,'official PSL data link changed')
                st.export()
                raise ValueError('PSL documentation does not link to configured data URL')
            run('NOAA_PSL',data_url,s.noaa_psl,dataset)
    if args.source in ['all','shfe'] and args.command=='backfill':
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
    modes=['shfe_daily','receipts'] if args.command=='collect' and args.source in ['all','shfe'] else [args.source] if args.source in ['shfe_daily','receipts'] else []
    if modes:
        start,end=map(date.fromisoformat,[args.start,args.end])
        if (end-start).days>370:
            raise ValueError('limit a daily request batch to 370 days')
        while start<=end:
            d=start.strftime('%Y%m%d');day=start.isoformat()
            # Request candidates only: 404 is not an official holiday classification.
            for mode in modes:
                if mode=='shfe_daily':
                    run('SHFE',f'{s.SHFE}/data/tradedata/future/dailydata/kx{d}.dat',s.shfe_day,day)
                else:
                    run('SHFE_RECEIPTS',f'{s.SHFE}/data/tradedata/future/stockdata/dailystock_{d}/ZH/all.html',s.shfe_receipts,day)
                time.sleep(.5)
            start+=timedelta(days=1)
    st.export()


if __name__=='__main__':
    main()
