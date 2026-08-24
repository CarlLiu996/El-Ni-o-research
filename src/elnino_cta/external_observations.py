from __future__ import annotations

from typing import Any

import pandas as pd


MONSOON_START = (6, 1)
MONSOON_END = (9, 30)
INDIA_REGION = "india_sugar"
MONSOON_COLUMNS = [
    "date",
    "season_year",
    "season_day",
    "current_cumulative_mm",
    "climatology_cumulative_mm",
    "cumulative_departure_pct",
    "historical_percentile",
    "valid_locations",
    "required_locations",
    "status",
    "data_as_of",
    "published_at",
    "downloaded_at",
    "source",
]


def _timestamp(year: int, month_day: tuple[int, int]) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=month_day[0], day=month_day[1])


def _not_in_season(cutoff: pd.Timestamp, downloaded_at: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "date": cutoff,
            "season_year": cutoff.year,
            "season_day": 0,
            "current_cumulative_mm": pd.NA,
            "climatology_cumulative_mm": pd.NA,
            "cumulative_departure_pct": pd.NA,
            "historical_percentile": pd.NA,
            "valid_locations": 0,
            "required_locations": 2,
            "status": "NOT_IN_SEASON",
            "data_as_of": cutoff,
            "published_at": pd.NA,
            "downloaded_at": downloaded_at,
            "source": "NASA POWER PRECTOTCORR",
        }],
        columns=MONSOON_COLUMNS,
    )


def build_india_monsoon_cumulative(
    weather: pd.DataFrame,
    as_of: str | pd.Timestamp,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    """Build equal-weight India monsoon progress using only complete point histories."""
    required = {"date", "region", "location", "precip_mm", "source"}
    missing = required.difference(weather.columns)
    if missing:
        raise ValueError(f"Missing monsoon input columns: {sorted(missing)}")

    cutoff = pd.Timestamp(as_of).normalize()
    downloaded = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    if cutoff < _timestamp(cutoff.year, MONSOON_START):
        return _not_in_season(cutoff, downloaded)

    daily = weather.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
    daily["precip_mm"] = pd.to_numeric(daily["precip_mm"], errors="coerce")
    daily = daily[
        daily["region"].eq(INDIA_REGION)
        & daily["date"].notna()
        & daily["date"].le(cutoff)
    ].copy()
    if daily.duplicated(["date", "region", "location"]).any():
        raise ValueError("Found duplicate date/region/location observations")

    years = sorted(set(daily["date"].dt.year.tolist()) | {cutoff.year})
    records: list[dict[str, Any]] = []
    for year in years:
        start = _timestamp(year, MONSOON_START)
        end = _timestamp(year, MONSOON_END)
        if year == cutoff.year:
            end = min(end, cutoff)
        if end < start:
            continue
        season = daily[
            daily["date"].between(start, end)
            & daily["date"].dt.month.between(MONSOON_START[0], MONSOON_END[0])
        ]
        dates = pd.date_range(start, end, freq="D")
        cumulative_by_point: dict[str, pd.Series] = {}
        for location, group in season.groupby("location"):
            values = group.set_index("date")["precip_mm"].sort_index().reindex(dates)
            complete = values.notna().cumprod().astype(bool)
            cumulative_by_point[str(location)] = values.fillna(0.0).cumsum().where(complete)
        cumulative = pd.DataFrame(cumulative_by_point, index=dates)
        valid_locations = cumulative.notna().sum(axis=1)
        equal_weight_mean = cumulative.mean(axis=1).where(valid_locations.ge(2))
        for season_day, date in enumerate(dates, start=1):
            records.append({
                "date": date,
                "season_year": year,
                "season_day": season_day,
                "current_cumulative_mm": equal_weight_mean.loc[date],
                "valid_locations": int(valid_locations.loc[date]),
            })

    result = pd.DataFrame(records)
    baseline = result[
        result["season_year"].between(1991, 2020)
        & result["current_cumulative_mm"].notna()
    ].copy()
    baseline["current_cumulative_mm"] = pd.to_numeric(
        baseline["current_cumulative_mm"], errors="coerce"
    )
    normals = baseline.groupby("season_day")["current_cumulative_mm"].mean()
    distributions = baseline.groupby("season_day")["current_cumulative_mm"].apply(list)
    result["climatology_cumulative_mm"] = result["season_day"].map(normals)
    numeric_current = pd.to_numeric(result["current_cumulative_mm"], errors="coerce")
    numeric_normal = pd.to_numeric(result["climatology_cumulative_mm"], errors="coerce")
    result["cumulative_departure_pct"] = numeric_current / numeric_normal - 1.0

    def percentile(row: pd.Series) -> float | Any:
        value = pd.to_numeric(pd.Series([row["current_cumulative_mm"]]), errors="coerce").iloc[0]
        history = pd.to_numeric(
            pd.Series(distributions.get(row["season_day"], [])), errors="coerce"
        ).dropna()
        if pd.isna(value) or history.empty:
            return pd.NA
        return float(history.le(float(value)).mean())

    result["historical_percentile"] = result.apply(percentile, axis=1)
    result["required_locations"] = 2
    result["status"] = result["current_cumulative_mm"].notna().map(
        {True: "OK", False: "INSUFFICIENT_COVERAGE"}
    )
    result["data_as_of"] = cutoff
    result["published_at"] = pd.NA
    result["downloaded_at"] = downloaded
    sources = sorted(str(value) for value in daily["source"].dropna().unique())
    result["source"] = "; ".join(sources) or "NASA POWER PRECTOTCORR"
    return result[MONSOON_COLUMNS].sort_values(["season_year", "season_day"]).reset_index(drop=True)
