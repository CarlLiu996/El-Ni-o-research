"""Daily as-of evidence bundles. Report generation never confirms user delivery."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from .analysis import evidence_tag
from .store import digest, now

TZ=ZoneInfo('Asia/Shanghai')
GROUP=['metric_id','commodity','contract_code','price_type','frequency','unit','source_id']
COLUMNS='report_date symbol metric_id source_id source_url document_id sha256 observation_id observation_date published_at first_seen_at fetched_at contract_code price_type frequency unit new_value old_value old_observation_date delta percent_delta comparison_basis vintage source_flag evidence_tag newly_visible already_in_previous_bundle meaningful_change verification_status quality_issues age_days'.split()


def init_state(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS daily_run (
      run_id VARCHAR PRIMARY KEY,report_date DATE,freeze_at TIMESTAMPTZ,previous_freeze TIMESTAMPTZ,
      mode VARCHAR,generated_at TIMESTAMPTZ,bundle_path VARCHAR,content_sha256 VARCHAR);
      CREATE TABLE IF NOT EXISTS daily_evidence (
      run_id VARCHAR,observation_id VARCHAR,evidence_tag VARCHAR,PRIMARY KEY(run_id,observation_id));
      CREATE TABLE IF NOT EXISTS delivery_attempt (
      attempt_id VARCHAR PRIMARY KEY,run_id VARCHAR,channel VARCHAR,attempted_at TIMESTAMPTZ,
      status VARCHAR,receipt_id VARCHAR,response_sha256 VARCHAR,error_message VARCHAR);
      CREATE TABLE IF NOT EXISTS operational_check (
      kind VARCHAR,checked_at TIMESTAMPTZ,status VARCHAR,evidence_ref VARCHAR,evidence_sha256 VARCHAR);
      CREATE TABLE IF NOT EXISTS parallel_review (
      trade_date DATE PRIMARY KEY,run_id VARCHAR,baseline_ref VARCHAR,baseline_sha256 VARCHAR,
      reviewed_at TIMESTAMPTZ,reviewer VARCHAR,status VARCHAR,findings_json VARCHAR);
    ''')


def cutoffs(report_date=None,mode='parallel',at=None):
    at=at or datetime.now(timezone.utc)
    if at.tzinfo is None:raise ValueError('timezone-aware clock required')
    day=date.fromisoformat(report_date) if report_date else at.astimezone(TZ).date()
    target=datetime.combine(day,time(7,50),TZ)
    freeze=at if mode=='parallel' and day==at.astimezone(TZ).date() else target
    if freeze>at:raise ValueError('07:50 freeze has not occurred yet')
    return freeze.isoformat(),(target-timedelta(days=1)).isoformat()


def visible(store,cutoff):
    return store.db.execute('''SELECT * FROM history WHERE data_status IN ('verified','provisional')
      AND collected_at<=? AND (published_at IS NULL OR published_at<=?)
      AND observation_date<=? QUALIFY row_number() OVER(PARTITION BY series_key ORDER BY vintage DESC)=1''',
      [cutoff,cutoff,cutoff.astimezone(TZ).date()]).df()


def canonical(metric):
    return metric.split('__',1)[1] if metric.startswith('VENDOR_') and '__' in metric else metric


def age_limit(frequency):
    return {'daily':4,'weekly':21,'monthly':62,'annual':400}.get(frequency,0)


def material(metric,delta,pct,old):
    if delta is None or delta==0:return False
    metric=canonical(metric)
    if metric.startswith('warehouse'):
        return abs(delta)>=100 or (pct is not None and abs(pct)>=1)
    if metric in ['futures_open','futures_high','futures_low']:return False
    if metric in ['futures_close','futures_settlement']:
        return pct is not None and abs(pct)>=.5
    if metric in ['futures_volume','futures_open_interest']:
        return old is not None and old['value']>=100 and pct is not None and abs(pct)>=5
    return True


def evidence(store,freeze,previous):
    current=visible(store,freeze).sort_values(GROUP+['observation_date'])
    prior=visible(store,previous)
    prior_keys={r['series_key']:r for r in prior.to_dict('records')}
    generated={r[0] for r in store.db.execute('''SELECT DISTINCT e.observation_id
       FROM daily_evidence e JOIN daily_run r USING(run_id) WHERE r.freeze_at<=?''',[previous]).fetchall()}
    rows=[]
    for _,group in current.groupby(GROUP,dropna=False,sort=False):
        entries=group.to_dict('records')
        for i,r in enumerate(entries):
            old=prior_keys.get(r['series_key'])
            revision=bool(old and old['observation_id']!=r['observation_id'])
            if i!=len(entries)-1 and not revision:continue
            if not revision:old=entries[i-1] if i else None
            first=r['collected_at']
            published=None if pd.isna(r['published_at']) else r['published_at']
            newly_visible=previous<first<=freeze or (published is not None and previous<published<=freeze)
            already=r['observation_id'] in generated
            tag=evidence_tag(published,first,previous,freeze,already)
            age=(freeze.astimezone(TZ).date()-r['observation_date'].date()).days
            stale=age>age_limit(r['frequency'])
            delta=r['value']-old['value'] if old else None
            pct=delta/abs(old['value'])*100 if old and old['value']!=0 else None
            changed=bool(newly_visible and not already and not stale and material(r['metric_id'],delta,pct,old))
            issues=[]
            if published is None:issues.append('unknown_release_time_first_seen_is_not_publication')
            if stale:issues.append('stale_observation')
            if not old:issues.append('no_comparable_prior_observation')
            rows.append(dict(report_date=freeze.astimezone(TZ).date().isoformat(),symbol=r['commodity'],
              metric_id=r['metric_id'],source_id=r['source_id'],source_url=r['source_url'],
              document_id=r['document_id'],sha256=r['sha256'],observation_id=r['observation_id'],
              observation_date=r['observation_date'].date().isoformat(),published_at=published,
              first_seen_at=first,fetched_at=r['fetched_at'],contract_code=r['contract_code'],price_type=r['price_type'],
              frequency=r['frequency'],unit=r['unit'],new_value=r['value'],old_value=old['value'] if old else None,
              old_observation_date=old['observation_date'].date().isoformat() if old else None,
              delta=delta,percent_delta=pct,
              comparison_basis='prior_vintage_same_period' if revision else 'previous_observation_visible_at_current_freeze',
              vintage=r['vintage'],source_flag=r['source_flag'],evidence_tag=tag,newly_visible=newly_visible,
              already_in_previous_bundle=already,meaningful_change=changed,
              verification_status=r['data_status'],quality_issues=';'.join(issues),age_days=age))
    return rows,current


def lookup(frame,metric,symbol,contract=None):
    f=frame[(frame.commodity==symbol)&frame.metric_id.map(lambda x:canonical(x)==metric)]
    if contract is not None:f=f[f.contract_code==contract]
    if f.empty:return None
    f=f[f.observation_date==f.observation_date.max()]
    if f.unit.nunique()!=1 or f.price_type.nunique()!=1 or f.value.nunique()!=1:return None
    return f.iloc[0].to_dict()


def feature_status(frame,feature,symbol,freeze):
    aliases={'MPOB_monthly_production':('mpob_production','P'),'MPOB_monthly_exports':('mpob_exports','P'),
             'MPOB_month_end_stock':('mpob_stock','P')}
    if feature in aliases:
        metric,product=aliases[feature];legs=[lookup(frame,metric,product)]
    elif feature=='RU_NR_receipts':
        legs=[lookup(frame,'warehouse_receipts_warehouse',x) for x in ['RU','NR']]+[lookup(frame,'warehouse_receipts_factory','NR')]
    elif feature=='RU_NR_same_date_relative_price':
        legs=[lookup(frame,'futures_settlement',x,x+'2701') for x in ['RU','NR']]
    elif 'receipts' in feature.lower():
        legs=[lookup(frame,'warehouse_receipts_warehouse',symbol)]
    elif '_spread' in feature and '2701' in feature and '2705' in feature:
        legs=[lookup(frame,'futures_settlement',symbol,symbol+m) for m in ['2701','2705']]
    elif feature in ['Y2701_minus_P2701','OI2701_minus_Y2701','OI2701_minus_P2701','M2701_minus_RM2701']:
        a,b=feature.split('_minus_');legs=[lookup(frame,'futures_settlement',x[:-4],x) for x in [a,b]]
    elif 'basis' in feature.lower():
        return dict(status='unverified',reason='same_day_contract_location_quality_legs_not_verified',dates=[])
    else:legs=[lookup(frame,feature,symbol)]
    if any(x is None for x in legs):return dict(status='unverified',reason='not_obtained_or_conflicting_sources',dates=[])
    if any((freeze.astimezone(TZ).date()-x['observation_date'].date()).days>age_limit(x['frequency']) for x in legs):
        return dict(status='stale',reason='last_observation_too_old',dates=[str(x['observation_date'].date()) for x in legs])
    if len(legs)>1 and (len({x['observation_date'] for x in legs})!=1 or len({x['unit'] for x in legs})!=1 or len({x['price_type'] for x in legs})!=1):
        return dict(status='unverified',reason='incompatible_spread_legs',dates=[])
    return dict(status='verified',reason='explicit_source_observations',dates=[str(x['observation_date'].date()) for x in legs],
                observation_ids=[x['observation_id'] for x in legs])


def load_products():
    return yaml.safe_load((Path(__file__).resolve().parents[2]/'config/metrics.yaml').read_text())['products']


def risk_asof(store,freeze):
    if not store.db.execute("SELECT 1 FROM information_schema.tables WHERE table_name='risk_fact'").fetchone():
        return dict(status='unverified',reason='no_checked_notice_facts',facts=[],baseline_verified=False)
    raw=store.db.execute("SELECT * FROM risk_fact WHERE validation_status='accepted_text_fact' AND first_seen<=? AND published_date<=?",
                         [freeze,freeze.astimezone(TZ).date()]).df()
    facts=[]
    for r in raw.to_dict('records'):
        facts.append(dict(published_date=str(r['published_date'].date()),applicable_trade_date=str(r['applicable_trade_date'].date()),
                          source_url=r['source_url'],document_id=r['document_id'],**json.loads(r['fact_json'])))
    import re
    titles=store.db.execute("""SELECT n.title FROM notice_seen n JOIN source_document d USING(document_id)
       WHERE n.first_seen<=? AND n.status='parsed_text_baseline_incomplete' AND d.parse_status='parsed'""",[freeze]).fetchall()
    dates=[]
    for (title,) in titles:
        m=re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日交易提示',title)
        if m:dates.append(f'{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}')
    latest=max(dates,default=None)
    stale=latest is None or latest<freeze.astimezone(TZ).date().isoformat()
    return dict(status='unverified' if stale else 'partial',latest_applicable_trade_date=latest,
                reason='latest_notice_date_not_confirmed' if stale else 'text_facts_only_customer_rate_baseline_incomplete',
                facts=facts,baseline_verified=False)


def draw_changed(current,rows,out):
    import os
    os.environ.setdefault('MPLCONFIGDIR',str((out/'.cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    charts=[]
    for r in rows:
        if not r['meaningful_change']:continue
        f=current
        for key,value in [('metric_id',r['metric_id']),('commodity',r['symbol']),('contract_code',r['contract_code']),
                          ('price_type',r['price_type']),('unit',r['unit']),('source_id',r['source_id'])]:
            f=f[f[key]==value]
        f=f.sort_values('observation_date')
        minimum=180 if r['frequency']=='daily' else 30 if r['frequency']=='monthly' else 104
        if len(f)<minimum:continue
        name=digest((r['metric_id']+r['symbol']+r['contract_code']).encode())[:12]+'.png'
        fig,ax=plt.subplots(figsize=(10,4))
        if r['frequency']=='monthly':
            series=f.set_index(pd.to_datetime(f.observation_date)).value
            series=series.reindex(pd.date_range(series.index.min(),series.index.max(),freq='ME'))
            ax.plot(series.index,series.values)
        else:ax.plot(f.observation_date,f.value)
        ax.margins(y=.12);ax.scatter(f.observation_date.iloc[-1],f.value.iloc[-1],s=18)
        ax.set_title(f"{r['symbol']} {r['metric_id']} {r['contract_code']} | {r['frequency']} | n={len(f)}")
        ax.set_ylabel(r['unit']);ax.grid(alpha=.2)
        fig.text(.01,.01,f"{r['source_url']}\n{f.observation_date.min().date()} to {f.observation_date.max().date()} | {r['source_flag']} | release time unknown unless stated",fontsize=7)
        fig.tight_layout(rect=(0,.09,1,1));fig.savefig(out/'charts'/name,dpi=140);plt.close(fig)
        charts.append(dict(file='charts/'+name,observation_id=r['observation_id'],n=len(f),metric_id=r['metric_id'],symbol=r['symbol']))
    return charts


def daily_report(store,output,freeze,previous,mode='parallel'):
    freeze=datetime.fromisoformat(freeze);previous=datetime.fromisoformat(previous)
    if not freeze.tzinfo or not previous.tzinfo or not previous<freeze<=datetime.now(timezone.utc):
        raise ValueError('ordered past timezone-aware cutoffs required')
    local=freeze.astimezone(TZ)
    if mode=='production' and ((local.hour,local.minute,local.second)!=(7,50,0) or local.microsecond):
        raise ValueError('production freeze must be exactly 07:50 Asia/Shanghai')
    if mode not in ['parallel','production']:raise ValueError('unsupported report mode')
    init_state(store);out=Path(output);(out/'charts').mkdir(parents=True,exist_ok=True)
    for p in (out/'charts').glob('*.png'):p.unlink()
    rows,frame=evidence(store,freeze,previous);products=load_products()
    p0=[];overview=[]
    for symbol,config in products.items():
        for feature in config['p0']:
            p0.append(dict(symbol=symbol,metric_id=feature,**feature_status(frame,feature,symbol,freeze)))
        missing=[r['metric_id'] for r in p0 if r['symbol']==symbol and r['status']!='verified']
        changes=[r for r in rows if r['symbol'] in ([symbol,'NR'] if symbol=='RU' else [symbol]) and r['meaningful_change']]
        f=frame[frame.commodity.isin([symbol,'NR'] if symbol=='RU' else [symbol])]
        overview.append(dict(symbol=symbol,account_tag=config['account'],last_known_stage=config['stage'],
            old_rating=config['baseline_rating'],new_rating=None,rating_status='historical_baseline_not_reviewed',changed_rating=False,
            newly_disclosed_evidence=len(changes),valuation_view='仅做方向判断；未形成可复核估值',valuation_confidence='insufficient',
            safety_margin='unverified',concentrated_setup=False,
            source_cutoff=str(f.observation_date.max().date()) if len(f) else None,missing_data=missing))
    risk=risk_asof(store,freeze);charts=draw_changed(frame,rows,out)
    run_id=digest((mode+freeze.isoformat()+previous.isoformat()).encode())[:24]
    from .readiness import assess
    readiness=assess(store,p0,overview,risk,mode,freeze)
    pd.DataFrame(rows,columns=COLUMNS).to_csv(out/'evidence_ledger.csv',index=False)
    pd.DataFrame(p0).to_csv(out/'source_coverage.csv',index=False)
    pd.DataFrame(overview).to_csv(out/'overview.csv',index=False)
    (out/'risk_ledger.json').write_text(json.dumps(risk,ensure_ascii=False,indent=2))
    qa=dict(complete_9_symbols=len(overview)==9,all_p0_verified=all(x['status']=='verified' for x in p0),
        future_data_leakage_check='asof_before_vintage_selection',source_missing=[x for x in p0 if x['status']!='verified'],
        unverified_metrics=[x['metric_id'] for x in p0 if x['status']!='verified'],historical_series_available=charts,
        margin_notice_checked_through=risk.get('latest_applicable_trade_date'),exceptions=[],replacement_readiness=readiness)
    (out/'qa.json').write_text(json.dumps(qa,ensure_ascii=False,default=str,indent=2))
    lines=[f'# 九品种CTA并行日报｜{local.date()}','',f'实际冻结：{local.isoformat()}；模式：{mode}。本报告尚不能替代原08:00简报。','',
      '|品种|新增有效变化|历史评级（未复核）|当前评级|缺少P0指标|','|---|---:|---|---|---:|']
    for r in overview:lines.append(f"|{r['symbol']}|{r['newly_disclosed_evidence']}|{r['old_rating']}|未形成新评级|{len(r['missing_data'])}|")
    lines+=['','本次不输出★★★★集中表达建议；历史评级基线不能当作今天重新确认。','','## 本次有效变化']
    changes=[r for r in rows if r['meaningful_change'] and (not r['contract_code'] or r['contract_code'].endswith(('2701','2705')))]
    if not changes:lines+=['本次未取得可核实的关键新增，不代表客观市场没有变化。']
    for r in changes:
        lines+=[f"- {r['symbol']} {r['metric_id']} {r['contract_code']}：{r['old_observation_date']} {r['old_value']:,.2f} → {r['observation_date']} {r['new_value']:,.2f} {r['unit']}，差额 {r['delta']:+,.2f}；{r['evidence_tag']}。[来源]({r['source_url']})"]
        if r['symbol'] in ['RU','NR'] and r['metric_id'].startswith('warehouse'):
            lines+=['  注册仓单变化须核对交割、到期注销与实际出库，不能单独认定终端消费或社会库存去化。']
    by_id={r['observation_id']:r for r in rows}
    for chart in charts:
        r=by_id[chart['observation_id']]
        lines+=['',f"![{r['symbol']} {r['metric_id']}]({chart['file']})",'',
          f"最新变化：{r['delta']:+,.2f} {r['unit']}，统计期{r['observation_date']}，发布时刻未知时只标首次发现。",
          f"历史位置：{chart['n']}个真实{r['frequency']}观测，未插值、未虚构分位。",
          '供需解释：单项指标须与供需、现货结构交叉验证。',
          'CTA含义/证伪：保留人工复核，不自动升降评级；口径或交割解释不成立时撤回信号。']
    lines+=['','## 数据缺口与接管状态',f"P0已核验 {sum(x['status']=='verified' for x in p0)}/{len(p0)}；切换阻塞见readiness.json。",
      '完整证据留在私有数据包；未知或过期不补值。','','## 国投期货保证金及涨跌停板监测',
      f"状态：{risk['status']}；{risk['reason']}。完整公司费率基线未核实，不能宣称全站无变化。"]
    text='\n'.join(lines)+'\n';(out/'report.md').write_text(text)
    manifest=dict(run_id=run_id,report_date=local.date().isoformat(),freeze_at=freeze.isoformat(),previous_freeze=previous.isoformat(),mode=mode,
      generated_at=now(),evidence_rows=len(rows),meaningful_changes=len(changes),charts=charts,current_rating_reviewed=False,
      delivery_status='not_attempted',replacement_ready=readiness['replacement_ready'],existing_0800_task_modified=False,
      report_sha256=digest(text.encode()))
    (out/'report_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    (out/'readiness.json').write_text(json.dumps(readiness,ensure_ascii=False,indent=2))
    handoff=dict(schema_version=1,run_id=run_id,freeze_at=freeze.isoformat(),mode=mode,overview=overview,
      changed_evidence=[r for r in rows if r['meaningful_change']],risk=risk,readiness=readiness,
      replacement_ready=readiness['replacement_ready'],instructions='Only cite visible facts. No automatic rating upgrades. Generated is not delivered.')
    (out/'briefing.json').write_text(json.dumps(handoff,ensure_ascii=False,default=str,indent=2))
    store.db.execute('INSERT OR REPLACE INTO daily_run VALUES (?,?,?,?,?,current_timestamp,?,?)',
      [run_id,local.date(),freeze,previous,mode,str(out),manifest['report_sha256']])
    if rows:
        frame=pd.DataFrame([(run_id,r['observation_id'],r['evidence_tag']) for r in rows],columns=['run_id','observation_id','evidence_tag'])
        store.db.register('_daily_rows',frame)
        try:store.db.execute('INSERT OR REPLACE INTO daily_evidence SELECT * FROM _daily_rows')
        finally:store.db.unregister('_daily_rows')
    return manifest
