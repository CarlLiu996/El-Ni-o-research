"""Synthetic bridge tests only; no fixtures enter the real market store."""
from datetime import datetime
import json
from pathlib import Path

import duckdb
import pytest

from agri_cta.continuity import archive_report,historical_state,parse_ratings,SYMBOLS
from agri_cta.handoff import export_handoff,read_handoff,scheduled_phase
from agri_cta.store import Store,digest,row


def dt(s):return datetime.fromisoformat(s+'+08:00')


def report(day='2026-11-12',rating='多★★ →',kind='automation_published_result'):
    text='| 品种 | 阶段 | CTA评级 |\n|---|---|---|\n'
    text+=''.join(f'| {x}/NR | 基本面验证 | {rating} |\n' if x=='RU' else
        f'| {x} | 基本面验证 | {rating} |\n' for x in SYMBOLS)
    return dict(source_kind=kind,task_id='synthetic-task',run_id='synthetic-'+day+kind,
      report_date=day,scheduled_for=day+'T08:00:00+08:00',
      source_created_at=day+'T08:01:00+08:00',completed_at=day+'T08:02:00+08:00',
      content_text=text,content_truncated=False)


def seed(tmp_path):
    st=Store(tmp_path/'private')
    raw=st.root/'raw/synthetic.txt';raw.parent.mkdir();raw.write_bytes(b'synthetic official-like fixture')
    st.db.execute("INSERT INTO source_document VALUES ('synthetic','SHFE_RECEIPTS','https://example.invalid',current_timestamp,?,'raw/synthetic.txt',200,'text/plain','synthetic','parsed',NULL)",[digest(raw.read_bytes())])
    for day,value,seen in [('2026-11-10',1000,'2026-11-11T06:00:00'),('2026-11-12',1200,'2026-11-12T15:00:00')]:
        st.ingest('synthetic',[row('warehouse_receipts_warehouse','RU',day,value,'tonnes','daily')])
        st.db.execute('UPDATE observation SET collected_at=? WHERE observation_date=?',[dt(seen),day])
    return st


def export(st):
    return export_handoff(st,'2026-11-13',dt('2026-11-13T07:50:00'),dt('2026-11-12T07:50:00'),'production')


def test_actual_history_is_archived_idempotently_and_never_current_approval(tmp_path):
    st=seed(tmp_path);payload=report()
    a=archive_report(st,payload);b=archive_report(st,payload)
    assert a['snapshot_id']==b['snapshot_id'] and a['ratings_found']==9
    assert st.db.execute('SELECT count(*) FROM report_snapshot').fetchone()[0]==1
    state=historical_state(st,'2026-11-13',dt('2026-11-13T07:50:00'))
    assert state['previous_report']['content_text']==payload['content_text']
    assert all(r['last_reported_rating']=='多★★' and not r['current_rating_confirmed'] for r in state['historical_ratings'])


def test_new_report_without_rating_does_not_erase_older_actual_view(tmp_path):
    st=seed(tmp_path);archive_report(st,report())
    archive_report(st,report('2026-11-13','历史未复核'))
    state=historical_state(st,'2026-11-14',dt('2026-11-14T07:50:00'))
    assert state['previous_report']['report_date']=='2026-11-13'
    assert all(r['rating_report_date']=='2026-11-12' for r in state['historical_ratings'])


def test_old_to_pending_cell_is_not_new_rating():
    assert all(r['rating'] is None for r in parse_ratings(report(rating='多★★ → 待复核')['content_text']))


def test_draft_not_promoted_to_published_history(tmp_path):
    st=seed(tmp_path);archive_report(st,report())
    archive_report(st,report('2026-11-13','空★★★★','generated_draft'))
    state=historical_state(st,'2026-11-14',dt('2026-11-14T07:50:00'))
    assert state['previous_report']['report_date']=='2026-11-12'
    assert all(r['last_reported_rating']=='多★★' for r in state['historical_ratings'])


@pytest.mark.parametrize('update',[{'content_sha256':'wrong'},{'content_truncated':True},
    {'scheduled_for':'2026-11-11T08:00:00+08:00'},{'completed_at':None}])
def test_invalid_provider_archive_rejected(tmp_path,update):
    st=seed(tmp_path)
    with pytest.raises(ValueError):archive_report(st,{**report(),**update})
    assert st.db.execute('SELECT count(*) FROM report_snapshot').fetchone()[0]==0


def test_incomplete_overview_and_prose_stars_rejected():
    text=report()['content_text'].replace('| RM | 基本面验证 | 多★★ → |\n','')
    with pytest.raises(ValueError):parse_ratings(text+'\n关注多★★★★，这只是正文示例')


def test_physical_frozen_database_excludes_late_and_future_published_revisions(tmp_path):
    st=seed(tmp_path);archive_report(st,report())
    st.ingest('synthetic',[row('warehouse_receipts_warehouse','RU','2026-11-12',9999,'tonnes','daily')])
    st.db.execute('UPDATE observation SET collected_at=? WHERE vintage=2',[dt('2026-11-13T09:00:00')])
    st.ingest('synthetic',[row('warehouse_receipts_warehouse','RU','2026-11-12',8888,'tonnes','daily',published_at=dt('2026-11-14T07:00:00'))])
    st.db.execute('UPDATE observation SET collected_at=? WHERE vintage=3',[dt('2026-11-12T16:00:00')])
    out=export(st);result=read_handoff(st.root,'2026-11-13')
    assert result['manifest']['qa']['asof_observations']==2
    assert result['payload']['changed_evidence'][0]['new_value']==1200
    assert result['payload']['chart_data'][0]['kind']=='two_point_comparison'
    assert result['payload']['chart_data'][0]['n']==2
    frozen=duckdb.connect(str(Path(out['bundle'])/'frozen.duckdb'),read_only=True)
    assert frozen.execute('SELECT max(value) FROM observation_asof').fetchone()[0]==1200
    assert frozen.execute('SELECT count(*) FROM rating_snapshot').fetchone()[0]==9
    assert frozen.execute('SELECT count(*) FROM report_snapshot').fetchone()[0]==1
    frozen.close()
    assert not result['payload']['all_nine_product_p0_complete']
    assert list(Path(out['bundle']).glob('*.png'))==[]


def test_reader_rejects_stale_date_and_tampered_frozen_bundle(tmp_path):
    st=seed(tmp_path);out=export(st)
    with pytest.raises(ValueError,match='stale'):read_handoff(st.root,'2026-11-14')
    (Path(out['bundle'])/'chart_data.json').write_text('[]')
    with pytest.raises(ValueError,match='hash mismatch'):read_handoff(st.root,'2026-11-13')


def test_bad_raw_snapshot_does_not_replace_last_valid_pointer(tmp_path):
    st=seed(tmp_path);export(st)
    pointer=(st.root/'handoff/latest.json').read_bytes()
    (st.root/'raw/synthetic.txt').write_text('corrupted fixture')
    with pytest.raises(ValueError,match='hash mismatch'):export(st)
    assert (st.root/'handoff/latest.json').read_bytes()==pointer


def test_late_recovery_cannot_be_called_production_freeze(tmp_path):
    st=seed(tmp_path)
    with pytest.raises(ValueError,match='07:50'):
        export_handoff(st,'2026-11-13',dt('2026-11-13T09:00:00'),dt('2026-11-12T07:50:00'),'production')


def test_scheduler_phase_uses_provider_schedule_not_late_wall_clock():
    assert scheduled_phase('2026-11-19T23:00:00Z')==dict(report_date='2026-11-20',
        scheduled_for='2026-11-19T23:00:00+00:00',phase='prepare_data')
    assert scheduled_phase('2026-11-20T00:00:00Z')['phase']=='render_report'


def test_unknown_scheduler_phase_never_guesses_to_publish():
    with pytest.raises(ValueError):scheduled_phase('2026-11-20T08:00:00')
    with pytest.raises(ValueError):scheduled_phase('2026-11-20T07:30:00+08:00')
