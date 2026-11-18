"""Explicit webhook adapter; HTTP acceptance and delivery confirmation are distinct."""
import json
import os
import uuid
from urllib.parse import urlsplit
import requests
from .store import digest
from .daily import init_state


def send_bundle(store,bundle,endpoint=None,*,probe=False):
    init_state(store)
    endpoint=endpoint or os.environ.get('CTA_DELIVERY_WEBHOOK_URL')
    if not endpoint:return dict(status='unconfigured',delivery_confirmed=False)
    if urlsplit(endpoint).scheme!='https' or urlsplit(endpoint).username:
        raise ValueError('HTTPS endpoint without embedded credentials required')
    if probe and bundle.get('kind')!='delivery_smoke_test':
        raise ValueError('probe must be labelled delivery_smoke_test')
    if not probe and (bundle.get('mode')!='production' or not bundle.get('replacement_ready')):
        raise ValueError('replacement gates not passed; refusing production replacement')
    run_id=bundle['run_id'];attempt=uuid.uuid4().hex
    if not probe:
        known=store.db.execute("SELECT h.sha256 FROM handoff_digest h JOIN daily_run d USING(run_id) WHERE h.run_id=? AND d.mode='production'",[run_id]).fetchone()
        actual=digest(json.dumps(bundle,ensure_ascii=False,default=str,sort_keys=True,separators=(',',':')).encode())
        if not known or known[0]!=actual:
            raise ValueError('unregistered or modified handoff bundle')

    old=store.db.execute("SELECT receipt_id FROM delivery_attempt WHERE run_id=? AND status='delivered' LIMIT 1",[run_id]).fetchone()
    if old:return dict(status='already_delivered',delivery_confirmed=True,receipt_id=old[0])
    try:
        r=requests.post(endpoint,json=bundle,headers={'Idempotency-Key':run_id},timeout=30,allow_redirects=False)
        payload=r.content
        if not 200<=r.status_code<300:raise ValueError(f'provider HTTP {r.status_code}')
        response=r.json();receipt=response.get('receipt_id')
        confirmed=response.get('status')=='delivered' and bool(receipt)
        status='delivered' if confirmed else 'accepted_delivery_unknown'
        store.db.execute('INSERT INTO delivery_attempt VALUES (?,?,?,current_timestamp,?,?,?,NULL)',
          [attempt,run_id,'configured_https_webhook',status,receipt,digest(payload)])
        if confirmed:
            store.db.execute("INSERT INTO operational_check VALUES ('delivery_verified',current_timestamp,'verified',?,?)",
                             [receipt,digest(payload)])
        return dict(status=status,delivery_confirmed=confirmed,receipt_id=receipt)
    except Exception as e:
        store.db.execute('INSERT INTO delivery_attempt VALUES (?,?,?,current_timestamp,?,NULL,NULL,?)',
                         [attempt,run_id,'configured_https_webhook','failed',type(e).__name__])
        return dict(status='failed',delivery_confirmed=False,error=type(e).__name__)
