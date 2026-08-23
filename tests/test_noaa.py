from elnino_cta.sources.noaa import fetch_dmi, fetch_oni, fetch_roni, fetch_soi


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


class FakeTextResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class FakeTextSession:
    def __init__(self, text):
        self.text = text

    def get(self, *args, **kwargs):
        return FakeTextResponse(self.text)


def test_fetch_roni_parses_official_table():
    html = """
    <table id="roni-v5-table2"><tbody>
      <tr><th>Year</th><th>DJF</th><th>JFM</th></tr>
      <tr><th><p>2026</p></th><td><span>-0.9</span></td><td><span>-0.8</span></td></tr>
    </tbody></table>
    """
    frame = fetch_roni(session=FakeTextSession(html))
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-01", "2026-02-01"]
    assert frame["roni"].tolist() == [-0.9, -0.8]


def test_fetch_soi_and_dmi_parse_monthly_rows():
    soi = fetch_soi(session=FakeTextSession(
        "(STAND TAHITI - STAND DARWIN)\nHEADER\n2026 1.8 2.4 2.0 -1.1 -1.5 -2.4\n"
        "(STAND TAHITI - STAND DARWIN)\n2026 0.8 1.4 1.0 -0.1 -0.9 -1.4\n"
    ))
    dmi = fetch_dmi(session=FakeTextSession("1870 2026\n2026 0.10 0.20 0.30 0.40 0.50 0.60\n"))
    assert soi.iloc[-1]["soi"] == -2.4
    assert len(soi) == 6
    assert dmi.iloc[-1]["dmi"] == 0.6
    assert soi.iloc[-1]["date"].strftime("%Y-%m-%d") == "2026-06-01"
