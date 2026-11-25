"""Deployment definitions must not silently enable unvalidated schedules or raw uploads."""
from pathlib import Path
import sys
import pytest
import yaml
from agri_cta.cli import main


def test_parallel_workflow_manual_and_private_only():
    path=Path(__file__).resolve().parents[2]/'.github/workflows/agri-cta-parallel.yml'
    text=path.read_text();doc=yaml.safe_load(text);events=doc.get('on',doc.get(True))
    assert set(events)=={'workflow_dispatch'}
    assert 'upload-artifact' not in text
    assert 'refusing transient-only execution' in text
    assert 'delivery-probe' in text


def test_unconfigured_storage_does_not_create_transient_database(tmp_path,monkeypatch):
    monkeypatch.delenv('CTA_S3_BUCKET',raising=False);monkeypatch.delenv('CTA_BUCKET',raising=False)
    monkeypatch.setattr(sys,'argv',['cli','storage-check','--data',str(tmp_path/'data')])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2 and not (tmp_path/'data/history.duckdb').exists()
