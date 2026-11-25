"""Synthetic bars only; no licensed or actual market history in fixtures."""
import json

import pytest

from agri_cta.store import Store
from agri_cta.weekly import parse_bars, collect


def fixture(**overrides):
    records = [dict(d=day, o='100', h='110', l='90', c='102', s='101', v='20', p='30')
               for day in ('2026-11-20', '2026-11-23', '2026-11-24')]
    records[0].update(overrides)
    return ('var _V21052021_4_12=(' + json.dumps(records) + ');').encode()


def test_incomplete_session_excluded_and_provider_separate():
    rows = parse_bars(fixture(), 'SR2701', '2026-11-23')
    assert len(rows) == 8
    assert {r['observation_date'] for r in rows} == {'2026-11-20', '2026-11-23'}
    assert all(r['metric_id'].startswith('sina_') and r['data_status'] == 'provisional' for r in rows)
    assert all(r['published_at'] is None and r['contract_code'] == 'SR2701' for r in rows)


@pytest.mark.parametrize('change', [dict(c='NaN'), dict(s='0'), dict(v='-1'), dict(l='105'), dict(d='2026-11-23')])
def test_bad_document_is_rejected_as_a_whole(change):
    with pytest.raises(ValueError):
        parse_bars(fixture(**change), 'SR2701', '2026-11-23')


def test_no_continuous_contract_or_challenge_page():
    with pytest.raises(ValueError):
        parse_bars(fixture(), 'SR0', '2026-11-23')
    with pytest.raises(ValueError):
        parse_bars(b'<html>security check</html>', 'SR2701', '2026-11-23')


def test_provider_replay_is_idempotent_and_revision_is_retained(tmp_path):
    store = Store(tmp_path)
    for doc in ('first', 'revised'):
        store.db.execute('INSERT INTO source_document VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         [doc, 'SINA_FIXED_CONTRACT', 'https://example.test/synthetic',
                          '2026-11-24T00:00:00Z', doc, 'raw/synthetic.bin', 200,
                          'application/javascript', 'synthetic', 'fetched', None])
    rows = parse_bars(fixture(), 'SR2701', '2026-11-23')
    assert store.ingest('first', rows) == 8
    assert store.ingest('first', rows) == 0
    revised = parse_bars(fixture(s='103'), 'SR2701', '2026-11-23')
    assert store.ingest('revised', revised) == 1
    assert store.db.execute("select list(value order by vintage) from observation where metric_id='sina_futures_settlement' and observation_date='2026-11-20'").fetchone()[0] == [101,103]
    store.db.close()


def test_source_access_denial_stops_subsequent_requests(tmp_path, monkeypatch):
    store = Store(tmp_path)
    calls = []
    def denied(source, url):
        calls.append(url)
        store.db.execute('INSERT INTO fetch_attempt VALUES (?,?,?,?,?,?,?,?)',
                         ['attempt', 'doc', source, url, '2026-11-24T00:00:00Z', 'raw/error.bin', 429, 'rate limit'])
        raise RuntimeError('429 rate limit')
    monkeypatch.setattr(store, 'fetch', denied)
    result = collect(store, '2026-11-23', ['SR2701', 'CF2701'])
    assert len(calls) == 1
    assert result[0]['status'] == 'unverified'
    assert result[1]['status'] == 'skipped_source_unavailable'
    assert store.db.execute('select count(*) from observation').fetchone()[0] == 0
    store.db.close()
