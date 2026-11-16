"""Synthetic HTTP responses exercise command semantics without external network."""
import json
import sys
import requests
import pytest
from agri_cta.cli import main


class Response:
    headers={'Content-Type':'text/plain'}
    def __init__(self,body,status=200):self.content=body;self.status_code=status
    def raise_for_status(self):
        if self.status_code>=400:raise requests.HTTPError(f'synthetic HTTP {self.status_code}')


def invoke(monkeypatch,tmp_path,*args):
    monkeypatch.setattr(sys,'argv',['cli',*args,'--data',str(tmp_path/'data')])
    monkeypatch.setattr('agri_cta.cli.time.sleep',lambda x:None)
    main()


def test_collect_shfe_uses_daily_files_not_annual_download(monkeypatch,tmp_path):
    calls=[]
    price=dict(PRODUCTGROUPID='ru',DELIVERYMONTH='2501',OPENPRICE=10,HIGHESTPRICE=11,LOWESTPRICE=9,CLOSEPRICE=10,SETTLEMENTPRICE=10,VOLUME=1,OPENINTEREST=2)
    body=json.dumps(dict(report_date='20250102',o_curinstrument=[price])).encode()
    warehouse='<p>2025-01-02</p><table><tr><td>天然橡胶</td><td>单位：吨</td></tr><tr><td>总计</td><td>1</td><td>0</td></tr></table>'.encode()
    def get(url,**kwargs):
        calls.append(url)
        return Response(body if '/kx' in url else warehouse)
    monkeypatch.setattr(requests,'get',get)
    invoke(monkeypatch,tmp_path,'collect','--source','shfe','--start','2025-01-02','--end','2025-01-02')
    assert len(calls)==2 and all('download' not in x for x in calls)


def test_batch_stops_after_access_denial(monkeypatch,tmp_path):
    calls=[]
    def get(url,**kwargs):
        calls.append(url);return Response(b'synthetic denied',412)
    monkeypatch.setattr(requests,'get',get)
    with pytest.raises(RuntimeError,match='Batch stopped'):
        invoke(monkeypatch,tmp_path,'backfill','--source','receipts','--start','2025-01-02','--end','2025-01-05')
    assert len(calls)==1
    assert (tmp_path/'data/last_run.json').is_file()


def test_resume_skips_only_verified_saved_snapshots(monkeypatch,tmp_path):
    calls=[]
    warehouse='<p>2025-01-02</p><table><tr><td>天然橡胶</td><td>单位：吨</td></tr><tr><td>总计</td><td>1</td><td>0</td></tr></table>'.encode()
    def get(url,**kwargs):calls.append(url);return Response(warehouse)
    monkeypatch.setattr(requests,'get',get)
    args=('backfill','--source','receipts','--start','2025-01-02','--end','2025-01-02')
    invoke(monkeypatch,tmp_path,*args)
    invoke(monkeypatch,tmp_path,*args,'--resume')
    assert len(calls)==1
    assert json.loads((tmp_path/'data/last_run.json').read_text())[0]['status']=='skipped_verified_snapshot'


def test_failed_risk_site_reports_unverified_not_checked(monkeypatch,tmp_path):
    monkeypatch.setattr(requests,'get',lambda *a,**kw:Response(b'synthetic unavailable',503))
    invoke(monkeypatch,tmp_path,'watch-risk','--output',str(tmp_path/'out'))
    qa=json.loads((tmp_path/'out/risk_qa.json').read_text())
    assert qa['article_failures'] and all(r['checked_articles']==0 for r in qa['coverage'])
    assert qa['conclusion'].startswith('未能取得')
    assert all(r['complete'] is False for r in qa['coverage'])
