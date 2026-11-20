"""Register externally reviewed, cited research; never synthesize approval in code."""
import json
import re
from datetime import datetime
from .store import digest,now


def init_reviews(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS research_review (
      review_id VARCHAR PRIMARY KEY,freeze_at TIMESTAMPTZ,registered_at TIMESTAMPTZ,
      reviewer VARCHAR,reviewer_kind VARCHAR,review_ref VARCHAR,payload_sha256 VARCHAR,payload_json VARCHAR)""")


def register_review(store,payload):
    from .daily import visible,load_products
    init_reviews(store)
    if payload.get('approved_for_research_use') is not True:
        raise ValueError('explicit external research approval is required')
    reviewer=payload.get('reviewer',{})
    if reviewer.get('kind') not in ['human','model'] or not reviewer.get('id') or not reviewer.get('review_ref'):
        raise ValueError('reviewer identity, kind and external review trace required')
    freeze=datetime.fromisoformat(payload['freeze_at'])
    if freeze.tzinfo is None:raise ValueError('review freeze requires timezone')
    frame=visible(store,freeze);known=set(frame.observation_id)
    products=load_products();reviews=payload.get('products',[])
    if len(reviews)!=9 or {x['symbol'] for x in reviews}!=set(products):
        raise ValueError('review must cover all nine products exactly once')
    for r in reviews:
        if not re.fullmatch(r'(多|空|观察)★{1,4}',r.get('rating','')):
            raise ValueError('invalid rating')
        if r.get('safety_margin') not in ['high','medium','low']:
            raise ValueError('reviewed safety margin required')
        if r.get('stage') not in ['预期形成','基本面验证','价格确认','趋势启动','逻辑证伪']:
            raise ValueError('reviewed stage required')
        if not r.get('falsifiers') or not r.get('analysis'):
            raise ValueError('analysis and falsification conditions required')
        dims=r.get('evidence_by_dimension',{})
        if any(not dims.get(k) for k in ['supply_demand','inventory','structure','trend']):
            raise ValueError('four research dimensions must be cited')
        ids={oid for values in dims.values() for oid in values}
        if len(ids)<3 or not ids<=known:
            raise ValueError('review references insufficient, unknown or future evidence')
        relevant=frame[frame.observation_id.isin(ids)]
        allowed={r['symbol'],'ENSO'}|({'NR'} if r['symbol']=='RU' else set())
        if not set(relevant.commodity)&allowed:
            raise ValueError('review lacks product-related evidence')
    encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False)
    sha=digest(encoded.encode());rid=sha[:24]
    store.db.execute('INSERT OR IGNORE INTO research_review VALUES (?,?,current_timestamp,?,?,?,?,?)',
      [rid,freeze,reviewer['id'],reviewer['kind'],reviewer['review_ref'],sha,encoded])
    return dict(review_id=rid,status='registered_external_review',reviewer_kind=reviewer['kind'])


def load_review(store,freeze):
    init_reviews(store)
    row=store.db.execute('SELECT payload_json FROM research_review WHERE freeze_at=? ORDER BY registered_at DESC LIMIT 1',[freeze]).fetchone()
    return json.loads(row[0]) if row else None


def register_parallel_review(store,payload):
    from pathlib import Path
    from .daily import init_state,TZ
    init_state(store)
    run=store.db.execute('SELECT report_date,freeze_at,mode,bundle_path FROM daily_run WHERE run_id=?',[payload['run_id']]).fetchone()
    if not run or run[2]!='production':raise ValueError('parallel review requires an actual production-cutoff bundle')
    trade_date=payload['trade_date']
    if not payload.get('reviewer') or payload.get('passed') is not True:
        raise ValueError('explicit completed external review required')
    baseline_path=Path(payload['baseline_path'])
    raw=baseline_path.read_bytes();baseline=json.loads(raw)
    if baseline.get('automation_id')!='6a9bc7951fb08191be0301c8191372a1' or not baseline.get('run_id') or not baseline.get('content_text'):
        raise ValueError('original automation run and actual text required')
    scheduled=datetime.fromisoformat(baseline['scheduled_for'])
    if scheduled.tzinfo is None or scheduled.astimezone(TZ).date()!=run[0]:
        raise ValueError('baseline report date mismatch')
    qa=json.loads((Path(run[3])/'qa.json').read_text())
    if not qa.get('all_p0_verified'):raise ValueError('incomplete P0 coverage cannot count as passed validation')
    proof=store.db.execute("""SELECT 1 FROM history WHERE observation_date=? AND collected_at<=?
       AND (published_at IS NULL OR published_at<=?)
       AND source_id IN ('SHFE','SHFE_ARCHIVE') AND metric_id='futures_close'
       AND data_status IN ('verified','provisional') LIMIT 1""",[trade_date,run[1],run[1]]).fetchone()
    if not proof:raise ValueError('official trading-day evidence required')
    if payload.get('critical_errors',1)!=0 or payload.get('missed_material_evidence',1)!=0:
        raise ValueError('unresolved comparison findings')
    store.db.execute('INSERT OR REPLACE INTO parallel_review VALUES (?,?,?,?,current_timestamp,?,?,?)',
      [trade_date,payload['run_id'],str(baseline_path),digest(raw),payload['reviewer'],'passed',json.dumps(payload,ensure_ascii=False)])
    return dict(status='recorded_external_parallel_review',trade_date=trade_date)
