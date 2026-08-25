from __future__ import annotations

import json

import pandas as pd

from elnino_cta.sources.imd import (
    fetch_imd_monsoon_events,
    parse_imd_event,
    resolve_imd_candidates,
)


def normal_dates() -> dict[str, str]:
    return {
        "version": "IMD revised normal dates 2020",
        "source": "https://mausam.imd.gov.in/normal.pdf",
        "onset_kerala": "06-01",
        "withdrawal_northwest_start": "09-17",
    }


def test_parse_imd_onset_rejects_forecast_and_accepts_declaration() -> None:
    forecast = parse_imd_event(
        "Southwest monsoon is likely to set in over Kerala during next 24 hours.",
        source_url="https://mausam.imd.gov.in/forecast.pdf",
        season_year=2026,
        normals=normal_dates(),
    )
    assert forecast["status"] == "NOT_ANNOUNCED"

    declared = parse_imd_event(
        "Southwest monsoon has set in over Kerala today, the 4th June 2026.",
        source_url="https://mausam.imd.gov.in/onset.pdf",
        season_year=2026,
        normals=normal_dates(),
        announcement_date="2026-06-04",
    )
    assert declared["event_type"] == "ONSET_KERALA"
    assert declared["official_date"] == pd.Timestamp("2026-06-04")
    assert declared["departure_days"] == 3
    assert declared["announcement_date"] == pd.Timestamp("2026-06-04")
    assert declared["status"] == "OK"


def test_parse_imd_withdrawal_start_requires_northwest_action() -> None:
    row = parse_imd_event(
        "Withdrawal of southwest monsoon commenced from West Rajasthan on 14th September 2025.",
        source_url="https://mausam.imd.gov.in/withdrawal.pdf",
        season_year=2025,
        normals=normal_dates(),
    )
    unrelated = parse_imd_event(
        "Southwest monsoon has withdrawn from Kerala on 14th September 2025.",
        source_url="https://mausam.imd.gov.in/unrelated.pdf",
        season_year=2025,
        normals=normal_dates(),
    )

    assert row["event_type"] == "WITHDRAWAL_NORTHWEST_START"
    assert row["departure_days"] == -3
    assert unrelated["status"] == "NOT_ANNOUNCED"


def test_conflicting_official_dates_require_review() -> None:
    first = parse_imd_event(
        "Southwest monsoon has set in over Kerala today, the 3rd June 2026.",
        "https://mausam.imd.gov.in/first.pdf",
        2026,
        normal_dates(),
    )
    second = parse_imd_event(
        "Southwest monsoon has set in over Kerala today, the 4th June 2026.",
        "https://mausam.imd.gov.in/second.pdf",
        2026,
        normal_dates(),
    )

    result = resolve_imd_candidates(pd.DataFrame([first, second]))

    assert len(result) == 1
    assert result.iloc[0]["status"] == "REVIEW_REQUIRED"
    assert pd.isna(result.iloc[0]["official_date"])
    assert pd.isna(result.iloc[0]["departure_days"])
    assert "first.pdf" in result.iloc[0]["source_url"]
    assert "second.pdf" in result.iloc[0]["source_url"]


class FakeResponse:
    def __init__(self, body: str, *, url: str, last_modified: str | None = None):
        self.content = body.encode("utf-8")
        self.text = body
        self.url = url
        self.headers = {"Content-Type": "text/html"}
        if last_modified:
            self.headers["Last-Modified"] = last_modified

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.documents = {
            "https://mausam.imd.gov.in/onset-2026.html": FakeResponse(
                "Southwest monsoon has set in over Kerala today, the 4th June 2026.",
                url="https://mausam.imd.gov.in/onset-2026.html",
                last_modified="Thu, 04 Jun 2026 12:00:00 GMT",
            ),
            "https://mausam.imd.gov.in/withdrawal-2026.html": FakeResponse(
                "Withdrawal of southwest monsoon commenced from West Rajasthan on 18th September 2026.",
                url="https://mausam.imd.gov.in/withdrawal-2026.html",
                last_modified="Fri, 18 Sep 2026 12:00:00 GMT",
            ),
        }

    def get(self, url: str, **_: object) -> FakeResponse:
        if url in self.documents:
            return self.documents[url]
        discovery = """
        <html><body>
          <a href="/onset-2026.html">2026 southwest monsoon onset over Kerala</a>
          <a href="/withdrawal-2026.html">2026 southwest monsoon withdrawal</a>
          <a href="/forecast-2026.html">2026 monsoon forecast</a>
        </body></html>
        """
        return FakeResponse(discovery, url=url)


def test_fetch_imd_events_discovers_official_pages_and_returns_unique_rows(tmp_path) -> None:
    normals_path = tmp_path / "normals.json"
    normals_path.write_text(json.dumps(normal_dates()), encoding="utf-8")

    result = fetch_imd_monsoon_events(
        2026,
        normals_path=normals_path,
        session=FakeSession(),
    )

    assert set(result["event_type"]) == {"ONSET_KERALA", "WITHDRAWAL_NORTHWEST_START"}
    assert not result.duplicated(["season_year", "event_type"]).any()
    assert set(result["status"]) == {"OK"}
    assert result["announcement_date"].notna().all()
