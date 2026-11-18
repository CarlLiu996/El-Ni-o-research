"""Synthetic provider responses: successful HTTP is not proof of delivery."""
import json
import pytest
from agri_cta.store import Store
from agri_cta.delivery import send_bundle


def test_no_endpoint_no_delivery_attempt(tmp_path,monkeypatch):
    monkeypatch.delenv('CTA_DELIVERY_WEBHOOK_URL',raising=False)
    st=Store(tmp_path)
    assert send_bundle(st,{'run_id':'synthetic'})['status']=='unconfigured'
    assert st.db.execute('SELECT count(*) FROM delivery_attempt').fetchone()[0]==0


def test_shadow_cannot_replace_original(tmp_path):
    st=Store(tmp_path)
    with pytest.raises(ValueError):
        send_bundle(st,dict(run_id='synthetic',mode='parallel',replacement_ready=False),'https://example.invalid')


@pytest.mark.parametrize('response,status,confirmed',[
    ({'status':'accepted'},'accepted_delivery_unknown',False),
    ({'status':'delivered','receipt_id':'synthetic-receipt'},'delivered',True),
    ({'status':'delivered'},'accepted_delivery_unknown',False)])
def test_acceptance_and_delivered_receipt_are_different(tmp_path,monkeypatch,response,status,confirmed):
    class R:
        status_code=200
        content=json.dumps(response).encode()
        def json(self):return response
    monkeypatch.setattr('requests.post',lambda *a,**kw:R())
    st=Store(tmp_path)
    result=send_bundle(st,dict(run_id='synthetic',mode='production',replacement_ready=True),'https://example.invalid')
    assert result['status']==status and result['delivery_confirmed']==confirmed
    assert bool(st.db.execute("SELECT 1 FROM operational_check WHERE kind='delivery_verified'").fetchone())==confirmed


def test_delivery_probe_is_explicit_and_idempotent(tmp_path,monkeypatch):
    calls=[]
    class R:
        status_code=200;content=b'{"status":"delivered","receipt_id":"synthetic"}'
        def json(self):return json.loads(self.content)
    def post(*a,**kw):calls.append(kw);return R()
    monkeypatch.setattr('requests.post',post)
    st=Store(tmp_path);b=dict(kind='delivery_smoke_test',run_id='synthetic-probe')
    assert send_bundle(st,b,'https://example.invalid',probe=True)['delivery_confirmed']
    assert send_bundle(st,b,'https://example.invalid',probe=True)['status']=='already_delivered'
    assert len(calls)==1 and calls[0]['allow_redirects'] is False
