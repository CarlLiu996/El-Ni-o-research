"""Private immutable as-of DuckDB/Parquet, report history and native chart input.

This bridge never uploads private data, sends a report, approves a rating, or
backdates a late recovery into an earlier production freeze.
"""
import argparse
import csv
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from .continuity import archive_report, historical_state, init_archive
from .daily import GROUP, TZ, cutoffs, evidence, feature_status, init_state, load_products, risk_asof
from .store import Store, digest, now


def plain(value):
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    if value is None:return None
    if isinstance(value,float) and not math.isfinite(value):return None
    if pd.isna(value):return None
    if hasattr(value,'isoformat'):return value.isoformat()
    if hasattr(value,'item'):return value.item()
    return value


def write_json(path,value):
    path.write_text(json.dumps(plain(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def scheduled_phase(scheduled_for):
    stamp=datetime.fromisoformat(scheduled_for.replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('timezone-aware provider scheduled_for required')
    local=stamp.astimezone(TZ)
    if local.strftime('%H:%M:%S') not in ('07:00:00','08:00:00'):
        raise ValueError('unexpected scheduled phase; do not infer from delayed start time')
    return dict(report_date=local.date().isoformat(),scheduled_for=stamp.isoformat(),
        phase='prepare_data' if local.hour==7 else 'render_report')


def native_charts(frame,rows):
    charts=[]
    for r in rows:
        if not r['meaningful_change'] or r['symbol'] not in {*load_products(),'NR'}:
            continue
        if r['contract_code'] and not r['contract_code'].endswith(('2701','2705')):
            continue
        f=frame
        for col in GROUP:
            field='commodity' if col=='commodity' else col
            val=r['symbol'] if col=='commodity' else r[col]
            f=f[f[field]==val]
        f=f.sort_values('observation_date')
        if f.observation_date.duplicated().any():raise ValueError('conflicting chart dates')
        freq=r['frequency'];minimum={'daily':45,'weekly':12,'monthly':30}.get(freq,99999)
        if len(f)>=minimum:
            data=[dict(date=str(x.observation_date.date()),value=x.value) for x in f.itertuples()]
            if freq=='monthly':
                indexed=f.set_index('observation_date').value
                indexed=indexed.reindex(pd.date_range(indexed.index.min(),indexed.index.max(),freq='ME'))
                data=[dict(date=str(d.date()),value=None if pd.isna(v) else v) for d,v in indexed.items()]
            kind='line';sample='one_year_daily' if freq=='daily' and len(f)>=180 else 'actual_short_history'
        elif r['old_value'] is not None:
            data=[dict(date=r['old_observation_date'],value=r['old_value']),
                  dict(date=r['observation_date'],value=r['new_value'])]
            kind='two_point_comparison';sample='n=2; not a long historical trend'
        else:continue
        charts.append(dict(symbol=r['symbol'],metric_id=r['metric_id'],contract_code=r['contract_code'],
          kind=kind,frequency=freq,price_type=r['price_type'],unit=r['unit'],
          evidence_tag=r['evidence_tag'],data=data,n=len(f) if kind=='line' else 2,
          missing_points=sum(x['value'] is None for x in data),sample_description=sample,
          delta=r['delta'],percent_delta=r['percent_delta'],source_url=r['source_url'],
          published_at=r['published_at'],observation_id=r['observation_id'],
          note='Render inside the report; no separate PNG delivery. Warehouse changes are not proof of terminal demand.'))
    return charts


def export_handoff(store,report_date,freeze,previous,mode='parallel'):
    freeze=datetime.fromisoformat(freeze) if isinstance(freeze,str) else freeze
    previous=datetime.fromisoformat(previous) if isinstance(previous,str) else previous
    if not freeze.tzinfo or not previous.tzinfo or previous>=freeze or freeze>datetime.now(timezone.utc):
        raise ValueError('ordered past timezone-aware cutoffs required')
    if freeze.astimezone(TZ).date().isoformat()!=report_date:
        raise ValueError('report date differs from actual freeze')
    if mode=='production' and freeze.astimezone(TZ).strftime('%H:%M:%S')!='07:50:00':
        raise ValueError('production requires actual 07:50 cutoff')
    init_state(store);init_archive(store)
    rows,frame=evidence(store,freeze,previous)
    if frame.empty:raise ValueError('no verified actual observations at the requested freeze')
    continuity=historical_state(store,report_date,freeze)
    products=load_products();coverage=[]
    for symbol,config in products.items():
        for feature in config['p0']:
            coverage.append(dict(symbol=symbol,metric_id=feature,**feature_status(frame,feature,symbol,freeze)))
    # Verify all raw lineage used by the complete as-of database, not just the last point.
    documents=store.db.execute('SELECT * FROM source_document').df()
    documents=documents[documents.document_id.isin(set(frame.document_id))]
    for r in documents.itertuples():
        path=store.root/r.raw_path
        if not path.is_file() or digest(path.read_bytes())!=r.sha256:
            raise ValueError('raw snapshot missing or hash mismatch: '+r.document_id)
    charts=native_charts(frame,rows)
    overview=[]
    histories={r['symbol']:r for r in continuity['historical_ratings']}
    for symbol,config in products.items():
        overview.append(dict(symbol=symbol,account=config['account'],historical_view=histories[symbol],
          current_rating=None,current_rating_status='requires_current_research_review',
          missing_p0=[r['metric_id'] for r in coverage if r['symbol']==symbol and r['status']!='verified']))
    root=store.root/'handoff';root.mkdir(exist_ok=True)
    stage=root/('.building-'+uuid.uuid4().hex);stage.mkdir()
    payload=dict(schema_version=1,report_date=report_date,freeze_at=freeze.isoformat(),
        previous_freeze=previous.isoformat(),mode=mode,generated_at=now(),
        time_semantics='strict first-seen as-of; late recovery is never backdated',
        overview=overview,changed_evidence=[r for r in rows if r['meaningful_change']],
        source_coverage=coverage,continuity=continuity,risk=risk_asof(store,freeze),
        chart_data=charts,all_nine_product_p0_complete=all(r['status']=='verified' for r in coverage),
        delivery_status='not_attempted',replacement_ready=False)
    write_json(stage/'handoff.json',payload)
    write_json(stage/'continuity_state.json',continuity)
    write_json(stage/'rating_snapshot.json',continuity['historical_ratings'])
    write_json(stage/'chart_data.json',charts)
    if continuity['previous_report']:
        (stage/'previous_report.md').write_text(continuity['previous_report']['content_text'])
    if continuity['previous_generated_report']:
        (stage/'previous_generated_report.md').write_text(continuity['previous_generated_report']['content_text'])
    pd.DataFrame(rows).to_csv(stage/'evidence_ledger.csv',index=False)
    pd.DataFrame(coverage).to_csv(stage/'source_coverage.csv',index=False)
    # The physical freeze contains only observations visible at freeze, including lineage.
    frozen=duckdb.connect(str(stage/'frozen.duckdb'))
    try:
        frozen.register('_visible',frame)
        frozen.execute('CREATE TABLE observation_asof AS SELECT * FROM _visible')
        frozen.execute('CREATE VIEW latest AS SELECT * FROM observation_asof')
        frozen.register('_documents',documents)
        frozen.execute('CREATE TABLE source_document AS SELECT * FROM _documents')
        eligible=store.db.execute("SELECT * FROM report_snapshot WHERE report_date<? AND source_created_at<=?",[report_date,freeze]).df()
        frozen.register('_reports',eligible)
        frozen.execute('CREATE TABLE report_snapshot AS SELECT * FROM _reports')
        ratings=store.db.execute('SELECT r.* FROM rating_snapshot r JOIN report_snapshot s USING(snapshot_id) WHERE s.report_date<? AND s.source_created_at<=?',[report_date,freeze]).df()
        frozen.register('_ratings',ratings)
        frozen.execute('CREATE TABLE rating_snapshot AS SELECT * FROM _ratings')
        frozen.execute('CREATE TABLE freeze_metadata AS SELECT ?::TIMESTAMPTZ freeze_at, ?::VARCHAR report_date, ?::VARCHAR mode',[freeze,report_date,mode])
        target=str(stage/'observations_asof.parquet').replace("'","''")
        frozen.execute(f"COPY observation_asof TO '{target}' (FORMAT PARQUET)")
    finally:frozen.close()
    qa=dict(asof_observations=len(frame),raw_documents_hash_verified=len(documents),raw_hash_failures=[],
      future_data_leakage_check='filter before vintage selection; frozen database contains no later rows',
      previous_full_report_archived=continuity['previous_report'] is not None,
      historical_ratings_available=sum(x['last_reported_rating'] is not None for x in continuity['historical_ratings']),
      all_nine_products_listed=len(overview)==9,all_p0_verified=payload['all_nine_product_p0_complete'],
      last_exchange_trade_date=str(frame.loc[frame.source_id.isin(['SHFE','SHFE_ARCHIVE']), 'observation_date'].max().date()) if frame.source_id.isin(['SHFE','SHFE_ARCHIVE']).any() else None,
      source_missing=[x for x in coverage if x['status']!='verified'],
      current_ratings_approved=False,delivery_verified=False,mode=mode)
    write_json(stage/'qa.json',qa)
    hashes={p.name:digest(p.read_bytes()) for p in stage.iterdir() if p.is_file()}
    manifest=dict(schema_version=1,report_date=report_date,freeze_at=freeze.isoformat(),
        mode=mode,created_at=now(),files=hashes,qa=qa,
        implementation_sha256=digest(Path(__file__).read_bytes()))
    write_json(stage/'manifest.json',manifest)
    # Unique immutable bundle; latest pointer changes only after all files are complete.
    bundle=root/report_date/(freeze.astimezone(TZ).strftime('%H%M%S')+'-'+digest((stage/'manifest.json').read_bytes())[:12])
    bundle.parent.mkdir(exist_ok=True);stage.rename(bundle)
    pointer=dict(report_date=report_date,freeze_at=freeze.isoformat(),mode=mode,
      manifest_path=str((bundle/'manifest.json').relative_to(root)),
      manifest_sha256=digest((bundle/'manifest.json').read_bytes()))
    tmp=root/('.latest-'+uuid.uuid4().hex)
    write_json(tmp,pointer);os.replace(tmp,root/'latest.json')
    return dict(bundle=str(bundle),pointer=str(root/'latest.json'),qa=qa,charts=len(charts))


def read_handoff(data,report_date):
    root=(Path(data)/'handoff').resolve()
    pointer=json.loads((root/'latest.json').read_text())
    if pointer.get('report_date')!=report_date:raise ValueError('stale handoff date')
    path=(root/pointer['manifest_path']).resolve()
    if not path.is_relative_to(root):raise ValueError('handoff path escapes private root')
    if digest(path.read_bytes())!=pointer['manifest_sha256']:raise ValueError('manifest hash mismatch')
    manifest=json.loads(path.read_text())
    if manifest['report_date']!=report_date:raise ValueError('manifest date mismatch')
    for name,sha in manifest['files'].items():
        if Path(name).name!=name:raise ValueError('unsafe bundle member')
        file=(path.parent/name).resolve()
        if not file.is_relative_to(path.parent) or digest(file.read_bytes())!=sha:
            raise ValueError('bundle member missing or hash mismatch: '+name)
    payload=json.loads((path.parent/'handoff.json').read_text())
    if payload['report_date']!=report_date or payload['freeze_at']!=manifest['freeze_at']:
        raise ValueError('payload freeze/date mismatch')
    prior=payload['continuity']['previous_report']
    if prior and digest((path.parent/'previous_report.md').read_bytes())!=prior['content_sha256']:
        raise ValueError('previous report hash mismatch')
    db=duckdb.connect(str(path.parent/'frozen.duckdb'),read_only=True)
    try:
        n=db.execute('SELECT count(*) FROM observation_asof').fetchone()[0]
        leaks=db.execute('SELECT count(*) FROM observation_asof WHERE collected_at>? OR published_at>?',
            [payload['freeze_at'],payload['freeze_at']]).fetchone()[0]
        if n!=manifest['qa']['asof_observations'] or leaks:raise ValueError('frozen database as-of violation')
    finally:db.close()
    return dict(manifest_path=str(path),manifest=manifest,payload=payload)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['archive-report','prepare','verify','phase'])
    p.add_argument('--data',required=True)
    p.add_argument('--report-date')
    p.add_argument('--report-json')
    p.add_argument('--scheduled-for')
    p.add_argument('--mode',choices=['parallel','production'],default='production')
    p.add_argument('--refresh',action='store_true')
    args=p.parse_args()
    if args.command=='phase':
        if not args.scheduled_for:p.error('--scheduled-for required; do not guess from current time')
        print(json.dumps(scheduled_phase(args.scheduled_for),ensure_ascii=False))
        return
    day=args.report_date or datetime.now(TZ).date().isoformat()
    if args.command=='verify':
        result=read_handoff(args.data,day)
        print(json.dumps(dict(status='verified_actual_frozen_database_and_report_state',
          manifest=result['manifest_path'],qa=result['manifest']['qa']),ensure_ascii=False,default=str,indent=2))
        return
    if args.command=='prepare' and args.refresh:
        from .orchestrator import collect_daily
        collect_daily(args.data)
    store=Store(args.data)
    try:
        if args.command=='archive-report':
            if not args.report_json:p.error('--report-json required')
            result=archive_report(store,json.loads(Path(args.report_json).read_text()))
        else:
            freeze,previous=cutoffs(day,args.mode)
            result=export_handoff(store,day,freeze,previous,args.mode)
        store.export()
        print(json.dumps(result,ensure_ascii=False,default=str,indent=2))
    finally:store.db.close()


if __name__=='__main__':main()
