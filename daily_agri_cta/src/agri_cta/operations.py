"""Verify a scheduled GitHub run through provider metadata, never by a local flag alone."""
import json
import os
import re
from pathlib import Path
import requests


def record_scheduled_collection(data,collection,env=None):
    env=os.environ if env is None else env
    if env.get('GITHUB_EVENT_NAME')!='schedule':
        return dict(status='not_scheduled',verified=False)
    repo=env.get('GITHUB_REPOSITORY','');run=env.get('GITHUB_RUN_ID','');sha=env.get('GITHUB_SHA','')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not run.isdigit() or not re.fullmatch(r'[0-9a-f]{40}',sha):
        return dict(status='unverified_runtime_identity',verified=False)
    if not any(x.get('source')=='shfe' and any(y.get('status')=='parsed' for y in x.get('results',[])) for x in collection['sources']):
        return dict(status='no_parsed_exchange_document',verified=False)
    url=f'https://api.github.com/repos/{repo}/actions/runs/{run}'
    try:
        headers={'Accept':'application/vnd.github+json'}
        if env.get('CTA_GITHUB_ACTIONS_TOKEN'):
            headers['Authorization']='Bearer '+env['CTA_GITHUB_ACTIONS_TOKEN']
        r=requests.get(url,headers=headers,timeout=20);r.raise_for_status();actual=r.json()
        if actual.get('event')!='schedule' or actual.get('head_sha')!=sha or str(actual.get('id'))!=run:
            return dict(status='provider_run_mismatch',verified=False)
        proof=dict(provider='github_actions',run_id=run,head_sha=sha,event='schedule',url=url,
                   scope='scheduled_trigger_and_collector_completed_not_delivery_or_complete_coverage')
        path=Path(data)/'scheduled_collection_proof.json';encoded=json.dumps(proof,indent=2);path.write_text(encoded)
        from .store import Store,digest
        from .daily import init_state
        st=Store(data);init_state(st)
        st.db.execute("INSERT INTO operational_check VALUES ('scheduled_collection',current_timestamp,'verified',?,?)",
                      [url,digest(encoded.encode())])
        st.export();st.db.close()
        return dict(status='verified_scheduled_collector',verified=True,run_id=run)
    except Exception as e:
        return dict(status='unverified_provider_check',verified=False,error=type(e).__name__)
