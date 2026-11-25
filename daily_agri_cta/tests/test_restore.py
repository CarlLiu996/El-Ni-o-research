"""Synthetic in-memory S3: these tests do not certify actual cloud storage."""
import io,json,sys,tarfile
from types import SimpleNamespace
import pytest
from agri_cta.store import Store,digest
from agri_cta.persistence import sync


class S3:
    class Missing(Exception):pass
    exceptions=SimpleNamespace(NoSuchKey=Missing)
    def __init__(self):self.objects={}
    def get_public_access_block(self,**kw):
        return {'PublicAccessBlockConfiguration':{x:True for x in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']}}
    def put_object(self,**kw):
        assert kw['ServerSideEncryption']=='AES256'
        self.objects[kw['Key']]=kw['Body']
    def get_object(self,**kw):
        if kw['Key'] not in self.objects:raise self.Missing()
        return dict(Body=io.BytesIO(self.objects[kw['Key']]))


def fake(monkeypatch):
    s=S3();monkeypatch.setitem(sys.modules,'boto3',SimpleNamespace(client=lambda _:s));return s


def test_snapshot_restores_database_and_private_raw(monkeypatch,tmp_path):
    fake(monkeypatch);root=tmp_path/'source';st=Store(root);st.db.close()
    (root/'raw').mkdir();(root/'raw/synthetic.txt').write_text('synthetic-only')
    m=sync(root,'synthetic-bucket','cta')
    restored=tmp_path/'restored'
    assert sync(restored,'synthetic-bucket','cta',restore=True)['sha256']==m['sha256']
    assert (restored/'raw/synthetic.txt').read_text()=='synthetic-only'


def test_bad_checksum_does_not_mutate_existing_data(monkeypatch,tmp_path):
    s=fake(monkeypatch);root=tmp_path/'source';st=Store(root);st.db.close()
    m=sync(root,'synthetic-bucket','cta')
    s.objects[m['key']]=b'synthetic-corruption'
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('keep')
    with pytest.raises(ValueError,match='checksum'):sync(target,'synthetic-bucket','cta',restore=True)
    assert (target/'keep').read_text()=='keep'


def test_archive_traversal_rejected_before_replacement(monkeypatch,tmp_path):
    s=fake(monkeypatch);buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as t:
        m=tarfile.TarInfo('../escape');m.size=1;t.addfile(m,io.BytesIO(b'x'))
    blob=buf.getvalue();key='cta/snapshots/synthetic.tar.gz';s.objects[key]=blob
    s.objects['cta/latest.json']=json.dumps(dict(key=key,sha256=digest(blob))).encode()
    target=tmp_path/'existing';target.mkdir();(target/'keep').write_text('keep')
    with pytest.raises(ValueError,match='unsafe'):sync(target,'synthetic-bucket','cta',restore=True)
    assert (target/'keep').read_text()=='keep' and not (tmp_path/'escape').exists()
