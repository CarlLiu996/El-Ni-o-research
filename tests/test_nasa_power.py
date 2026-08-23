from elnino_cta.sources.nasa_power import Location, fetch_daily_agroclimate


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "properties": {"parameter": {
                "PRECTOTCORR": {"20260801": 4.08, "20260802": -999},
                "GWETROOT": {"20260801": 0.70, "20260802": 0.69},
            }}
        }


class FakeSession:
    def get(self, *args, **kwargs):
        assert kwargs["params"]["parameters"] == "PRECTOTCORR,GWETROOT"
        return FakeResponse()


def test_fetch_daily_agroclimate_keeps_rain_and_root_zone_wetness():
    frame = fetch_daily_agroclimate(
        Location("Riau", 0.293, 101.706), "2026-08-01", "2026-08-02", session=FakeSession()
    )
    assert frame["precip_mm"].isna().sum() == 1
    assert frame["root_zone_soil_wetness"].tolist() == [0.70, 0.69]
