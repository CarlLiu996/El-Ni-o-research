"""Synthetic publication serials and hashes, independent of live data."""
import pytest
from agri_cta.store import Store,digest
from agri_cta.coverage import warehouse_sequence


def add(st,day,serial):
    body=f'synthetic 总第{serial}期'.encode()
    path=st.root/'raw'/f'{day}.bin';path.parent.mkdir(exist_ok=True);path.write_bytes(body)
    st.db.execute("INSERT INTO source_document VALUES (?,?,?,current_timestamp,?,?,200,'text/html','synthetic','parsed',NULL)",
        [day,'SHFE_RECEIPTS','https://example.invalid/dailystock_'+day.replace('-','')+'/ZH/all.html',digest(body),str(path.relative_to(st.root))])


def test_contiguous_sequence_across_weekend_without_fake_daily_rows(tmp_path):
    st=Store(tmp_path);add(st,'2025-01-03',1);add(st,'2025-01-06',2)
    r=warehouse_sequence(st,'2025-01-03','2025-01-06')
    assert r['complete_published_bulletin_sequence'] and r['documents']==2
    assert r['exchange_holiday_classifications']=='not_inferred'


def test_missing_issue_and_missing_boundary_are_not_complete(tmp_path):
    st=Store(tmp_path);add(st,'2025-01-03',1);add(st,'2025-01-06',3)
    r=warehouse_sequence(st,'2025-01-03','2025-01-06')
    assert not r['complete_published_bulletin_sequence'] and r['missing_serials']==[2]
    assert not warehouse_sequence(st,'2025-01-02','2025-01-06')['boundary_dates_present']


def test_tampered_raw_cannot_certify_coverage(tmp_path):
    st=Store(tmp_path);add(st,'2025-01-03',1)
    (tmp_path/'raw/2025-01-03.bin').write_bytes(b'synthetic corrupted')
    with pytest.raises(ValueError,match='checksum'):warehouse_sequence(st,'2025-01-03','2025-01-03')
