"""Synthetic local scheduling, idempotency and honest provenance tests."""
from datetime import datetime,timedelta
import json

import pytest

from agri_cta.runtime import plan,begin,finish,connect
from agri_cta.continuity import archive_report,historical_state,SYMBOLS
from agri_cta.store import Store


def dt(time):return datetime.fromisoformat('2026-11-20T'+time+'+08:00')


def body():
    return '| 品种 | 当前评级 |\n|---|---|\n'+''.join(f'| {s} | 多★★ |\n' for s in SYMBOLS)


def test_missing_provider_metadata_is_audited_not_a_blocker():
    assert plan(dt('07:02:00'))['phase']=='prepare_data'
    p=plan(dt('08:05:00'))
    assert p['phase']=='render_report' and p['provider_run_id'] is None
    assert p['provider_scheduled_for'] is None and p['decision_source']=='execution_window_and_local_idempotency'


def test_known_delayed_prepare_stays_prepare_and_early_report_never_publishes():
    assert plan(dt('08:05:00'),'2026-11-20T07:00:00+08:00')['phase']=='prepare_data'
    assert plan(dt('07:55:00'),'2026-11-20T08:00:00+08:00')['phase']=='prepare_data'


def test_report_slot_is_atomic_and_completed_response_not_emitted_twice(tmp_path):
    a=begin(tmp_path,'synthetic-task',dt('08:01:00'))
    assert a['action']=='run' and a['execution_id'].startswith('local-')
    assert begin(tmp_path,'synthetic-task',dt('08:02:00'))['action']=='skip_running'
    p=tmp_path/'response.md';p.write_text(body())
    r=finish(tmp_path,a['execution_id'],p,at=dt('08:03:00'))
    assert r['status']=='response_ready_delivery_unconfirmed' and not r['delivery_verified']
    assert begin(tmp_path,'synthetic-task',dt('08:04:00'))['action']=='skip_already_completed'


def test_failed_execution_can_retry_without_inventing_success(tmp_path):
    a=begin(tmp_path,'synthetic-task',dt('08:01:00'))
    assert finish(tmp_path,a['execution_id'],error='synthetic source failure',at=dt('08:02:00'))['status']=='failed'
    b=begin(tmp_path,'synthetic-task',dt('08:03:00'))
    assert b['action']=='run' and b['execution_id']!=a['execution_id']


def test_prepare_completion_does_not_claim_report_or_delivery(tmp_path):
    a=begin(tmp_path,'synthetic-task',dt('07:01:00'))
    r=finish(tmp_path,a['execution_id'],at=dt('07:04:00'))
    assert r['status']=='prepared' and r['report_path'] is None and not r['delivery_verified']
    assert begin(tmp_path,'synthetic-task',dt('08:01:00'))['action']=='run'


def test_expired_lease_can_recover_and_unknown_execution_cannot_finish(tmp_path):
    a=begin(tmp_path,'synthetic-task',dt('08:01:00'))
    b=begin(tmp_path,'synthetic-task',dt('10:02:00'))
    assert b['action']=='run' and a['execution_id']!=b['execution_id']
    with pytest.raises(ValueError):finish(tmp_path,a['execution_id'],error='not owner')


def test_explicit_recovery_is_actual_time_not_production():
    p=plan(dt('09:01:00'),recovery=True)
    assert p['mode']=='parallel' and p['decision_source']=='explicit_recovery'


def test_local_draft_archives_without_fabricated_provider_fields(tmp_path):
    st=Store(tmp_path/'private')
    r=archive_report(st,dict(source_kind='generated_draft',task_id='synthetic-task',
      local_execution_id='local-synthetic',report_date='2026-11-19',
      source_created_at='2026-11-19T09:00:00+08:00',content_text=body()))
    record=st.db.execute('SELECT provider_run_id,source_metadata_json FROM report_snapshot').fetchone()
    assert record[0] is None and json.loads(record[1])['identity_kind']=='local_generation_not_provider'
    state=historical_state(st,'2026-11-20',dt('07:50:00'))
    assert state['previous_report'] is None
    assert state['previous_generated_report']['status']=='generated_response_delivery_unconfirmed'


def test_historical_column_does_not_become_new_rating():
    from agri_cta.continuity import parse_ratings
    historical=body().replace('当前评级','历史评级')
    assert all(r['rating'] is None for r in parse_ratings(historical))


def test_published_report_still_requires_actual_provider_metadata(tmp_path):
    st=Store(tmp_path/'private')
    with pytest.raises(ValueError):archive_report(st,dict(source_kind='automation_published_result',
       task_id='synthetic',local_execution_id='local-synthetic',report_date='2026-11-19',
       source_created_at='2026-11-19T09:00:00+08:00',content_text=body()))


def test_late_generated_evidence_not_repeated_as_new_next_morning(tmp_path):
    from test_handoff import seed
    from agri_cta.daily import evidence,init_state
    st=seed(tmp_path);init_state(st)
    oid=st.db.execute("SELECT observation_id FROM observation WHERE observation_date='2026-11-12'").fetchone()[0]
    payload=dict(source_kind='generated_draft',task_id='synthetic-task',local_execution_id='local-late',
      report_date='2026-11-12',source_created_at='2026-11-12T16:00:00+08:00',
      content_text=body(),reported_observation_ids=[oid])
    archive_report(st,payload);archive_report(st,payload)
    rows,_=evidence(st,datetime.fromisoformat('2026-11-13T07:50:00+08:00'),datetime.fromisoformat('2026-11-12T07:50:00+08:00'))
    r=next(r for r in rows if r['observation_id']==oid)
    assert r['already_in_previous_bundle'] and not r['meaningful_change']
    assert st.db.execute('SELECT count(*) FROM report_evidence').fetchone()[0]==1
    assert historical_state(st,'2026-11-13',datetime.fromisoformat('2026-11-13T07:50:00+08:00'))['previous_report'] is None


def test_unknown_reported_observation_rejected_before_archive(tmp_path):
    st=Store(tmp_path/'private')
    with pytest.raises(ValueError,match='actual stored observation'):
        archive_report(st,dict(source_kind='generated_draft',task_id='synthetic-task',
          local_execution_id='local-synthetic',report_date='2026-11-19',source_created_at='2026-11-19T09:00:00+08:00',
          content_text=body(),reported_observation_ids=['unknown-synthetic-observation']))
    assert st.db.execute('SELECT count(*) FROM report_snapshot').fetchone()[0]==0
