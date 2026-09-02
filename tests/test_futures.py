from pathlib import Path

from elnino_cta.sources.futures import _resolve_spec, _year_chunks


def test_year_chunks_preserve_requested_boundaries():
    assert _year_chunks("20151115", "20170203") == [
        ("20151115", "20151231"),
        ("20160101", "20161231"),
        ("20170101", "20170203"),
    ]


def test_new_domestic_contracts_resolve_to_tushare_codes():
    universe = Path("config/commodity_universe.json")

    assert _resolve_spec("CF", universe).continuous_code == "CF.ZCE"
    assert _resolve_spec("C", universe).continuous_code == "C.DCE"
    assert _resolve_spec("M", universe).continuous_code == "M.DCE"
    assert _resolve_spec("Y", universe).continuous_code == "Y.DCE"
    assert _resolve_spec("OI", universe).continuous_code == "OI.ZCE"
    assert _resolve_spec("RM", universe).continuous_code == "RM.ZCE"
