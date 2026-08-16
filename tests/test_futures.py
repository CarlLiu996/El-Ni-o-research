from elnino_cta.sources.futures import _year_chunks


def test_year_chunks_preserve_requested_boundaries():
    assert _year_chunks("20151115", "20170203") == [
        ("20151115", "20151231"),
        ("20160101", "20161231"),
        ("20170101", "20170203"),
    ]
