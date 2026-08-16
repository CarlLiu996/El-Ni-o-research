from elnino_cta.sources.noaa import fetch_oni


class FakeResponse:
    text = "SEAS YR TOTAL ANOM\nDJF 2024 27.10 1.80\nJFM 2024 27.00 1.50\n"

    def raise_for_status(self):
        return None


class FakeSession:
    def get(self, *args, **kwargs):
        return FakeResponse()


def test_fetch_oni_maps_season_to_middle_month():
    frame = fetch_oni(session=FakeSession())
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-01", "2024-02-01"]
    assert frame["oni"].tolist() == [1.8, 1.5]
