"""Synthetic child collectors exercise source isolation, not live market data."""
import json
from types import SimpleNamespace
from pathlib import Path
from agri_cta.orchestrator import collect_daily


def test_one_source_failure_does_not_silently_skip_other_sources(tmp_path,monkeypatch):
    monkeypatch.delenv('GITHUB_EVENT_NAME',raising=False)
    def run(command,stdout,**kwargs):
        if 'watch-risk' in command:
            out=Path(command[command.index('--output')+1]);out.mkdir(parents=True)
            (out/'risk_notice_candidates.json').write_text(json.dumps([dict(status='unverified',error='synthetic')]))
            return SimpleNamespace(returncode=1)
        source=command[command.index('--source')+1]
        stdout.write(json.dumps(dict(source=source,status='unverified' if source=='noaa' else 'parsed'))+'\n')
        stdout.flush()
        return SimpleNamespace(returncode=1 if source=='noaa' else 0)
    monkeypatch.setattr('subprocess.run',run)
    r=collect_daily(tmp_path/'data','2026-11-16','2026-11-17')
    sources={x['source']:x for x in r['sources']}
    assert sources['noaa']['status']=='completed_with_gaps'
    assert sources['shfe']['status']=='parsed'
    assert sources['sdic']['status']=='unverified'
    assert sources['CZCE']['status']=='not_connected_or_authorized_import_required'
    assert not r['all_nine_product_collection_complete'] and not r['scheduler_verification']['verified']
