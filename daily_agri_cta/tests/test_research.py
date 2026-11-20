"""Synthetic external-review validation; no fake approval is registered in live data."""
import pytest
from agri_cta.store import Store,row
from agri_cta.research import register_review


def test_unapproved_or_uncited_review_rejected(tmp_path):
    st=Store(tmp_path)
    with pytest.raises(ValueError,match='approval'):
        register_review(st,dict(approved_for_research_use=False))
    assert st.db.execute('SELECT count(*) FROM research_review').fetchone()[0]==0


def test_unknown_or_future_observations_rejected(tmp_path):
    from agri_cta.daily import load_products
    st=Store(tmp_path)
    payload=dict(approved_for_research_use=True,freeze_at='2026-11-13T07:50:00+08:00',
      reviewer=dict(kind='human',id='synthetic-reviewer',review_ref='synthetic-only'),
      products=[dict(symbol=symbol,rating='观察★',safety_margin='low',stage='基本面验证',
        analysis='synthetic',falsifiers=['synthetic'],
        evidence_by_dimension=dict(supply_demand=['missing-a'],inventory=['missing-b'],structure=['missing-c'],trend=['missing-d']))
        for symbol in load_products()])
    with pytest.raises(ValueError,match='future evidence'):register_review(st,payload)
    assert st.db.execute('SELECT count(*) FROM research_review').fetchone()[0]==0


def test_registered_review_is_idempotent_and_applied(tmp_path):
    from agri_cta.daily import load_products,daily_report
    import pandas as pd
    st=Store(tmp_path/'data')
    st.db.execute("INSERT INTO source_document VALUES ('synthetic','TEST','https://example.invalid',current_timestamp,'hash','synthetic',200,'text/plain','synthetic','parsed',NULL)")
    rows=[]
    for symbol in load_products():
        for metric in ['synthetic_supply','synthetic_stock','synthetic_structure']:
            rows.append(row(metric,symbol,'2026-11-12',100,'tonnes','daily'))
    st.ingest('synthetic',rows)
    st.db.execute("UPDATE observation SET collected_at='2026-11-12T06:00:00Z'")
    known=st.db.execute('SELECT commodity,observation_id FROM observation').fetchall()
    payload=dict(approved_for_research_use=True,freeze_at='2026-11-13T07:50:00+08:00',
      reviewer=dict(kind='human',id='synthetic-reviewer',review_ref='synthetic-not-real-approval'),products=[])
    for symbol in load_products():
        ids=[oid for s,oid in known if s==symbol]
        payload['products'].append(dict(symbol=symbol,rating='观察★',stage='基本面验证',safety_margin='low',
           analysis='synthetic-only',falsifiers=['synthetic-only'],
           evidence_by_dimension=dict(supply_demand=[ids[0]],inventory=[ids[1]],structure=[ids[2]],trend=[ids[2]])))
    a=register_review(st,payload);b=register_review(st,payload)
    assert a['review_id']==b['review_id']
    out=tmp_path/'report'
    manifest=daily_report(st,out,payload['freeze_at'],'2026-11-12T07:50:00+08:00','production')
    assert manifest['current_rating_reviewed'] and not manifest['replacement_ready']
    assert set(pd.read_csv(out/'overview.csv').new_rating)=={'观察★'}


def test_repeated_ad_hoc_run_cannot_count_as_trading_day_validation(tmp_path):
    from agri_cta.daily import daily_report
    from agri_cta.research import register_parallel_review
    st=Store(tmp_path/'data')
    report=daily_report(st,tmp_path/'out','2026-11-13T09:00:00+08:00','2026-11-12T07:50:00+08:00','parallel')
    with pytest.raises(ValueError,match='production-cutoff'):
        register_parallel_review(st,dict(run_id=report['run_id']))
