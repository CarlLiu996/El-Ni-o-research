"""All inline test data are SYNTHETIC; never inserted into the real history store."""
from datetime import datetime, timezone

import pytest

from agri_cta.store import Store, row
from agri_cta.sources import mpob, noaa, shfe_day, shfe_receipts
from agri_cta.analysis import evidence_tag, matched_difference, risk_change, ecdf


def dt(s):
    return datetime.fromisoformat(s+'+08:00')


def seed(st):
    st.db.execute("INSERT INTO source_document VALUES ('synthetic','TEST','https://example.invalid',current_timestamp,'hash','synthetic',200,'text/plain','synthetic','fetched',NULL)")


def test_idempotence_revision_and_reversion(tmp_path):
    st=Store(tmp_path);seed(st)
    r=row('mpob_stock','P','2025-10-31',100,'tonnes')
    assert st.ingest('synthetic',[r])==1
    assert st.ingest('synthetic',[r])==0
    assert st.ingest('synthetic',[dict(r,value=101)])==1
    assert st.ingest('synthetic',[r])==1  # Official reversion is a new vintage too.
    assert st.db.execute('SELECT list(value ORDER BY vintage) FROM observation').fetchone()[0]==[100,101,100]
    st.export()
    assert (tmp_path/'observation.parquet').is_file()


def test_document_atomic_validation(tmp_path):
    st=Store(tmp_path);seed(st)
    with pytest.raises(ValueError):
        st.ingest('synthetic',[row('stock','P','2025-01-31',1,'tonnes'),row('stock','P','2025-02-28',-1,'tonnes')])
    assert st.db.execute('SELECT count(*) FROM observation').fetchone()[0]==0


def test_empty_reject(tmp_path):
    st=Store(tmp_path)
    with pytest.raises(ValueError):st.ingest('none',[])


def test_late_weekly_disclosure():
    # Qingdao observation November 8, first published November 12, known at Nov 13 freeze.
    assert evidence_tag(dt('2026-11-12T15:00'),dt('2026-11-12T16:00'),dt('2026-11-12T07:50'),dt('2026-11-13T07:50'))=='TODAY_NEW'


def test_prior_usda_continuation():
    assert evidence_tag(dt('2026-11-10T20:00'),dt('2026-11-11T07:00'),dt('2026-11-12T07:50'),dt('2026-11-13T07:50'),True)=='CONTINUING'


def test_unknown_release_not_today_new():
    assert evidence_tag(None,dt('2026-11-12T16:00'),dt('2026-11-12T07:50'),dt('2026-11-13T07:50'))=='FIRST_SEEN_UNKNOWN_RELEASE'


def test_new_revision_not_backdated_before_ingest():
    assert evidence_tag(dt('2026-10-10T12:00'),dt('2026-11-13T10:00'),dt('2026-11-12T07:50'),dt('2026-11-13T07:50'))=='NOT_YET_VISIBLE'


def test_price_type_and_date_mismatch():
    a=dict(observation_date='2026-11-12',price_type='close',unit='CNY/tonne',contract_code='SR2701',value=10)
    b=dict(a,price_type='settlement',contract_code='SR2705')
    with pytest.raises(ValueError):matched_difference(a,b)
    with pytest.raises(ValueError):matched_difference(a,dict(a,observation_date='2026-11-11'))
    assert matched_difference(a,dict(a,contract_code='SR2705',value=8))==2


def test_unknown_old_rate_and_delivery_rule():
    r=risk_change('company_speculative_margin',22)
    assert r['previous_value'] is None and r['delta'] is None
    assert risk_change('delivery_restriction','natural_person_zero')['rule_type']=='delivery_restriction'


def test_percentile_requires_history():
    assert ecdf([1,2]) is None
    assert ecdf(list(range(30)))==1


def test_noaa_schema_and_baseline():
    b=b' YR   MON  NINO1+2  ANOM   NINO3    ANOM   NINO4    ANOM   NINO3.4  ANOM\n1950 1 1 2 3 4 5 6 7 -1.99\n'
    r=noaa(b)[0]
    assert r['value']==-1.99 and r['observation_date']=='1950-01-31'
    assert r['source_flag']=='ERSSTv5;1991-2020'
    with pytest.raises(ValueError):noaa(b'bad schema')


def test_mpob_synthetic_blanks_revision_and_palm_oil_only():
    head=['','Dec 24','Jan 25','Feb (r)','Mar (p)','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    rows=[head,['PRODUCTION (TONNES)'],['Crude Palm Oil','9','1','2','3']+['']*9,
          ['CLOSING STOCK (TONNES)'],['Palm Oil','9','4','5','6']+['']*9,
          ['EXPORT (TONNES)'],['Palm Oil','9','7','8','9']+['']*9]
    body='<h1>SUMMARY OF THE MALAYSIAN PALM OIL INDUSTRY 2025</h1><table>'
    body+=''.join('<tr>'+''.join('<td>'+x+'</td>' for x in r)+'</tr>' for r in rows)+'</table>'
    result=mpob(body.encode(),2025)
    assert len(result)==9
    assert result[1]['source_flag']=='revised'
    assert result[2]['data_status']=='provisional'
    assert all(r['observation_date']!='2024-12-31' for r in result)
    with pytest.raises(ValueError):mpob(body.encode(),2024)


def test_header_only_and_untraded_contract():
    import json
    j={'report_date':'20261112','o_curinstrument':[dict(PRODUCTGROUPID='ru',DELIVERYMONTH='2701',OPENPRICE='',HIGHESTPRICE='',LOWESTPRICE='',CLOSEPRICE='',SETTLEMENTPRICE=10,VOLUME=0,OPENINTEREST=0)]}
    r=shfe_day(json.dumps(j).encode(),'2026-11-12')
    assert {x['metric_id'] for x in r}=={'futures_settlement','futures_volume','futures_open_interest'}
    with pytest.raises(ValueError):shfe_day(json.dumps(j).encode(),'2026-11-11')
    with pytest.raises(ValueError):shfe_receipts(b'<table>header only</table>','2026-11-12')


def test_conflicting_document_values_fail_closed(tmp_path):
    st=Store(tmp_path);seed(st)
    a=row('stock','P','2025-01-31',1,'tonnes')
    with pytest.raises(ValueError,match='conflicting duplicate'):
        st.ingest('synthetic',[a,dict(a,value=2)])
    assert st.db.execute('SELECT count(*) FROM observation').fetchone()[0]==0


def test_synthetic_official_archive():
    import io,zipfile,openpyxl
    from agri_cta.sources import shfe_archive
    wb=openpyxl.Workbook();ws=wb.active
    ws.append(['synthetic']);ws.append(['synthetic'])
    ws.append(['synthetic']*14)
    ws.append(['Contract','Date','pre close','Pre settle','Open','High','Low','Close','Settle','ch1','ch2','Volume','Amount','OI'])
    ws.append(['ru2501','20250102',9,9,10,12,9,11,10,1,1,20,1,30])
    x=io.BytesIO();wb.save(x);z=io.BytesIO()
    with zipfile.ZipFile(z,'w') as archive:archive.writestr('synthetic.xlsx',x.getvalue())
    rows=shfe_archive(z.getvalue(),2025)
    assert len(rows)==7
    assert next(r for r in rows if r['metric_id']=='futures_close')['value']==11
    with pytest.raises(ValueError):shfe_archive(z.getvalue(),2024)


def test_asof_report_excludes_later_revision(tmp_path):
    from agri_cta.report import report
    st=Store(tmp_path/'db');seed(st)
    r=row('mpob_stock','P','2026-10-31',100,'tonnes')
    st.ingest('synthetic',[r])
    st.db.execute("UPDATE observation SET collected_at='2026-11-12T06:00:00Z'")
    st.ingest('synthetic',[dict(r,value=200)])
    st.db.execute("UPDATE observation SET collected_at='2026-11-13T06:00:00Z' WHERE vintage=2")
    out=tmp_path/'report'
    result=report(st,out,'2026-11-13T07:50:00+08:00','2026-11-12T07:50:00+08:00')
    import pandas as pd
    ledger=pd.read_csv(out/'evidence_ledger.csv')
    assert result['rows']==1 and ledger.new_value.tolist()==[100]
    assert ledger.evidence_tag.tolist()==['FIRST_SEEN_UNKNOWN_RELEASE']
    report(st,out,'2026-11-13T07:50:00+08:00','2026-11-12T07:50:00+08:00')
    assert pd.read_csv(out/'evidence_ledger.csv').evidence_tag.tolist()==['FIRST_SEEN_UNKNOWN_RELEASE']


def test_fetch_failure_stores_snapshot_no_observations(tmp_path,monkeypatch):
    import requests
    class Response:
        status_code=412
        content=b'synthetic blocked response'
        headers={'Content-Type':'text/plain'}
        def raise_for_status(self):raise requests.HTTPError('synthetic 412')
    monkeypatch.setattr(requests,'get',lambda *a,**kw:Response())
    st=Store(tmp_path)
    with pytest.raises(RuntimeError):st.fetch('TEST','https://example.invalid/blocked')
    assert st.db.execute('SELECT count(*) FROM fetch_attempt').fetchone()[0]==1
    assert st.db.execute('SELECT parse_status FROM source_document').fetchone()[0]=='unverified'
    assert len(list(tmp_path.glob('raw/TEST/*/metadata.json')))==1
    assert st.db.execute('SELECT count(*) FROM observation').fetchone()[0]==0
