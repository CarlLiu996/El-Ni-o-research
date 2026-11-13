"""Evidence export with explicit freeze and conservative missing-source coverage."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .analysis import evidence_tag

SYMBOLS=['SR','CF','RU','NR','M','C','P','Y','OI','RM']


def validate(store):
    db=store.db
    coverage=db.execute('''SELECT source_id,commodity,metric_id,frequency,unit,
       min(observation_date) AS start_date,max(observation_date) AS end_date,
       count(*) AS n,count(DISTINCT observation_date) AS dates,
       count(published_at) AS known_release_dates FROM latest
       GROUP BY ALL ORDER BY source_id,commodity,metric_id''').df()
    failures=db.execute("SELECT source_id,source_url,parse_status,error_message FROM source_document WHERE parse_status='unverified'").df()
    hard_errors = db.execute("SELECT count(*) FROM latest WHERE NOT isfinite(value) OR unit='' OR source_url='' OR observation_date IS NULL").fetchone()[0]
    excluded = db.execute("SELECT data_status,count(*) AS n FROM observation WHERE data_status NOT IN ('verified','provisional') GROUP BY data_status").df().to_dict('records')
    return dict(qa_passed=hard_errors==0,hard_errors=hard_errors,excluded_observations=excluded,complete_9_symbols=False,future_data_leakage_check='enforced_at_report_freeze',
      source_missing=['CZCE','DCE','SDIC_verified_rates','industrial_P0_metrics'],
      unverified_metrics=['all spot/basis/inventory except MPOB and RU/NR receipts'],
      exceptions=failures.to_dict('records'),historical_series_available=coverage.to_dict('records'),
      margin_notice_checked_through=None,
      publication_warning='Unknown publication time is NULL; first seen is not a release timestamp.',
      calendar_warning='Coverage dates are actual official records; missing dates are never filled.')


def report(store, output, freeze, previous):
    freeze,previous=map(datetime.fromisoformat,[freeze,previous])
    if not freeze.tzinfo or not previous.tzinfo or previous>=freeze:
        raise ValueError('ordered timezone-aware cutoffs required')
    if freeze>datetime.now(timezone.utc):
        raise ValueError('future freeze')
    out=Path(output);(out/'charts').mkdir(parents=True,exist_ok=True)
    # As-of select BEFORE latest-vintage selection to prevent revision lookahead.
    df=store.db.execute('''SELECT * FROM history WHERE data_status IN ('verified','provisional') AND collected_at<=? AND
        (published_at IS NULL OR published_at<=?) AND observation_date<=CAST(? AS DATE)
        QUALIFY row_number() OVER(PARTITION BY series_key ORDER BY vintage DESC)=1''',
        [freeze,freeze,freeze]).df()
    covered={r[0] for r in store.db.execute('SELECT DISTINCT observation_id FROM reported_evidence WHERE report_date < ?',[freeze.date()]).fetchall()}
    groups=['metric_id','commodity','contract_code','price_type','unit','frequency']
    df=df.sort_values(groups+['observation_date'])
    df['prior_date']=df.groupby(groups,dropna=False)['observation_date'].shift()
    df['prior_value']=df.groupby(groups,dropna=False)['value'].shift()
    vintage_values={(key,v):(date,value) for key,v,date,value in store.db.execute(
        'SELECT series_key,vintage,observation_date,value FROM observation WHERE collected_at<=? AND (published_at IS NULL OR published_at<=?)',
        [freeze,freeze]).fetchall()}
    records=[]
    for r in df.to_dict('records'):
        tag=evidence_tag(None if pd.isna(r['published_at']) else r['published_at'],r['collected_at'],previous,freeze,r['observation_id'] in covered)
        old=vintage_values.get((r['series_key'],r['vintage']-1))
        if old is None and not pd.isna(r['prior_date']):
            old=(r['prior_date'],r['prior_value'])
        records.append(dict(report_date=freeze.date(),symbol=r['commodity'],metric_id=r['metric_id'],
          source_id=r['source_id'],source_url=r['source_url'],observation_date=r['observation_date'],
          published_at=r['published_at'],fetched_at=r['fetched_at'],observation_id=r['observation_id'],
          contract_code=r['contract_code'],price_type=r['price_type'],vintage=r['vintage'],
          old_observation_date=old[0] if old else None,old_value=old[1] if old else None,
          new_value=r['value'],unit=r['unit'],delta=r['value']-old[1] if old else None,
          evidence_tag=tag,already_reported=r['observation_id'] in covered,
          meaningful_change=bool(old and r['value']!=old[1]),verification_status=r['data_status'],
          quality_issues='unknown_release_time' if pd.isna(r['published_at']) else ''))
    ledger=pd.DataFrame(records)
    ledger.to_csv(out/'evidence_ledger.csv',index=False)
    qa=validate(store)
    qa['as_of_observations']=len(df)
    (out/'qa.json').write_text(json.dumps(qa,default=str,ensure_ascii=False,indent=2))
    coverage=[]
    for symbol in SYMBOLS:
        f=df[df.commodity==symbol]
        coverage.append(dict(symbol=symbol,status='partial' if len(f) else 'unverified',
          checked_metrics=','.join(sorted(set(f.metric_id))),latest_observation=str(f.observation_date.max()) if len(f) else None,
          latest_published_at=None,missing='spot, basis, industrial P0, company risk; see GAP_REGISTER.md'))
    pd.DataFrame(coverage).to_csv(out/'source_coverage.csv',index=False)
    # Diagnostic history charts, not claims of a new release or trading signal.
    import os
    cache=(store.root/'.cache').resolve()
    cache.mkdir(exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR',str(cache/'matplotlib'))
    os.environ.setdefault('XDG_CACHE_HOME',str(cache))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for metric in ['nino34_anomaly','mpob_stock']:
        f=df[(df.metric_id==metric)&(df.data_status.isin(['verified','provisional']))].sort_values('observation_date')
        if len(f)<30:
            continue
        fig,ax=plt.subplots(figsize=(10,4))
        # Reindex monthly, leaving visible gaps; no interpolation across missing months.
        series=f.set_index(pd.to_datetime(f.observation_date)).value
        series=series.reindex(pd.date_range(series.index.min(),series.index.max(),freq='ME'))
        ax.plot(series.index,series.values,lw=1)
        ax.set_title(f'{metric} | monthly | n={len(f)} | missing months={series.isna().sum()}')
        ax.set_ylabel(f.unit.iloc[0]);ax.grid(alpha=.2)
        fig.text(.01,.01,f'{f.source_url.iloc[-1]} | {f.source_flag.iloc[-1]}\n{f.observation_date.min().date()} to {f.observation_date.max().date()} | latest stored vintage | release time unknown',fontsize=7)
        fig.tight_layout(rect=(0,.09,1,1));fig.savefig(out/'charts'/f'{metric}.png',dpi=140);plt.close(fig)
    manifest=dict(freeze_at=freeze.isoformat(),previous_freeze=previous.isoformat(),purpose='independent_validation',
      database=str(store.root/'history.duckdb'),rows=len(df),publication_status='unknown unless recorded',
      unchanged_existing_0800_task=True,m1_complete=False,m2_complete=False)
    (out/'report_manifest.json').write_text(json.dumps(manifest,indent=2))
    (out/'report.md').write_text('# 独立数据验证报告\n\n数据取自官方文件；未知公布时间保留为空。首次发现不等于今日公布。\n\n'
      f'冻结时间：{freeze.isoformat()}；可见记录 {len(df)}。\n\n'
      '九品种覆盖及失败源见 source_coverage.csv / qa.json。图为历史库诊断图，不代表今日新增披露或评级变化。\n\n'
      '国投期货：尚未完成公告与费率基线核验，未能核实变动。\n\n'
      '原08:00任务保持不变；尚未完成5个境内交易日并行验证。\n')
    store.db.execute('BEGIN TRANSACTION')
    try:
        for r in records:
            store.db.execute('INSERT OR IGNORE INTO reported_evidence VALUES (?,?,?)',[freeze.date(),r['observation_id'],r['evidence_tag']])
        store.db.execute('COMMIT')
    except Exception:
        store.db.execute('ROLLBACK')
        raise
    return manifest
