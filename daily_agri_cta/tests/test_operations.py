"""Synthetic provider metadata never counted as a real live acceptance artifact."""
from agri_cta.operations import record_scheduled_collection


def test_manual_run_cannot_be_schedule_proof(tmp_path,monkeypatch):
    monkeypatch.setattr('requests.get',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not call provider')))
    r=record_scheduled_collection(tmp_path,dict(sources=[]),{'GITHUB_EVENT_NAME':'workflow_dispatch'})
    assert not r['verified'] and r['status']=='not_scheduled'


def test_provider_event_mismatch_cannot_open_gate(tmp_path,monkeypatch):
    class R:
        def raise_for_status(self):pass
        def json(self):return dict(event='workflow_dispatch',id=123,head_sha='a'*40)
    monkeypatch.setattr('requests.get',lambda *a,**kw:R())
    env=dict(GITHUB_EVENT_NAME='schedule',GITHUB_REPOSITORY='synthetic/repo',GITHUB_RUN_ID='123',GITHUB_SHA='a'*40)
    c=dict(sources=[dict(source='shfe',results=[dict(status='parsed')])])
    r=record_scheduled_collection(tmp_path,c,env)
    assert not r['verified'] and not (tmp_path/'history.duckdb').exists()


def test_no_parsed_exchange_document_cannot_open_gate(tmp_path):
    env=dict(GITHUB_EVENT_NAME='schedule',GITHUB_REPOSITORY='synthetic/repo',GITHUB_RUN_ID='123',GITHUB_SHA='a'*40)
    assert not record_scheduled_collection(tmp_path,dict(sources=[]),env)['verified']
