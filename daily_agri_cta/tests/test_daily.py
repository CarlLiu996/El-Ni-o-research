"""Synthetic as-of and replacement tests; no synthetic rows enter the real store."""
from datetime import datetime
import json
import pandas as pd
import pytest
from agri_cta.store import Store,row
from agri_cta.daily import daily_report,evidence,init_state,feature_status,cutoffs
from agri_cta.analysis import evidence_tag


def dt(s):return datetime.fromisoformat(s+'+08:00')


def seed(st):
    st.db.execute("INSERT INTO source_document VALUES ('synthetic','TEST','https://example.invalid',current_timestamp,'hash','synthetic',200,'text/plain','synthetic','parsed',NULL)")


def ingest(st,date,value,seen,pub=None):
    st.ingest('synthetic',[row('warehouse_receipts_warehouse','RU',date,value,'tonnes','daily',published_at=pub)])
    st.db.execute('UPDATE observation SET collected_at=? WHERE observation_date=?',[seen,date])


def test_daily_real_change_rerun_and_no_fake_year_chart(tmp_path):
    st=Store(tmp_path/'db');seed(st)
    ingest(st,'2026-11-10',100,dt('2026-11-11T06:00:00'))
    ingest(st,'2026-11-12',110,dt('2026-11-12T15:00:00'))
    out=tmp_path/'report'
    args=(st,out,dt('2026-11-13T07:50:00').isoformat(),dt('2026-11-12T07:50:00').isoformat(),'production')
    a=daily_report(*args);b=daily_report(*args)
    assert a['run_id']==b['run_id'] and a['meaningful_changes']==1
    assert st.db.execute('SELECT count(*) FROM daily_run').fetchone()[0]==1
    r=pd.read_csv(out/'evidence_ledger.csv').iloc[0]
    assert r.evidence_tag=='FIRST_SEEN_UNKNOWN_RELEASE' and r.delta==10
    assert not a['replacement_ready'] and a['delivery_status']=='not_attempted'
    assert len(pd.read_csv(out/'overview.csv'))==9
    assert list((out/'charts').glob('*.png'))==[]


def test_old_change_is_not_daily_new_change(tmp_path):
    st=Store(tmp_path);seed(st)
    ingest(st,'2026-11-09',100,dt('2026-11-10T06:00:00'))
    ingest(st,'2026-11-10',120,dt('2026-11-11T06:00:00'))
    init_state(st)
    rows,_=evidence(st,dt('2026-11-13T07:50:00'),dt('2026-11-12T07:50:00'))
    assert rows[0]['delta']==20 and not rows[0]['meaningful_change']


def test_publication_can_make_preobserved_record_newly_visible(tmp_path):
    st=Store(tmp_path);seed(st)
    ingest(st,'2026-11-10',100,dt('2026-11-11T06:00:00'))
    ingest(st,'2026-11-11',110,dt('2026-11-11T07:00:00'),dt('2026-11-12T15:00:00'))
    init_state(st)
    rows,_=evidence(st,dt('2026-11-13T07:50:00'),dt('2026-11-12T07:50:00'))
    assert rows[0]['evidence_tag']=='TODAY_NEW' and rows[0]['newly_visible'] and rows[0]['meaningful_change']


def test_future_not_continuing_even_if_previously_marked():
    assert evidence_tag(None,dt('2026-11-13T09:00:00'),dt('2026-11-12T07:50:00'),dt('2026-11-13T07:50:00'),True)=='NOT_YET_VISIBLE'


def test_production_cutoff_is_exact(tmp_path):
    st=Store(tmp_path)
    with pytest.raises(ValueError,match='07:50'):
        daily_report(st,tmp_path/'out',dt('2026-11-13T09:00:00').isoformat(),dt('2026-11-12T07:50:00').isoformat(),'production')


def test_empty_data_has_nine_products_headers_and_closed_gates(tmp_path):
    st=Store(tmp_path/'db')
    daily_report(st,tmp_path/'out',dt('2026-11-13T07:50:00').isoformat(),dt('2026-11-12T07:50:00').isoformat(),'production')
    assert 'metric_id' in pd.read_csv(tmp_path/'out/evidence_ledger.csv').columns
    qa=json.loads((tmp_path/'out/readiness.json').read_text())
    assert not qa['replacement_ready'] and 'delivery_verified' in qa['blocking_gates']


def test_spread_rejects_different_dates():
    f=pd.DataFrame([dict(metric_id='futures_settlement',commodity='RU',contract_code='RU2701',observation_date=pd.Timestamp('2026-11-12'),price_type='settlement',value=100,unit='CNY/tonne',frequency='daily',observation_id='a'),
                    dict(metric_id='futures_settlement',commodity='NR',contract_code='NR2701',observation_date=pd.Timestamp('2026-11-11'),price_type='settlement',value=90,unit='CNY/tonne',frequency='daily',observation_id='b')])
    assert feature_status(f,'RU_NR_same_date_relative_price','RU',dt('2026-11-13T07:50:00'))['status']=='unverified'


def test_cutoff_timezone_and_future():
    freeze,previous=cutoffs('2026-11-13','production',dt('2026-11-13T08:00:00'))
    assert freeze=='2026-11-13T07:50:00+08:00' and previous=='2026-11-12T07:50:00+08:00'
    with pytest.raises(ValueError):cutoffs('2026-11-13','production',dt('2026-11-13T07:00:00'))


def test_earlier_period_revision_remains_visible(tmp_path):
    st=Store(tmp_path);seed(st)
    ingest(st,'2026-11-10',100,dt('2026-11-11T06:00:00'))
    ingest(st,'2026-11-11',120,dt('2026-11-11T20:00:00'))
    st.ingest('synthetic',[row('warehouse_receipts_warehouse','RU','2026-11-10',101,'tonnes','daily')])
    st.db.execute("UPDATE observation SET collected_at=? WHERE vintage=2",[dt('2026-11-12T15:00:00')])
    init_state(st)
    rows,_=evidence(st,dt('2026-11-13T07:50:00'),dt('2026-11-12T07:50:00'))
    correction=next(r for r in rows if r['observation_date']=='2026-11-10')
    assert correction['comparison_basis']=='prior_vintage_same_period' and correction['delta']==1
