"""Runnable local scheduling and one-response-per-day coordination.

Provider metadata is optional audit context. Local execution IDs are explicitly
not provider run IDs; generated responses never imply confirmed user delivery.
"""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .daily import TZ
from .store import digest


def plan(at=None,scheduled_for=None,recovery=False):
    at=at or datetime.now(timezone.utc)
    if at.tzinfo is None:raise ValueError('timezone-aware execution clock required')
    local=at.astimezone(TZ)
    result=dict(report_date=local.date().isoformat(),execution_at=at.isoformat(),
        provider_scheduled_for=scheduled_for,provider_run_id=None,
        decision_source='execution_window_and_local_idempotency',
        phase='prepare_data' if local.hour<8 else 'render_report',
        mode='parallel' if recovery or local.hour<8 else 'production')
    if scheduled_for and not recovery:
        stamp=datetime.fromisoformat(scheduled_for.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('invalid supplied provider time')
        planned=stamp.astimezone(TZ)
        if planned.hour not in (7,8) or planned.minute or planned.second:
            raise ValueError('unexpected supplied provider schedule')
        result.update(report_date=planned.date().isoformat(),
            phase='prepare_data' if planned.hour==7 else 'render_report',
            decision_source='provider_schedule')
        if planned.date()!=local.date():
            raise ValueError('delayed prior-day schedule requires explicit recovery')
    if recovery:result.update(phase='render_report',mode='parallel',decision_source='explicit_recovery')
    # An early manual execution never publishes a production report before 08:00.
    if result['phase']=='render_report' and local.hour<8 and not recovery:
        result.update(phase='prepare_data',mode='parallel',decision_source='not_before_0800')
    return result


def connect(data):
    root=Path(data);root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(root/'runtime.sqlite'),timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS execution_slot (
      slot TEXT PRIMARY KEY,execution_id TEXT,status TEXT,started_at TEXT,
      lease_until TEXT,finished_at TEXT,report_path TEXT,report_sha256 TEXT,
      metadata_json TEXT,error TEXT)''')
    db.commit()
    return db


def begin(data,task_id,at=None,scheduled_for=None,recovery=False):
    at=at or datetime.now(timezone.utc);p=plan(at,scheduled_for,recovery)
    slot=task_id+'|'+p['report_date']+'|'+p['phase']
    db=connect(data)
    try:
        db.execute('BEGIN IMMEDIATE')
        prior=db.execute('SELECT * FROM execution_slot WHERE slot=?',[slot]).fetchone()
        if prior and prior['status'] in ('prepared','response_ready_delivery_unconfirmed'):
            db.commit()
            return dict(**p,action='skip_already_completed',execution_id=prior['execution_id'],
              status=prior['status'],report_path=prior['report_path'],delivery_verified=False)
        if prior and prior['status']=='running' and datetime.fromisoformat(prior['lease_until'])>at:
            db.commit();return dict(**p,action='skip_running',execution_id=prior['execution_id'])
        eid='local-'+uuid.uuid4().hex
        db.execute('INSERT OR REPLACE INTO execution_slot VALUES (?,?,?,?,?,NULL,NULL,NULL,?,NULL)',
          [slot,eid,'running',at.isoformat(),(at+timedelta(hours=2)).isoformat(),json.dumps(p)])
        db.commit()
        return dict(**p,action='run',execution_id=eid,run_id_kind='local_execution_not_provider')
    except Exception:
        db.rollback();raise
    finally:db.close()


def finish(data,execution_id,report=None,error=None,at=None):
    at=at or datetime.now(timezone.utc);db=connect(data)
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM execution_slot WHERE execution_id=?',[execution_id]).fetchone()
        if not row or row['status']!='running':raise ValueError('no owned running execution')
        phase=json.loads(row['metadata_json'])['phase']
        path=sha=None
        if error:status='failed'
        elif phase=='prepare_data':status='prepared'
        else:
            if not report:raise ValueError('actual complete response file required')
            path=str(Path(report).resolve());raw=Path(path).read_bytes()
            from .continuity import parse_ratings
            parse_ratings(raw.decode())  # All nine products, actual report body.
            sha=digest(raw);status='response_ready_delivery_unconfirmed'
        db.execute('UPDATE execution_slot SET status=?,finished_at=?,report_path=?,report_sha256=?,error=? WHERE execution_id=?',
          [status,at.isoformat(),path,sha,error,execution_id])
        db.commit()
        return dict(execution_id=execution_id,status=status,report_path=path,report_sha256=sha,delivery_verified=False)
    except Exception:
        db.rollback();raise
    finally:db.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['begin','finish','fail','status'])
    p.add_argument('--data',required=True);p.add_argument('--task-id',default='Automation_d98151445b48819181272485b7fa9dba')
    p.add_argument('--scheduled-for');p.add_argument('--recovery',action='store_true')
    p.add_argument('--execution-id');p.add_argument('--report-file');p.add_argument('--error')
    a=p.parse_args()
    if a.command=='begin':r=begin(a.data,a.task_id,scheduled_for=a.scheduled_for,recovery=a.recovery)
    elif a.command in ('finish','fail'):
        if not a.execution_id:p.error('--execution-id required')
        if a.command=='fail' and not a.error:p.error('--error required')
        r=finish(a.data,a.execution_id,a.report_file,a.error)
    else:
        db=connect(a.data)
        try:r=[dict(x) for x in db.execute('SELECT * FROM execution_slot ORDER BY started_at DESC LIMIT 10')]
        finally:db.close()
    print(json.dumps(r,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
