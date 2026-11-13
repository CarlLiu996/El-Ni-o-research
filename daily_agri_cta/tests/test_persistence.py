"""Synthetic S3 guard; no external credentials or bucket calls."""
import sys
from types import SimpleNamespace
import pytest
from agri_cta.persistence import sync


def test_public_bucket_is_rejected_before_writing(monkeypatch,tmp_path):
    class Client:
        def get_public_access_block(self,**kw):
            return {'PublicAccessBlockConfiguration':{'BlockPublicAcls':False}}
        def put_object(self,**kw):
            raise AssertionError('must not upload')
    monkeypatch.setitem(sys.modules,'boto3',SimpleNamespace(client=lambda x:Client()))
    with pytest.raises(ValueError,match='Block Public Access'):sync(tmp_path,'synthetic-bucket','test')
