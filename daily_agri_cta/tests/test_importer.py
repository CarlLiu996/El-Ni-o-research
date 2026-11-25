"""Synthetic authorized import tests."""
import pytest
from agri_cta.importer import parse_authorized

DATA=b'metric_id,commodity,observation_date,contract_code,price_type,frequency,value,unit,published_at\nstock,P,2025-01-31,,non_price,monthly,10,tonnes,\n'


def test_authorization_gate_and_unknown_release():
    with pytest.raises(PermissionError):parse_authorized(DATA,'.csv')
    assert parse_authorized(DATA,'.csv',True)[0]['published_at'] is None


def test_conflicting_duplicate_rejected():
    b=DATA+b'stock,P,2025-01-31,,non_price,monthly,20,tonnes,\n'
    with pytest.raises(ValueError,match='conflicting duplicate'):parse_authorized(b,'.csv',True)
