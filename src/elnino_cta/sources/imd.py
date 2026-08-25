from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


IMD_DISCOVERY_URLS = (
    "https://mausam.imd.gov.in/imd_latest/contents/press_release.php",
    "https://mausam.imd.gov.in/responsive/monsooninformation.php",
    "https://internal.imd.gov.in/pages/press_release_mausam.php",
)
EVENT_TYPES = ("ONSET_KERALA", "WITHDRAWAL_NORTHWEST_START")
IMD_COLUMNS = [
    "season_year",
    "event_type",
    "official_date",
    "normal_date",
    "departure_days",
    "announcement_date",
    "source_url",
    "normal_source",
    "normal_version",
    "status",
    "review_status",
]
ONSET_PATTERN = re.compile(
    r"southwest\s+monsoon\s+has\s+set\s+in\s+over\s+kerala"
    r"(?:\s+today,?)?\s*(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)[,\s]+(20\d{2})",
    re.IGNORECASE,
)
WITHDRAWAL_PATTERN = re.compile(
    r"withdrawal\s+of\s+(?:the\s+)?southwest\s+monsoon\s+"
    r"(?:commenced|began)\s+from\s+(?:west\s+rajasthan|northwest\s+india)"
    r"(?:\s+and\s+kutch)?\s+(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)[,\s]+(20\d{2})",
    re.IGNORECASE,
)


def extract_imd_text(content: bytes, content_type: str) -> str:
    if "pdf" in content_type.lower() or content.startswith(b"%PDF"):
        reader = PdfReader(BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return BeautifulSoup(content, "html.parser").get_text(" ", strip=True)


def _empty_event(
    source_url: str,
    season_year: int,
    normals: dict[str, object],
    event_type: str | object = pd.NA,
) -> dict[str, object]:
    normal_key = {
        "ONSET_KERALA": "onset_kerala",
        "WITHDRAWAL_NORTHWEST_START": "withdrawal_northwest_start",
    }.get(str(event_type))
    normal_date = (
        pd.Timestamp(f"{season_year}-{normals[normal_key]}") if normal_key else pd.NaT
    )
    return {
        "season_year": season_year,
        "event_type": event_type,
        "official_date": pd.NaT,
        "normal_date": normal_date,
        "departure_days": pd.NA,
        "announcement_date": pd.NaT,
        "source_url": source_url,
        "normal_source": normals["source"],
        "normal_version": normals["version"],
        "status": "NOT_ANNOUNCED",
        "review_status": "NO_EXPLICIT_DECLARATION",
    }


def parse_imd_event(
    text: str,
    source_url: str,
    season_year: int,
    normals: dict[str, object],
    announcement_date: str | pd.Timestamp | None = None,
) -> dict[str, object]:
    normalized = re.sub(r"\s+", " ", text)
    for event_type, pattern, normal_key in (
        ("ONSET_KERALA", ONSET_PATTERN, "onset_kerala"),
        ("WITHDRAWAL_NORTHWEST_START", WITHDRAWAL_PATTERN, "withdrawal_northwest_start"),
    ):
        match = pattern.search(normalized)
        if not match:
            continue
        official = pd.to_datetime(" ".join(match.groups()), dayfirst=True)
        if official.year != season_year:
            continue
        normal = pd.Timestamp(f"{season_year}-{normals[normal_key]}")
        return {
            "season_year": season_year,
            "event_type": event_type,
            "official_date": official,
            "normal_date": normal,
            "departure_days": int((official - normal).days),
            "announcement_date": (
                pd.to_datetime(announcement_date) if announcement_date is not None else pd.NaT
            ),
            "source_url": source_url,
            "normal_source": normals["source"],
            "normal_version": normals["version"],
            "status": "OK",
            "review_status": "AUTO_ACCEPTED",
        }
    return _empty_event(source_url, season_year, normals)


def resolve_imd_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    usable = candidates[candidates["event_type"].notna()].copy()
    rows: list[dict[str, object]] = []
    for _, group in usable.groupby(["season_year", "event_type"], sort=True):
        ordered = group.sort_values(["announcement_date", "source_url"], na_position="last")
        dates = pd.to_datetime(ordered["official_date"], errors="coerce").dropna().unique()
        row = ordered.iloc[-1].to_dict()
        row["source_url"] = "; ".join(sorted(ordered["source_url"].dropna().astype(str).unique()))
        if len(dates) > 1:
            row["official_date"] = pd.NaT
            row["departure_days"] = pd.NA
            row["status"] = "REVIEW_REQUIRED"
            row["review_status"] = "CONFLICTING_OFFICIAL_DATES"
        rows.append(row)
    return pd.DataFrame(rows, columns=IMD_COLUMNS)


def _discovery_links(html: bytes, base_url: str, season_year: int) -> list[str]:
    links: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        row = anchor.find_parent("tr")
        context = row.get_text(" ", strip=True) if row is not None else anchor.get_text(" ", strip=True)
        label = f"{context} {anchor['href']}".lower()
        if str(season_year) not in label or "monsoon" not in label:
            continue
        if not any(keyword in label for keyword in ("onset", "kerala", "withdraw")):
            continue
        if any(keyword in label for keyword in ("forecast", "likely", "outlook")):
            continue
        candidate = urljoin(base_url, anchor["href"])
        hostname = urlparse(candidate).hostname or ""
        if hostname == "imd.gov.in" or hostname.endswith(".imd.gov.in"):
            links.add(candidate)
    return sorted(links)


def fetch_imd_monsoon_events(
    season_year: int,
    normals_path: Path,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> pd.DataFrame:
    normals = json.loads(normals_path.read_text(encoding="utf-8"))
    client = session or requests.Session()
    document_urls: set[str] = set()
    discovery_errors: list[requests.RequestException] = []
    for discovery_url in IMD_DISCOVERY_URLS:
        try:
            response = client.get(discovery_url, timeout=timeout)
            response.raise_for_status()
            document_urls.update(_discovery_links(response.content, response.url, season_year))
        except requests.RequestException as exc:
            discovery_errors.append(exc)
    if len(discovery_errors) == len(IMD_DISCOVERY_URLS):
        raise discovery_errors[-1]

    candidates: list[dict[str, object]] = []
    for source_url in sorted(document_urls):
        try:
            response = client.get(source_url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        announcement_date = response.headers.get("Last-Modified")
        text = extract_imd_text(response.content, response.headers.get("Content-Type", ""))
        parsed = parse_imd_event(
            text,
            source_url=source_url,
            season_year=season_year,
            normals=normals,
            announcement_date=announcement_date,
        )
        if pd.notna(parsed["event_type"]):
            candidates.append(parsed)

    resolved = resolve_imd_candidates(pd.DataFrame(candidates, columns=IMD_COLUMNS))
    present = set(resolved["event_type"].dropna()) if not resolved.empty else set()
    missing = [
        _empty_event("; ".join(IMD_DISCOVERY_URLS), season_year, normals, event_type)
        for event_type in EVENT_TYPES
        if event_type not in present
    ]
    return pd.concat([resolved, pd.DataFrame(missing)], ignore_index=True)[IMD_COLUMNS].sort_values(
        "event_type"
    ).reset_index(drop=True)
