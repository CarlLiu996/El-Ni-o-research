"""Immutable actual-report archives and dated historical rating continuity.

An imported published report is evidence of what was reported, never approval of
its trading view. Generated drafts remain distinguishable from published runs.
"""
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .store import digest, now

TZ = ZoneInfo('Asia/Shanghai')
SYMBOLS = ('SR','CF','RU','M','C','P','Y','OI','RM')


def init_archive(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS report_snapshot (
      snapshot_id VARCHAR PRIMARY KEY,report_date DATE,source_kind VARCHAR,
      task_id VARCHAR,provider_run_id VARCHAR,source_created_at TIMESTAMPTZ,
      archived_at TIMESTAMPTZ,content_sha256 VARCHAR,content_text VARCHAR,
      source_metadata_json VARCHAR);
      CREATE TABLE IF NOT EXISTS rating_snapshot (
      snapshot_id VARCHAR,symbol VARCHAR,rating VARCHAR,stage VARCHAR,
      status VARCHAR,PRIMARY KEY(snapshot_id,symbol));
      CREATE TABLE IF NOT EXISTS report_evidence (
      snapshot_id VARCHAR,observation_id VARCHAR,
      PRIMARY KEY(snapshot_id,observation_id));''')


def parse_ratings(text):
    """Only the nine-product overview's rating column; never mine prose stars."""
    blocks = re.findall(r'(?:^\|[^\n]+\n?)+', text, flags=re.M)
    for block in blocks:
        lines = block.splitlines()
        header = [x.strip() for x in lines[0].strip('|').split('|')]
        indices = [i for i,x in enumerate(header) if '评级' in x or '星级' in x]
        if not indices or not any('品种' in x for x in header):
            continue
        rating_i = indices[-1]
        stage_i = next((i for i,x in enumerate(header) if '阶段' in x), None)
        result = {}
        for line in lines[1:]:
            cells = [x.strip() for x in line.strip('|').split('|')]
            if len(cells) != len(header) or re.fullmatch(r'[:\- ]+', cells[0]):
                continue
            matches = re.findall(r'(?<![A-Z])(SR|CF|RU|NR|M|C|P|Y|OI|RM)(?![A-Z])', cells[0])
            matches = [x for x in matches if x != 'NR']
            if len(set(matches)) != 1:
                continue
            symbol = matches[0]
            if symbol in result:
                raise ValueError('duplicate product in report overview')
            cell = cells[rating_i]
            # An old->pending cell cannot become a newly reported current rating.
            if '→' in cell and cell.split('→')[-1].strip():
                cell = cell.split('→')[-1]
            historical_column=any(x in header[rating_i] for x in ('历史','上期','上次')) and not any(x in header[rating_i] for x in ('当前','本期'))
            unreviewed = historical_column or any(x in cell for x in ('未复核','待复核','未重评','历史','待核'))
            match = re.search(r'(多|空|观察)★{1,4}(?!★)',cell) if not unreviewed else None
            result[symbol] = dict(symbol=symbol,rating=match.group(0) if match else None,
                stage=cells[stage_i] if stage_i is not None else None,
                status='reported_historical_view_not_current_review' if match else 'no_current_rating_reported')
        if set(result) != set(SYMBOLS):
            raise ValueError('actual report overview must cover all nine products')
        return list(result.values())
    raise ValueError('actual nine-product rating overview not found')


def archive_report(store,payload):
    init_archive(store)
    kind = payload.get('source_kind')
    if kind not in ('automation_published_result','generated_draft'):
        raise ValueError('explicit published-result or generated-draft provenance required')
    if payload.get('content_truncated'):
        raise ValueError('truncated result is not a complete report archive')
    text = payload.get('content_text','')
    run_ref=payload.get('run_id') if kind=='automation_published_result' else payload.get('local_execution_id',payload.get('run_id'))
    if not text or not payload.get('task_id') or not run_ref:
        raise ValueError('actual text, task ID and explicit execution identity required')
    created = datetime.fromisoformat(payload['source_created_at'].replace('Z','+00:00'))
    scheduled = datetime.fromisoformat(payload['scheduled_for'].replace('Z','+00:00')) if payload.get('scheduled_for') else None
    if created.tzinfo is None or (scheduled and scheduled.tzinfo is None) or created > datetime.now(timezone.utc):
        raise ValueError('actual past timezone-aware provider times required')
    day = date.fromisoformat(payload['report_date'])
    if scheduled and scheduled.astimezone(TZ).date() != day:
        raise ValueError('report date differs from provider scheduled date')
    if not scheduled and (kind=='automation_published_result' or created.astimezone(TZ).date()!=day):
        raise ValueError('published reports require provider schedule; local drafts require actual generation date')
    if kind == 'automation_published_result' and not payload.get('completed_at'):
        raise ValueError('published archive requires completed provider result')
    sha = digest(text.encode())
    if payload.get('content_sha256',sha) != sha:
        raise ValueError('report content hash mismatch')
    ratings = parse_ratings(text)
    evidence_ids=set(payload.get('reported_observation_ids',[]))
    for oid in evidence_ids:
        if not store.db.execute('SELECT 1 FROM observation WHERE observation_id=?',[oid]).fetchone():
            raise ValueError('reported evidence must refer to an actual stored observation')
    sid = digest((payload['task_id']+run_ref+kind+sha).encode())
    metadata = {k:v for k,v in payload.items() if k != 'content_text'}
    metadata.update(provider_run_id=payload.get('run_id') if kind=='automation_published_result' else None,
      identity_kind='provider_run' if kind=='automation_published_result' else 'local_generation_not_provider',
      delivery_verified=False)
    store.db.execute('BEGIN TRANSACTION')
    try:
        store.db.execute('INSERT OR IGNORE INTO report_snapshot VALUES (?,?,?,?,?,?,current_timestamp,?,?,?)',
          [sid,day,kind,payload['task_id'],metadata['provider_run_id'],created,sha,text,
           json.dumps(metadata,ensure_ascii=False)])
        for r in ratings:
            store.db.execute('INSERT OR IGNORE INTO rating_snapshot VALUES (?,?,?,?,?)',
              [sid,r['symbol'],r['rating'],r['stage'],r['status']])
        for oid in evidence_ids:
            store.db.execute('INSERT OR IGNORE INTO report_evidence VALUES (?,?)',[sid,oid])
        store.db.execute('COMMIT')
    except Exception:
        store.db.execute('ROLLBACK');raise
    out=store.root/'report_archive'/day.isoformat()/sid
    out.mkdir(parents=True,exist_ok=True)
    (out/'report.md').write_text(text)
    (out/'metadata.json').write_text(json.dumps({**metadata,'snapshot_id':sid,
        'content_sha256':sha,'ratings':ratings},ensure_ascii=False,indent=2))
    return dict(snapshot_id=sid,report_date=day.isoformat(),source_kind=kind,
        content_sha256=sha,ratings_found=sum(r['rating'] is not None for r in ratings),archive=str(out))


def historical_state(store,report_date,cutoff):
    init_archive(store)
    day=date.fromisoformat(report_date) if isinstance(report_date,str) else report_date
    latest=store.db.execute('''SELECT snapshot_id,report_date,source_created_at,
       content_sha256,content_text FROM report_snapshot
       WHERE report_date<? AND source_created_at<=? AND source_kind='automation_published_result'
       ORDER BY report_date DESC,source_created_at DESC,archived_at DESC LIMIT 1''',[day,cutoff]).fetchone()
    views=[]
    for symbol in SYMBOLS:
        row=store.db.execute('''SELECT s.snapshot_id,s.report_date,r.rating,r.stage,s.content_sha256
          FROM rating_snapshot r JOIN report_snapshot s USING(snapshot_id)
          WHERE r.symbol=? AND r.rating IS NOT NULL AND s.report_date<?
          AND s.source_created_at<=? AND s.source_kind='automation_published_result'
          ORDER BY s.report_date DESC,s.source_created_at DESC,s.archived_at DESC LIMIT 1''',
          [symbol,day,cutoff]).fetchone()
        views.append(dict(symbol=symbol,last_reported_rating=row[2] if row else None,
          rating_report_date=str(row[1]) if row else None,stage_in_that_report=row[3] if row else None,
          snapshot_id=row[0] if row else None,source_report_sha256=row[4] if row else None,
          current_rating_confirmed=False,status='dated_historical_report_view' if row else 'not_archived'))
    previous=None
    if latest:
        previous=dict(snapshot_id=latest[0],report_date=str(latest[1]),published_at=str(latest[2]),
          content_sha256=latest[3],content_text=latest[4])
    generated=store.db.execute('''SELECT snapshot_id,report_date,source_created_at,content_sha256,content_text
      FROM report_snapshot WHERE report_date<? AND source_created_at<=? AND source_kind='generated_draft'
      ORDER BY report_date DESC,source_created_at DESC LIMIT 1''',[day,cutoff]).fetchone()
    previous_generated=None if not generated else dict(snapshot_id=generated[0],report_date=str(generated[1]),
      generated_at=str(generated[2]),content_sha256=generated[3],content_text=generated[4],
      status='generated_response_delivery_unconfirmed',delivery_verified=False)
    return dict(previous_report=previous,previous_generated_report=previous_generated,historical_ratings=views,
      warning='Historical reporting continuity is not a current research approval; missing newer ratings do not erase an older dated view.')
