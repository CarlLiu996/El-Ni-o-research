import pandas as pd

from elnino_cta.quality import audit_timeseries


def test_audit_detects_duplicates_and_bad_prices():
    frame = pd.DataFrame({"date": ["2024-01-01", "2024-01-01", "bad"], "close": [1, 0, None]})
    report = audit_timeseries(frame)
    assert report["duplicate_dates"] == 1
    assert report["invalid_dates"] == 1
    assert report["missing_price"] == 1
    assert report["nonpositive_price"] == 1
