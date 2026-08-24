# ENSO Historical Review Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, no-look-ahead historical El Niño review for palm oil and sugar, add event-aligned price and CTA-proxy evidence to one independent workbook sheet, and clarify the current L2 supply/demand/inventory display without changing CTA allocation advice.

**Architecture:** Source adapters normalize NOAA/NASA/FAOSTAT/World Bank data into CSVs. A standalone `historical_review` module identifies completed ONI events, builds L1—L5 event evidence, aligns price and CTA paths on `T0`, and writes descriptive summaries only. A repository-owned Python workbook builder reads processed files offline and generates the eight visible sheets; the current CTA recommendation and monitoring gate do not import historical outputs.

**Tech Stack:** Python 3.10+, pandas, requests, openpyxl, XlsxWriter, pytest, existing Tushare-derived P/SR files.

**Spec:** `docs/superpowers/specs/2026-08-24-enso-history-and-india-observations-design.md`

## Global Constraints

- Phase 1 covers the historical review and L2 display; live MODIS NDVI and IMD event ingestion are a later plan.
- Completed historical events use at least five consecutive overlapping NOAA ONI seasons at or above `0.5°C`; a trailing open run is `ONGOING` and excluded from completed-event frequencies.
- Event analysis uses only values available within the defined historical window; annual yield comparisons use year-over-year and the prior five-year median, never future-year fitting.
- World Bank monthly prices are research benchmarks without futures carry; monthly CTA results are proxies, not deployable futures backtests.
- Monthly CTA positions execute one month after signal formation, target 20% annual volatility, cap absolute leverage at 2, and charge 2 bps per unit turnover.
- Existing P/SR daily Fast/Medium/Slow outputs remain unchanged and are shown separately for events with coverage.
- `T0` is a retrospective NOAA event anchor and is not assumed knowable in real time; price and CTA paths are descriptive overlays. CTA proxy positions use only their own lagged price signals and never trade on the ex-post ENSO label.
- `00_结论-CTA建议`, `monitoring.py` gate status, and allocation confidence must not read files under `data/processed/history/`.
- Sugar cane crushed multiplied by sugar mix is labeled `制糖甘蔗量代理`, not sugar production, because ATR/recovery data is absent.
- Forecast and actual observations remain separate; source URLs, vintages, units, and limitations are retained.
- No secret, authorization header, credential URL, or user token may appear in code, logs, CSVs, workbook cells, or commits.

---

### Task 1: Add official historical source adapters

**Files:**
- Modify: `pyproject.toml`
- Create: `src/elnino_cta/sources/faostat.py`
- Create: `src/elnino_cta/sources/world_bank.py`
- Create: `tests/test_faostat.py`
- Create: `tests/test_world_bank.py`

**Interfaces:**
- Consumes: HTTP byte payloads from official FAOSTAT bulk download and World Bank Pink Sheet monthly workbook.
- Produces: `fetch_faostat_crop_history() -> pd.DataFrame` and `fetch_world_bank_prices() -> pd.DataFrame` with stable normalized schemas.

- [ ] **Step 1: Add workbook parser and writer dependencies**

Update `pyproject.toml` dependencies to include:

```toml
  "openpyxl>=3.1",
  "XlsxWriter>=3.2",
```

Run: `python -m pip install -e ".[dev]"`

Expected: installation completes and `python -c "import openpyxl, xlsxwriter"` exits 0.

- [ ] **Step 2: Write failing FAOSTAT parser tests**

Create a ZIP fixture in memory with normalized FAOSTAT columns and assert filtering, units, flags, and unique grain:

```python
def test_parse_faostat_crop_history_filters_required_series():
    frame = parse_faostat_crop_history(make_faostat_zip())
    assert set(frame["area"]) == {"India", "Brazil", "Indonesia", "Malaysia"}
    assert set(frame["item"]) == {"Sugar cane", "Oil palm fruit"}
    assert set(frame["element"]) == {"Production", "Area harvested", "Yield"}
    assert not frame.duplicated(["area", "item", "element", "year"]).any()
    assert {"unit", "value", "flag", "source", "downloaded_at"}.issubset(frame.columns)
```

Run: `python -m pytest tests/test_faostat.py -q`

Expected: FAIL because `parse_faostat_crop_history` does not exist.

- [ ] **Step 3: Implement the FAOSTAT adapter**

In `src/elnino_cta/sources/faostat.py`, define:

```python
FAOSTAT_CROPS_URL = (
    "https://bulks-faostat.fao.org/production/"
    "Crops_Livestock_E_All_Data_(Normalized).zip"
)
TARGETS = {
    ("India", "Sugar cane"),
    ("Brazil", "Sugar cane"),
    ("Indonesia", "Oil palm fruit"),
    ("Malaysia", "Oil palm fruit"),
}

def parse_faostat_crop_history(content: bytes, downloaded_at: str | None = None) -> pd.DataFrame:
    with ZipFile(BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one FAOSTAT CSV, found {names}")
        raw = pd.read_csv(archive.open(names[0]), encoding="utf-8-sig", low_memory=False)
    renamed = raw.rename(columns={
        "Area": "area", "Item": "item", "Element": "element", "Year": "year",
        "Unit": "unit", "Value": "value", "Flag": "flag",
    })
    required = {"area", "item", "element", "year", "unit", "value", "flag"}
    if not required.issubset(renamed.columns):
        raise ValueError(f"Unexpected FAOSTAT columns: {list(raw.columns)}")
    keep = renamed.apply(lambda row: (row["area"], row["item"]) in TARGETS, axis=1)
    keep &= renamed["element"].isin(["Production", "Area harvested", "Yield"])
    result = renamed.loc[keep, sorted(required)].copy()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result["year"] = pd.to_numeric(result["year"], errors="raise").astype(int)
    result = result.dropna(subset=["value"])
    result["source"] = FAOSTAT_CROPS_URL
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    keys = ["area", "item", "element", "year"]
    if result.duplicated(keys).any():
        raise ValueError("Duplicate FAOSTAT crop keys")
    present_targets = set(result[["area", "item"]].itertuples(index=False, name=None))
    if present_targets != TARGETS:
        raise ValueError(f"Missing FAOSTAT targets: {sorted(TARGETS - present_targets)}")
    return result.sort_values(keys).reset_index(drop=True)

def fetch_faostat_crop_history(
    timeout: int = 120,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    client = session or requests.Session()
    response = client.get(FAOSTAT_CROPS_URL, timeout=timeout)
    response.raise_for_status()
    return parse_faostat_crop_history(response.content)
```

The parser must read the normalized CSV inside the ZIP, map FAOSTAT column names to lowercase stable names, filter `Production`, `Area harvested`, and `Yield`, retain source flags, reject duplicate keys, and set `source=FAOSTAT_CROPS_URL`. It must raise `ValueError` when required target series are absent.

Run: `python -m pytest tests/test_faostat.py -q`

Expected: PASS.

- [ ] **Step 4: Write failing World Bank parser tests**

Build an in-memory XLSX with a `Monthly Prices` sheet and assert explicit alias mapping:

```python
def test_parse_world_bank_prices_returns_palm_and_sugar_monthly():
    frame = parse_world_bank_prices(make_pink_sheet_xlsx())
    assert set(frame["commodity"]) == {"palm_oil", "sugar"}
    assert list(frame.columns) == [
        "date", "commodity", "price", "unit", "source", "downloaded_at"
    ]
    assert not frame.duplicated(["date", "commodity"]).any()
    assert (frame["price"] > 0).all()
```

Run: `python -m pytest tests/test_world_bank.py -q`

Expected: FAIL because `parse_world_bank_prices` does not exist.

- [ ] **Step 5: Implement the World Bank adapter**

In `src/elnino_cta/sources/world_bank.py`, define:

```python
WORLD_BANK_MONTHLY_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
COMMODITY_ALIASES = {
    "palm_oil": {"Palm oil"},
    "sugar": {"Sugar, world"},
}
COMMODITY_UNITS = {
    "palm_oil": "USD/mt",
    "sugar": "USD/kg",
}

def parse_month(value: object) -> pd.Timestamp:
    match = re.fullmatch(r"(\d{4})M(\d{2})", str(value).strip())
    parsed = pd.Timestamp(year=int(match.group(1)), month=int(match.group(2)), day=1) if match else pd.to_datetime(value, errors="coerce")
    return parsed + pd.offsets.MonthEnd(0) if pd.notna(parsed) else pd.NaT

def parse_world_bank_prices(content: bytes, downloaded_at: str | None = None) -> pd.DataFrame:
    raw = pd.read_excel(BytesIO(content), sheet_name="Monthly Prices", header=None, engine="openpyxl")
    header_matches = raw.index[raw.iloc[:, 0].astype(str).str.strip().eq("Date")].tolist()
    if len(header_matches) != 1:
        raise ValueError("Could not identify World Bank monthly header row")
    header_row = header_matches[0]
    data = raw.iloc[header_row + 1:].copy()
    data.columns = [str(value).strip() for value in raw.iloc[header_row]]
    data = data.rename(columns={data.columns[0]: "date"})
    records = []
    for commodity, aliases in COMMODITY_ALIASES.items():
        matches = [column for column in data.columns if column in aliases]
        if len(matches) != 1:
            raise ValueError(f"Expected one World Bank column for {commodity}, found {matches}")
        subset = data[["date", matches[0]]].rename(columns={matches[0]: "price"})
        subset["commodity"] = commodity
        records.append(subset)
    result = pd.concat(records, ignore_index=True)
    result["date"] = result["date"].map(parse_month)
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    result = result.dropna(subset=["date", "price"])
    result = result[result["price"] > 0]
    result["unit"] = result["commodity"].map(COMMODITY_UNITS)
    result["source"] = WORLD_BANK_MONTHLY_URL
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    if result.duplicated(["date", "commodity"]).any():
        raise ValueError("Duplicate World Bank monthly commodity keys")
    return result[["date", "commodity", "price", "unit", "source", "downloaded_at"]].sort_values(["commodity", "date"]).reset_index(drop=True)

def fetch_world_bank_prices(
    timeout: int = 120,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    client = session or requests.Session()
    response = client.get(WORLD_BANK_MONTHLY_URL, timeout=timeout)
    response.raise_for_status()
    return parse_world_bank_prices(response.content)
```

Read the workbook with `openpyxl`, locate the row containing `1960M01`-style or date values rather than assuming a fixed row, normalize month-end dates, assign the Pink Sheet units explicitly (`USD/mt` for palm oil and `USD/kg` for world sugar), and fail loudly if either commodity is missing.

Run: `python -m pytest tests/test_world_bank.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the source adapters**

```powershell
git add pyproject.toml src/elnino_cta/sources/faostat.py src/elnino_cta/sources/world_bank.py tests/test_faostat.py tests/test_world_bank.py
git commit -m "feat: add FAOSTAT and World Bank history sources"
```

---

### Task 2: Identify ENSO events and build no-look-ahead climate/fundamental evidence

**Files:**
- Create: `src/elnino_cta/historical_review.py`
- Create: `tests/test_historical_review.py`
- Modify: `src/elnino_cta/pipeline.py`

**Interfaces:**
- Consumes: normalized ONI/SOI/DMI, NASA POWER weather, and FAOSTAT crop history frames.
- Produces: `identify_enso_events`, `build_weather_evidence`, `build_yield_evidence`, and `build_event_evidence_panel`.

- [ ] **Step 1: Write failing ONI event-identification tests**

```python
def test_identify_enso_events_requires_five_seasons_and_excludes_open_run():
    oni = synthetic_oni_runs(completed_length=6, short_length=4, open_length=5)
    events = identify_enso_events(oni)
    assert events["status"].tolist() == ["COMPLETED", "ONGOING"]
    assert events.loc[events["status"] == "COMPLETED", "duration_seasons"].iloc[0] == 6
    assert not events["event_id"].duplicated().any()
```

Run: `python -m pytest tests/test_historical_review.py::test_identify_enso_events_requires_five_seasons_and_excludes_open_run -q`

Expected: FAIL because `identify_enso_events` does not exist.

- [ ] **Step 2: Implement event identification**

Define the returned columns exactly:

```python
EVENT_COLUMNS = [
    "event_id", "start_date", "peak_date", "end_date", "peak_oni",
    "duration_seasons", "status", "source"
]

def identify_enso_events(oni: pd.DataFrame, threshold: float = 0.5, min_seasons: int = 5) -> pd.DataFrame:
    ordered = oni.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    ordered["qualifies"] = pd.to_numeric(ordered["oni"], errors="coerce").ge(threshold)
    ordered["run"] = ordered["qualifies"].ne(ordered["qualifies"].shift()).cumsum()
    records = []
    for _, group in ordered[ordered["qualifies"]].groupby("run"):
        if len(group) < min_seasons:
            continue
        peak_index = pd.to_numeric(group["oni"]).idxmax()
        start, end = group.iloc[0], group.iloc[-1]
        status = "ONGOING" if end["date"] == ordered.iloc[-1]["date"] else "COMPLETED"
        records.append({
            "event_id": f"{start['date'].year}-{end['date'].year}",
            "start_date": start["date"], "peak_date": ordered.loc[peak_index, "date"],
            "end_date": end["date"], "peak_oni": float(ordered.loc[peak_index, "oni"]),
            "duration_seasons": len(group), "status": status, "source": start["source"],
        })
    return pd.DataFrame(records, columns=EVENT_COLUMNS)
```

Group consecutive qualifying rows after sorting and de-duplicating dates. A qualifying run ending on the last ONI observation is `ONGOING`; all other qualifying runs are `COMPLETED`. The peak is the first date of the maximum ONI value, which makes ties deterministic.

Run the single test again.

Expected: PASS.

- [ ] **Step 3: Write failing weather and yield evidence tests**

```python
def test_yield_evidence_uses_only_prior_five_years():
    evidence = build_yield_evidence(events(), faostat_with_future_outlier())
    row = evidence.query("event_id == '2009-2010' and year_offset == 0").iloc[0]
    assert row["baseline_end_year"] == 2008
    assert row["baseline_years"] == 5
    assert row["deviation_vs_prior5_median"] == pytest.approx(0.10)

def test_weather_evidence_keeps_region_and_event_window_grain():
    evidence = build_weather_evidence(events(), nasa_daily_history())
    assert not evidence.duplicated(["event_id", "region"]).any()
    assert {"precip_anomaly_pct", "soil_wetness_anomaly", "coverage_pct"}.issubset(evidence)
```

Run: `python -m pytest tests/test_historical_review.py -q`

Expected: FAIL because the evidence builders do not exist.

- [ ] **Step 4: Implement climate and fundamental evidence builders**

Add exact interfaces:

```python
def build_weather_evidence(
    events: pd.DataFrame,
    weather: pd.DataFrame,
    climatology_start: int = 1991,
    climatology_end: int = 2020,
) -> pd.DataFrame:
    region_commodity = {
        "malaysia_palm": "palm_oil", "indonesia_palm": "palm_oil",
        "india_sugar": "sugar", "brazil_cs_sugar": "sugar",
    }
    daily = weather.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    climatology = daily[daily["date"].dt.year.between(climatology_start, climatology_end)].copy()
    climatology["month"] = climatology["date"].dt.month
    normals = climatology.groupby(["region", "month"]).agg(
        precip_normal=("precip_mm", "mean"), soil_normal=("root_zone_soil_wetness", "mean")
    ).reset_index()
    rows = []
    for event in events.itertuples(index=False):
        end = pd.Timestamp(event.start_date) + pd.DateOffset(months=12)
        window = daily[daily["date"].between(event.start_date, end)].copy()
        window["month"] = window["date"].dt.month
        joined = window.merge(normals, on=["region", "month"], how="left")
        for region, group in joined.groupby("region"):
            actual_rain = group["precip_mm"].sum(min_count=1)
            normal_rain = group["precip_normal"].sum(min_count=1)
            rows.append({
                "event_id": event.event_id, "status": event.status,
                "commodity": region_commodity[region], "region": region,
                "precip_anomaly_pct": actual_rain / normal_rain - 1 if normal_rain > 0 else pd.NA,
                "soil_wetness_anomaly": (group["root_zone_soil_wetness"] - group["soil_normal"]).mean(),
                "coverage_pct": group["precip_mm"].notna().mean(),
                "source": group["source"].iloc[0],
            })
    return pd.DataFrame(rows)

def build_yield_evidence(events: pd.DataFrame, crops: pd.DataFrame) -> pd.DataFrame:
    item_commodity = {"Oil palm fruit": "palm_oil", "Sugar cane": "sugar"}
    rows = []
    for event in events.itertuples(index=False):
        start_year = pd.Timestamp(event.start_date).year
        for year_offset in (0, 1):
            year = start_year + year_offset
            for keys, group in crops.groupby(["area", "item", "element"]):
                values = group.set_index("year")["value"].sort_index()
                if year not in values.index:
                    continue
                prior = values.loc[(values.index >= year - 5) & (values.index <= year - 1)]
                previous = values.get(year - 1, pd.NA)
                value = float(values.loc[year])
                source_row = group.loc[group["year"].eq(year)].iloc[0]
                rows.append({
                    "event_id": event.event_id, "status": event.status,
                    "commodity": item_commodity[keys[1]],
                    "year_offset": year_offset, "year": year,
                    "area": keys[0], "item": keys[1], "element": keys[2], "value": value,
                    "yoy": value / previous - 1 if pd.notna(previous) and previous != 0 else pd.NA,
                    "deviation_vs_prior5_median": value / prior.median() - 1 if len(prior) == 5 and prior.median() != 0 else pd.NA,
                    "baseline_years": len(prior), "baseline_end_year": year - 1,
                    "calendar_crop_year_caveat": True,
                    "source": source_row["source"], "source_flag": source_row["flag"],
                })
    return pd.DataFrame(rows)

def build_event_evidence_panel(
    events: pd.DataFrame,
    weather_evidence: pd.DataFrame,
    yield_evidence: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for item in weather_evidence.itertuples(index=False):
        for field, unit in (("precip_anomaly_pct", "ratio"), ("soil_wetness_anomaly", "index")):
            rows.append({
                "event_id": item.event_id, "status": item.status,
                "commodity": item.commodity, "layer": "L1",
                "metric_id": f"weather:{item.region}:{field}",
                "value": getattr(item, field), "unit": unit,
                "coverage_pct": item.coverage_pct, "source": item.source,
                "source_flag": pd.NA,
                "limitation": "T0至T+12月；按1991-2020同月均值计算",
            })
    for item in yield_evidence.itertuples(index=False):
        for field in ("yoy", "deviation_vs_prior5_median"):
            rows.append({
                "event_id": item.event_id, "status": item.status,
                "commodity": item.commodity, "layer": "L2",
                "metric_id": f"crop:{item.area}:{item.item}:{item.element}:y{item.year_offset}:{field}",
                "value": getattr(item, field), "unit": "ratio",
                "coverage_pct": 1.0 if item.baseline_years == 5 else item.baseline_years / 5,
                "source": item.source,
                "source_flag": item.source_flag,
                "limitation": "FAOSTAT日历年口径；不等同作物年度",
            })
    columns = [
        "event_id", "status", "commodity", "layer", "metric_id",
        "value", "unit", "coverage_pct", "source", "source_flag", "limitation",
    ]
    return pd.DataFrame(rows, columns=columns)
```

Weather evidence uses `T0` through `T+12` months and same-calendar-month 1991—2020 means. Yield evidence stores event start year and following year separately as `year_offset=0` and `year_offset=1`, with YoY and deviation from the prior five-year median. The combined panel is long-form with one unique `event_id × commodity × metric_id` row. Preserve FAOSTAT flags and set `calendar_crop_year_caveat=True` for every annual crop observation.

Run: `python -m pytest tests/test_historical_review.py -q`

Expected: PASS.

- [ ] **Step 5: Extend the historical climate refresh boundary**

Add a helper to `pipeline.py` that calls existing NOAA and NASA adapters without invoking futures refresh:

```python
def refresh_historical_climate(
    output_dir: Path,
    start: str,
    end: str,
    regions_path: Path,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    climate_dir = output_dir / "climate"
    climate_dir.mkdir(parents=True, exist_ok=True)
    frames = {"oni": fetch_oni(), "soi": fetch_soi(), "dmi": fetch_dmi()}
    for name, frame in frames.items():
        frame.to_csv(climate_dir / f"noaa_{name}.csv", index=False)
    regions = json.loads(regions_path.read_text(encoding="utf-8"))
    weather = []
    for region, items in regions.items():
        for item in items:
            frame = fetch_daily_agroclimate(Location(**item), start, end)
            frame["region"] = region
            weather.append(frame)
    combined = pd.concat(weather, ignore_index=True)
    combined.to_csv(climate_dir / "nasa_power_precipitation.csv", index=False)
    return {
        "start": start, "end": end,
        "rows": {**{name: len(frame) for name, frame in frames.items()}, "weather": len(combined)},
        "coverage": {
            **{
                name: {"min_date": str(pd.to_datetime(frame["date"]).min().date()), "max_date": str(pd.to_datetime(frame["date"]).max().date())}
                for name, frame in frames.items()
            },
            "weather": {"min_date": str(pd.to_datetime(combined["date"]).min().date()), "max_date": str(pd.to_datetime(combined["date"]).max().date())},
        },
    }
```

It must write complete ONI/SOI/DMI history and NASA POWER data from 1981-01-01 for all configured regions, and return row counts and date coverage for the manifest.

Run: `python -m pytest tests/test_noaa.py tests/test_nasa_power.py tests/test_historical_review.py -q`

Expected: PASS.

- [ ] **Step 6: Commit event and evidence logic**

```powershell
git add src/elnino_cta/historical_review.py src/elnino_cta/pipeline.py tests/test_historical_review.py
git commit -m "feat: build no-look-ahead ENSO event evidence"
```

---

### Task 3: Build event-aligned price paths and monthly CTA proxies

**Files:**
- Modify: `src/elnino_cta/historical_review.py`
- Modify: `tests/test_historical_review.py`

**Interfaces:**
- Consumes: completed/ongoing events and normalized World Bank monthly prices.
- Produces: `build_event_price_paths`, `build_monthly_cta_proxy`, `build_event_cta_paths`, and `summarize_event_cta`.

- [ ] **Step 1: Write failing price-path alignment tests**

```python
def test_event_price_paths_are_t0_normalized_and_span_minus6_plus18():
    paths = build_event_price_paths(events(), monthly_prices())
    one = paths.query("event_id == '1997-1998' and commodity == 'palm_oil'")
    assert one["relative_month"].min() == -6
    assert one["relative_month"].max() == 18
    assert one.loc[one["relative_month"] == 0, "price_index"].iloc[0] == pytest.approx(100.0)
```

Run: `python -m pytest tests/test_historical_review.py::test_event_price_paths_are_t0_normalized_and_span_minus6_plus18 -q`

Expected: FAIL because `build_event_price_paths` does not exist.

- [ ] **Step 2: Implement event-aligned price paths**

```python
def build_event_price_paths(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    before_months: int = 6,
    after_months: int = 18,
) -> pd.DataFrame:
    records = []
    for event in events.itertuples(index=False):
        t0 = pd.Timestamp(event.start_date) + pd.offsets.MonthEnd(0)
        for commodity, group in prices.groupby("commodity"):
            indexed = group.set_index(pd.to_datetime(group["date"])).sort_index()
            if t0 not in indexed.index:
                continue
            base = float(indexed.loc[t0, "price"])
            for relative_month in range(-before_months, after_months + 1):
                date = t0 + pd.DateOffset(months=relative_month) + pd.offsets.MonthEnd(0)
                if date not in indexed.index:
                    continue
                price = float(indexed.loc[date, "price"])
                records.append({
                    "event_id": event.event_id, "status": event.status,
                    "commodity": commodity, "date": date, "relative_month": relative_month,
                    "price": price, "price_index": price / base * 100,
                    "source": indexed.loc[date, "source"],
                })
    return pd.DataFrame(records)
```

Return `event_id`, `status`, `commodity`, `date`, `relative_month`, `price`, `price_index`, `source`. Require a valid `T0` price; leave incomplete future months absent for `ONGOING` events.

Run the price-path test again.

Expected: PASS.

- [ ] **Step 3: Write failing monthly CTA tests**

```python
@pytest.mark.parametrize("speed,windows,vol_window", [
    ("medium", (3, 6), 3),
    ("slow", (6, 12), 6),
])
def test_monthly_cta_is_lagged_scaled_and_costed(speed, windows, vol_window):
    result = build_monthly_cta_proxy(monthly_prices_one_asset(), speed)
    assert result.loc[0, "position"] == 0
    assert (result["position"].abs() <= 2.0).all()
    assert result.loc[result["turnover"] > 0, "cost"].gt(0).all()
    expected = (1 + result["net_return"]).cumprod()
    pd.testing.assert_series_equal(result["equity"], expected, check_names=False)
```

Run: `python -m pytest tests/test_historical_review.py::test_monthly_cta_is_lagged_scaled_and_costed -q`

Expected: FAIL because `build_monthly_cta_proxy` does not exist.

- [ ] **Step 4: Implement monthly CTA and event summaries**

```python
MONTHLY_LOOKBACKS = {
    "medium": ((3, 6), 3),
    "slow": ((6, 12), 6),
}

def build_monthly_cta_proxy(
    prices: pd.DataFrame,
    speed: str,
    annual_vol_target: float = 0.20,
    max_leverage: float = 2.0,
    cost_bps_per_turnover: float = 2.0,
) -> pd.DataFrame:
    if speed not in MONTHLY_LOOKBACKS:
        raise ValueError(f"speed must be one of {sorted(MONTHLY_LOOKBACKS)}")
    windows, vol_window = MONTHLY_LOOKBACKS[speed]
    result = prices[["date", "price"]].sort_values("date").drop_duplicates("date").copy()
    result["return"] = result["price"].pct_change(fill_method=None)
    directions = [result["price"].pct_change(window, fill_method=None).apply(lambda value: 1 if value > 0 else -1 if value < 0 else 0) for window in windows]
    result["signal"] = sum(directions) / len(directions)
    result["realized_vol"] = result["return"].rolling(vol_window).std() * math.sqrt(12)
    result["target_position"] = (result["signal"] * annual_vol_target / result["realized_vol"]).clip(-max_leverage, max_leverage)
    result["position"] = result["target_position"].shift(1).fillna(0.0)
    result["turnover"] = result["position"].diff().abs().fillna(result["position"].abs())
    result["gross_return"] = result["position"] * result["return"].fillna(0.0)
    result["cost"] = result["turnover"] * cost_bps_per_turnover / 10_000
    result["net_return"] = result["gross_return"] - result["cost"]
    result["equity"] = (1 + result["net_return"]).cumprod()
    result["speed"] = speed
    return result

def build_event_cta_paths(events: pd.DataFrame, cta: pd.DataFrame) -> pd.DataFrame:
    records = []
    ordered = cta.copy()
    ordered["date"] = pd.to_datetime(ordered["date"]) + pd.offsets.MonthEnd(0)
    for event in events.itertuples(index=False):
        t0 = pd.Timestamp(event.start_date) + pd.offsets.MonthEnd(0)
        for _, series in ordered.groupby(["commodity", "speed"]):
            window = series[series["date"].between(t0 - pd.DateOffset(months=6), t0 + pd.DateOffset(months=18))].sort_values("date").copy()
            if window.empty:
                continue
            window["event_id"] = event.event_id
            window["status"] = event.status
            window["relative_month"] = (window["date"].dt.year - t0.year) * 12 + window["date"].dt.month - t0.month
            window["event_equity"] = (1 + window["net_return"]).cumprod()
            records.append(window)
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()

def summarize_event_cta(paths: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, group in paths.groupby(["event_id", "commodity", "speed"]):
        for horizon in (3, 6, 12, 18):
            window = group[group["relative_month"].between(0, horizon)].sort_values("date")
            if window.empty:
                continue
            equity = (1 + window["net_return"]).cumprod()
            rows.append({
                "event_id": keys[0], "status": window["status"].iloc[0],
                "commodity": keys[1], "speed": keys[2],
                "horizon_months": horizon, "net_return": equity.iloc[-1] - 1,
                "max_drawdown": (equity / equity.cummax() - 1).min(),
                "positive_month_share": window["net_return"].gt(0).mean(),
                "average_abs_position": window["position"].abs().mean(),
                "turnover": window["turnover"].sum(),
            })
    return pd.DataFrame(rows)
```

Use `sqrt(12)` for annualization, average the two momentum directions, shift target position by one month, and calculate costs on absolute position change. Summary windows are 0—3, 0—6, 0—12, and 0—18 months with compounded net return, maximum drawdown, positive-month share, average absolute position, and total turnover.

Run: `python -m pytest tests/test_historical_review.py -q`

Expected: PASS.

- [ ] **Step 5: Add daily P/SR event-window summaries without changing daily signals**

Add:

```python
def summarize_existing_daily_cta(
    events: pd.DataFrame,
    signal_frames: dict[tuple[str, str], pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for event in events.itertuples(index=False):
        start = pd.Timestamp(event.start_date)
        end = start + pd.DateOffset(months=18)
        for (symbol, speed), frame in signal_frames.items():
            window = frame[pd.to_datetime(frame["date"]).between(start, end)].copy()
            if window.empty:
                rows.append({"event_id": event.event_id, "symbol": symbol, "speed": speed, "coverage_status": "UNAVAILABLE"})
                continue
            equity = (1 + window["net_return"]).cumprod()
            rows.append({
                "event_id": event.event_id, "symbol": symbol, "speed": speed,
                "coverage_status": "AVAILABLE", "net_return": equity.iloc[-1] - 1,
                "max_drawdown": (equity / equity.cummax() - 1).min(),
                "start_date": window["date"].iloc[0], "end_date": window["date"].iloc[-1],
            })
    return pd.DataFrame(rows)
```

Only summarize dates already present in the existing signal CSVs. Do not rebuild or splice dominant contracts. Return missing coverage explicitly for events before 2015.

Add a test asserting pre-2015 rows have `coverage_status="UNAVAILABLE"` and 2015+ rows compound existing `net_return` exactly.

Run: `python -m pytest tests/test_historical_review.py -q`

Expected: PASS.

- [ ] **Step 6: Commit price and CTA review logic**

```powershell
git add src/elnino_cta/historical_review.py tests/test_historical_review.py
git commit -m "feat: add event price and CTA proxy paths"
```

---

### Task 4: Produce descriptive historical patterns and L2 six-cell summaries

**Files:**
- Create: `src/elnino_cta/fundamental_summary.py`
- Modify: `src/elnino_cta/historical_review.py`
- Create: `tests/test_fundamental_summary.py`
- Modify: `tests/test_historical_review.py`

**Interfaces:**
- Consumes: event evidence, event price/CTA summaries, and existing fundamental CSVs.
- Produces: descriptive pattern tables and exactly six current L2 category rows.

- [ ] **Step 1: Write failing historical-pattern isolation tests**

```python
def test_pattern_labels_respect_minimum_sample_and_emit_no_allocation_fields():
    patterns = summarize_patterns(event_results())
    assert set(patterns["pattern_strength"]) <= {
        "STABLE_HISTORY", "LIMITED_SAMPLE", "INSUFFICIENT_EVIDENCE"
    }
    assert not any("allocation" in c or "confidence_adjustment" in c for c in patterns.columns)
```

Run: `python -m pytest tests/test_historical_review.py::test_pattern_labels_respect_minimum_sample_and_emit_no_allocation_fields -q`

Expected: FAIL because `summarize_patterns` does not exist.

- [ ] **Step 2: Implement descriptive pattern summaries**

```python
def make_pattern_input(
    evidence: pd.DataFrame,
    price_paths: pd.DataFrame,
    cta_summary: pd.DataFrame,
) -> pd.DataFrame:
    required = ["event_id", "status", "commodity", "metric_id", "value"]
    rows = [evidence.loc[evidence["status"].eq("COMPLETED"), required].copy()]
    completed_prices = price_paths[price_paths["status"].eq("COMPLETED")]
    for (event_id, commodity), group in completed_prices.groupby(["event_id", "commodity"]):
        indexed = group.set_index("relative_month")["price_index"]
        for horizon in (3, 6, 12, 18):
            if 0 in indexed.index and horizon in indexed.index:
                rows.append(pd.DataFrame([{
                    "event_id": event_id, "status": "COMPLETED", "commodity": commodity,
                    "metric_id": f"price_return_{horizon}m",
                    "value": float(indexed.loc[horizon] / indexed.loc[0] - 1),
                }]))
    completed_cta = cta_summary[cta_summary["status"].eq("COMPLETED")].copy()
    completed_cta["metric_id"] = (
        "cta_" + completed_cta["speed"] + "_" +
        completed_cta["horizon_months"].astype(str) + "m_net_return"
    )
    completed_cta = completed_cta.rename(columns={"net_return": "value"})
    rows.append(completed_cta[required])
    result = pd.concat(rows, ignore_index=True)
    return result.dropna(subset=["value"]).drop_duplicates(
        ["event_id", "commodity", "metric_id"], keep="last"
    )

def summarize_patterns(event_results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, group in event_results.groupby(["commodity", "metric_id"]):
        values = pd.to_numeric(group["value"], errors="coerce").dropna()
        count = len(values)
        positive_share = values.gt(0).mean() if count else pd.NA
        same_direction_share = max(positive_share, 1 - positive_share) if count else pd.NA
        strength = (
            "STABLE_HISTORY" if count >= 4 and same_direction_share >= 0.70
            else "LIMITED_SAMPLE" if count == 3
            else "INSUFFICIENT_EVIDENCE"
        )
        rows.append({
            "commodity": keys[0], "metric_id": keys[1], "sample_size": count,
            "positive_count": int(values.gt(0).sum()), "negative_count": int(values.lt(0).sum()),
            "flat_count": int(values.eq(0).sum()), "same_direction_share": same_direction_share,
            "median": values.median() if count else pd.NA,
            "q25": values.quantile(0.25) if count else pd.NA,
            "q75": values.quantile(0.75) if count else pd.NA,
            "pattern_strength": strength,
        })
    return pd.DataFrame(rows)
```

`make_pattern_input` converts climate/fundamental evidence, 3/6/12/18-month price returns, and Medium/Slow CTA cost-after returns to the same long-form grain and excludes `ONGOING` events. Group palm oil and sugar separately. Emit sample size, positive/negative/flat counts, same-direction share, median, interquartile range, and the label rules from the spec. Do not create action, allocation, recommended-position, or confidence-adjustment columns.

Run the pattern test again.

Expected: PASS.

- [ ] **Step 3: Write failing six-row L2 summary tests**

```python
def test_build_l2_summary_has_two_commodities_times_three_categories():
    summary = build_l2_summary(fundamental_frames(), as_of="2026-08-23")
    assert list(summary[["commodity", "category"]].itertuples(index=False, name=None)) == [
        ("palm_oil", "supply"), ("palm_oil", "demand"), ("palm_oil", "inventory"),
        ("sugar", "supply"), ("sugar", "demand"), ("sugar", "inventory"),
    ]
    assert summary["primary_value"].notna().all()
    assert set(summary["value_type"]) <= {"actual", "forecast"}

def test_sugar_cane_for_sugar_proxy_is_not_named_sugar_output():
    details = build_l2_details(fundamental_frames())
    row = details.query("metric_id == 'brazil_cane_for_sugar_proxy'").iloc[0]
    assert "甘蔗" in row["label"]
    assert "食糖产量" not in row["label"]
```

Run: `python -m pytest tests/test_fundamental_summary.py -q`

Expected: FAIL because the summary module does not exist.

- [ ] **Step 4: Implement the L2 summary module**

Define:

```python
METRIC_META = {
    "mpob_production": ("马来西亚棕榈油月度供给", "高位偏松，低位偏紧", "单一国家月度口径"),
    "ffb_yield": ("单位面积鲜果串产出，解释供给效率", "高位偏松，低位偏紧", "MPOB历史回填，非点时数据"),
    "global_palm_stock_use": ("全球期末库存相对消费的缓冲", "高位偏松，低位偏紧", "USDA年度估计/预测"),
    "mpob_exports": ("马来西亚棕榈油外需代理", "高位偏紧，低位偏松", "未覆盖印尼出口"),
    "mpob_inventory": ("马来西亚月末现货缓冲", "高位偏松，低位偏紧", "不等同全球库存"),
    "india_sugar_output": ("印度食糖供给兑现", "高位偏松，低位偏紧", "月度流量，需结合榨季累计"),
    "unica_crush": ("巴西中南部甘蔗压榨进度", "高位通常偏松", "甘蔗可分流至乙醇"),
    "unica_sugar_mix": ("巴西中南部甘蔗用于制糖的比例", "高位通常偏松", "本身不是产糖量"),
    "global_sugar_total_use_source": ("全球食糖消费需求", "高位偏紧，低位偏松", "USDA年度低频口径"),
    "global_sugar_ending_stocks_source": ("全球食糖期末库存缓冲", "高位偏松，低位偏紧", "USDA年度低频口径"),
    "global_sugar_stock_use": ("全球食糖期末库存相对消费", "高位偏松，低位偏紧", "综合结果，不替代库存与消费"),
    "global_palm_total_use": ("全球棕榈油消费需求", "高位偏紧，低位偏松", "USDA年度估计/预测"),
    "global_palm_ending_stocks": ("全球棕榈油期末库存缓冲", "高位偏松，低位偏紧", "USDA年度估计/预测"),
    "brazil_cane_for_sugar_proxy": ("压榨量乘制糖比，代理制糖甘蔗投入", "高位通常偏松", "缺少ATR/出糖率，非食糖产量"),
}

def percentile_label(value: float | None) -> str:
    if pd.isna(value):
        return "不可比较"
    return "低位" if value <= 1 / 3 else "高位" if value >= 2 / 3 else "中位"

def build_l2_details(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    def classify_value_type(row: pd.Series) -> str:
        explicit = str(row.get("value_type", "")).strip().lower()
        if explicit in {"actual", "forecast"}:
            return explicit
        note = str(row.get("note", "")).lower()
        if any(word in note for word in ("forecast", "projection", "estimate")):
            return "forecast"
        observed = pd.Timestamp(row["date"])
        published = pd.Timestamp(row["published_at"])
        return "forecast" if observed > published else "actual"

    details = []
    for dataset, frame in frames.items():
        ordered = frame.sort_values("date").copy()
        latest = ordered.iloc[-1]
        details.append({
            "metric_id": dataset, "label": latest.get("indicator_name", dataset),
            "date": latest["date"], "value": float(latest["value"]),
            "unit": latest.get("unit"), "source": latest.get("source"),
            "published_at": latest.get("published_at"),
            "value_type": classify_value_type(latest),
            "seasonal_percentile": ordered.loc[
                pd.to_datetime(ordered["date"]).dt.month.eq(pd.Timestamp(latest["date"]).month), "value"
            ].astype(float).le(float(latest["value"])).mean(),
        })
    palm_balance = frames["global_palm_stock_use"].sort_values("date").copy()
    palm_latest = palm_balance.iloc[-1]
    for metric_id, column, label in (
        ("global_palm_total_use", "total_use_1000t", "全球棕榈油消费量"),
        ("global_palm_ending_stocks", "ending_stocks_1000t", "全球棕榈油期末库存"),
    ):
        seasonal = palm_balance.loc[
            pd.to_datetime(palm_balance["date"]).dt.month.eq(pd.Timestamp(palm_latest["date"]).month), column
        ].astype(float)
        details.append({
            "metric_id": metric_id, "label": label,
            "date": palm_latest["date"], "value": float(palm_latest[column]),
            "unit": "1000 tonnes", "source": palm_latest["source"],
            "published_at": palm_latest["published_at"],
            "value_type": classify_value_type(palm_latest),
            "seasonal_percentile": seasonal.le(float(palm_latest[column])).mean(),
        })
    crush = frames["unica_crush"].sort_values("date").copy()
    mix = frames["unica_sugar_mix"].sort_values("date").copy()
    aligned = pd.merge_asof(crush, mix, on="date", direction="backward", suffixes=("_crush", "_mix"))
    aligned["proxy_value"] = aligned["value_crush"] * aligned["value_mix"] / 100
    latest = aligned.dropna(subset=["value_mix"]).iloc[-1]
    proxy_history = aligned.dropna(subset=["proxy_value"])
    proxy_season = proxy_history.loc[
        pd.to_datetime(proxy_history["date"]).dt.month.eq(pd.Timestamp(latest["date"]).month), "proxy_value"
    ].astype(float)
    details.append({
        "metric_id": "brazil_cane_for_sugar_proxy",
        "label": "巴西中南部制糖甘蔗量代理",
        "date": latest["date"], "value": float(latest["proxy_value"]),
        "unit": "tonnes cane allocated to sugar", "source": "UNICA crush × sugar mix",
        "published_at": max(pd.Timestamp(latest["published_at_crush"]), pd.Timestamp(latest["published_at_mix"])),
        "value_type": "actual",
        "seasonal_percentile": proxy_season.le(float(latest["proxy_value"])).mean(),
    })
    result = pd.DataFrame(details)
    result[["meaning", "direction_rule", "limitation"]] = result["metric_id"].map(METRIC_META).apply(pd.Series)
    result["current_position"] = result["seasonal_percentile"].map(percentile_label)
    result["position_meaning"] = result.apply(
        lambda row: f"{row['current_position']}；{row['direction_rule']}" if row["current_position"] != "不可比较" else f"不可比较；{row['limitation']}",
        axis=1,
    )
    return result

def build_l2_summary(
    frames: dict[str, pd.DataFrame],
    as_of: str | pd.Timestamp,
) -> pd.DataFrame:
    details = build_l2_details(frames).set_index("metric_id")
    definitions = [
        ("palm_oil", "supply", "mpob_production", ["mpob_production"], ["ffb_yield"]),
        ("palm_oil", "demand", "mpob_exports", ["mpob_exports"], ["global_palm_total_use"]),
        ("palm_oil", "inventory", "mpob_inventory", ["mpob_inventory"], ["global_palm_ending_stocks", "global_palm_stock_use"]),
        ("sugar", "supply", "india_sugar_output", ["india_sugar_output", "brazil_cane_for_sugar_proxy"], ["brazil_cane_for_sugar_proxy"]),
        ("sugar", "demand", "global_sugar_total_use_source", ["global_sugar_total_use_source"], []),
        ("sugar", "inventory", "global_sugar_ending_stocks_source", ["global_sugar_ending_stocks_source"], ["global_sugar_stock_use"]),
    ]
    rows = []
    for commodity, category, primary, scoring_metrics, drivers in definitions:
        item = details.loc[primary]
        percentile = item["seasonal_percentile"]
        value_type = item["value_type"]
        scoring = details.loc[scoring_metrics]
        if scoring["value_type"].ne("actual").any() or scoring["seasonal_percentile"].isna().any():
            direction = "INSUFFICIENT"
        else:
            component_directions = []
            for component_percentile in scoring["seasonal_percentile"]:
                if category == "demand":
                    component = "TIGHT" if component_percentile >= 2 / 3 else "LOOSE" if component_percentile <= 1 / 3 else "NEUTRAL"
                else:
                    component = "TIGHT" if component_percentile <= 1 / 3 else "LOOSE" if component_percentile >= 2 / 3 else "NEUTRAL"
                component_directions.append(component)
            direction = component_directions[0] if len(set(component_directions)) == 1 else "NEUTRAL"
        rows.append({
            "commodity": commodity, "category": category, "primary_metric": primary,
            "primary_value": item["value"], "unit": item["unit"], "date": item["date"],
            "published_at": item["published_at"], "drivers": ",".join(drivers),
            "scoring_metrics": ",".join(scoring_metrics),
            "value_type": value_type, "driver_value_types": ",".join(details.loc[driver, "value_type"] for driver in drivers),
            "seasonal_percentile": percentile, "direction": direction,
            "as_of": pd.Timestamp(as_of),
        })
    return pd.DataFrame(rows)
```

Use `pd.merge_asof` to align UNICA sugar mix to cumulative crush without looking forward. Compute `brazil_cane_for_sugar_proxy = crush_tonnes * sugar_mix_pct / 100`. Extract `total_use_1000t` and `ending_stocks_1000t` from the palm stock-use file as distinct detail rows; the sugar source files are already distinct consumption and ending-stock series. Determine forecast status from an explicit field first, then forecast/projection/estimate notes, then whether the observation period ends after its publication date. The category direction must be `TIGHT`, `NEUTRAL`, `LOOSE`, or `INSUFFICIENT`. Palm-oil categories and sugar demand/inventory score only their primary metric; sugar supply jointly scores actual India output and the Brazil cane-for-sugar proxy, returning `NEUTRAL` if they disagree. If any scoring metric is forecast or lacks a percentile, return `INSUFFICIENT`. Non-scoring forecast background drivers remain visible in `driver_value_types` but do not overwrite an actual primary metric's category.

Run: `python -m pytest tests/test_fundamental_summary.py -q`

Expected: PASS.

- [ ] **Step 5: Commit pattern and L2 summary logic**

```powershell
git add src/elnino_cta/historical_review.py src/elnino_cta/fundamental_summary.py tests/test_historical_review.py tests/test_fundamental_summary.py
git commit -m "feat: summarize ENSO patterns and L2 fundamentals"
```

---

### Task 5: Add a history-refresh command and write processed outputs

**Files:**
- Modify: `src/elnino_cta/cli.py`
- Modify: `src/elnino_cta/pipeline.py`
- Create: `tests/test_history_pipeline.py`
- Modify: `docs/DATA_SOURCES.md`

**Interfaces:**
- Consumes: official source adapters and existing processed P/SR signal files.
- Produces: eight `data/processed/history/*.csv` files and a source manifest without changing the current monitor snapshot.

- [ ] **Step 1: Write a failing end-to-end pipeline test with injected source frames**

```python
def test_refresh_history_writes_expected_outputs_without_touching_monitor(tmp_path):
    monitor = tmp_path / "reports" / "monitoring" / "latest.json"
    monitor.parent.mkdir(parents=True)
    monitor.write_text('{"sentinel": true}', encoding="utf-8")
    result = refresh_history(
        output_dir=tmp_path / "data" / "processed",
        as_of="2026-08-23",
        source_bundle=synthetic_source_bundle(),
    )
    assert set(result["files"]) == EXPECTED_HISTORY_FILES
    assert monitor.read_text(encoding="utf-8") == '{"sentinel": true}'
```

Run: `python -m pytest tests/test_history_pipeline.py -q`

Expected: FAIL because `refresh_history` does not exist.

- [ ] **Step 2: Implement output orchestration**

Add to `pipeline.py`:

```python
HISTORY_FILES = {
    "events": "enso_events.csv",
    "evidence": "event_evidence_panel.csv",
    "price_paths": "event_price_paths.csv",
    "cta_paths": "event_cta_paths.csv",
    "cta_summary": "event_cta_summary.csv",
    "daily_cta_summary": "existing_daily_cta_summary.csv",
    "patterns": "historical_patterns.csv",
    "current_context": "current_event_context.csv",
}

def refresh_history(
    output_dir: Path,
    as_of: str,
    source_bundle: dict[str, object] | None = None,
    regions_path: Path = Path("config/regions.json"),
) -> dict[str, object]:
    signal_dir = output_dir / "signals"
    daily_signals = {}
    for symbol in ("P", "SR"):
        for speed in ("fast", "medium", "slow"):
            path = signal_dir / f"{symbol}_{speed}_proxy_research.csv"
            if path.exists():
                daily_signals[(symbol, speed)] = pd.read_csv(path)
    if source_bundle is None:
        refresh_historical_climate(
            output_dir=output_dir, start="1981-01-01", end=as_of,
            regions_path=regions_path,
        )
        bundle = {
            "oni": pd.read_csv(output_dir / "climate" / "noaa_oni.csv"),
            "soi": pd.read_csv(output_dir / "climate" / "noaa_soi.csv"),
            "dmi": pd.read_csv(output_dir / "climate" / "noaa_dmi.csv"),
            "weather": pd.read_csv(output_dir / "climate" / "nasa_power_precipitation.csv"),
            "crops": fetch_faostat_crop_history(),
            "prices": fetch_world_bank_prices(),
            "daily_signals": daily_signals,
        }
    else:
        bundle = source_bundle
    events = identify_enso_events(bundle["oni"])
    weather = build_weather_evidence(events, bundle["weather"])
    yields = build_yield_evidence(events, bundle["crops"])
    evidence = build_event_evidence_panel(events, weather, yields)
    price_paths = build_event_price_paths(events, bundle["prices"])
    cta_frames = []
    for commodity, prices in bundle["prices"].groupby("commodity"):
        for speed in ("medium", "slow"):
            frame = build_monthly_cta_proxy(prices, speed)
            frame["commodity"] = commodity
            cta_frames.append(frame)
    cta_paths = build_event_cta_paths(events, pd.concat(cta_frames, ignore_index=True))
    cta_summary = summarize_event_cta(cta_paths)
    daily_cta_summary = summarize_existing_daily_cta(events, bundle.get("daily_signals", {}))
    patterns = summarize_patterns(make_pattern_input(evidence, price_paths, cta_summary))
    current_context = evidence[evidence["status"].eq("ONGOING")].copy()
    history_dir = output_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "events": events, "evidence": evidence, "price_paths": price_paths,
        "cta_paths": cta_paths, "cta_summary": cta_summary,
        "daily_cta_summary": daily_cta_summary,
        "patterns": patterns, "current_context": current_context,
    }
    files = {}
    for key, frame in outputs.items():
        target = history_dir / HISTORY_FILES[key]
        temporary = target.with_suffix(".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(target)
        files[key] = str(target)
    artifacts = {}
    for key, frame in outputs.items():
        target = Path(files[key])
        date_columns = [column for column in ("date", "start_date", "peak_date") if column in frame.columns]
        dates = pd.concat([pd.to_datetime(frame[column], errors="coerce") for column in date_columns]).dropna() if date_columns else pd.Series(dtype="datetime64[ns]")
        sources = sorted({
            str(value) for column in frame.columns if column == "source"
            for value in frame[column].dropna().unique()
        })
        artifacts[key] = {
            "path": str(target), "rows": len(frame), "sources": sources,
            "min_date": dates.min().date().isoformat() if not dates.empty else None,
            "max_date": dates.max().date().isoformat() if not dates.empty else None,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    downloaded_at = pd.Timestamp.now(tz="UTC").isoformat()
    manifest = {
        "as_of": as_of, "downloaded_at": downloaded_at, "files": files,
        "rows": {key: len(frame) for key, frame in outputs.items()}, "artifacts": artifacts,
    }
    (history_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
```

With `source_bundle=None`, fetch official sources and read current signal CSVs from `output_dir/signals`. With injected frames, perform no network calls. Write atomically through a temporary file followed by `Path.replace`. Add source URLs, row counts, min/max dates, download times, and file SHA-256 to `data/processed/history/manifest.json`.

Run: `python -m pytest tests/test_history_pipeline.py -q`

Expected: PASS.

- [ ] **Step 3: Add the CLI command**

Extend `cli.py`:

```python
history_parser = sub.add_parser("history-refresh", help="Build independent ENSO history review data")
history_parser.add_argument("--output", default="data/processed")
history_parser.add_argument("--as-of", required=True)
```

Dispatch with `refresh_history(Path(args.output), args.as_of)` and print the manifest as JSON. Do not call `monitor` or `debrief` from this command.

Run: `python -m pytest tests/test_history_pipeline.py -q`

Expected: PASS.

- [ ] **Step 4: Document source and interpretation boundaries**

In `docs/DATA_SOURCES.md`, add official URLs, product names, coverage, units, revision behavior, FAOSTAT flag retention, World Bank spot-price limitation, and the statement that historical output is not imported by the current CTA gate.

Run: `python -m compileall -q src`

Expected: exit 0.

- [ ] **Step 5: Commit the history pipeline**

```powershell
git add src/elnino_cta/cli.py src/elnino_cta/pipeline.py tests/test_history_pipeline.py docs/DATA_SOURCES.md
git commit -m "feat: add independent ENSO history refresh"
```

---

### Task 6: Generate the eight-sheet workbook with history and six-row L2 summary

**Files:**
- Create: `scripts/build_research_workbook.py`
- Create: `tests/test_research_workbook.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: current monitor/fundamental/market/signal CSVs plus `data/processed/history/*.csv`.
- Produces: `research/厄尔尼诺农产品CTA研究框架_八页复盘版_YYYYMMDD.xlsx`.

- [ ] **Step 1: Write failing workbook-structure tests**

```python
EXPECTED_SHEETS = [
    "00_结论-CTA建议", "01_总逻辑", "02_H_历次厄尔尼诺复盘",
    "03_L1气候天气", "04_L2供给基本面", "05_L3市场定价",
    "06_L4_CTA环境", "07_L5数据审计",
]

def test_workbook_has_eight_ordered_sheets_and_l2_six_rows(tmp_path):
    path = build_research_workbook(fixture_repo(), tmp_path, "2026-08-23")
    wb = load_workbook(path, data_only=False, read_only=False)
    assert wb.sheetnames == EXPECTED_SHEETS
    ws = wb["04_L2供给基本面"]
    categories = [ws.cell(row=r, column=2).value for r in range(11, 17)]
    assert categories == ["供给", "需求", "库存", "供给", "需求", "库存"]
```

Run: `python -m pytest tests/test_research_workbook.py -q`

Expected: FAIL because the workbook builder does not exist.

- [ ] **Step 2: Implement reusable workbook formatting and eight sheets**

In `scripts/build_research_workbook.py`, define:

```python
def build_research_workbook(repo_dir: Path, output_dir: Path, as_of: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"厄尔尼诺农产品CTA研究框架_八页复盘版_{as_of.replace('-', '')}.xlsx"
    frames = load_workbook_inputs(repo_dir)
    with xlsxwriter.Workbook(output_path) as workbook:
        formats = create_formats(workbook)
        add_current_conclusion_sheet(workbook, frames, formats)
        add_logic_sheet(workbook, frames, formats)
        add_history_sheet(workbook, frames, formats)
        add_climate_sheet(workbook, frames, formats)
        add_l2_sheet(
            workbook, frames["l2_summary"], frames["l2_details"],
            frames["fundamentals"], formats,
        )
        add_market_sheet(workbook, frames, formats)
        add_cta_sheet(workbook, frames, formats)
        add_audit_sheet(workbook, frames, formats)
    validate_workbook(output_path)
    return output_path

def add_history_sheet(workbook, frames: dict[str, pd.DataFrame], formats: dict[str, object]) -> None:
    sheet = workbook.add_worksheet("02_H_历次厄尔尼诺复盘")
    write_title(sheet, "02｜历次厄尔尼诺复盘", "独立研究旁路；不进入当前CTA配置结论", formats)
    write_event_summary_table(
        sheet, frames["events"], frames["evidence"], frames["price_paths"],
        frames["cta_summary"], frames["daily_cta_summary"], formats,
    )
    write_event_path_data_and_sparklines(sheet, frames["price_paths"], frames["cta_paths"], formats)
    write_pattern_table(sheet, frames["patterns"], formats)
    sheet.freeze_panes(10, 0)

def add_l2_sheet(
    workbook,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    fundamental_history: dict[str, pd.DataFrame],
    formats: dict[str, object],
) -> None:
    sheet = workbook.add_worksheet("04_L2供给基本面")
    write_title(sheet, "04｜L2 供给基本面", "顶部六类结论；下方保留全部驱动和来源", formats)
    write_l2_summary_rows(sheet, summary, start_row=9, formats=formats)
    write_l2_detail_rows(
        sheet, details, fundamental_history, start_row=19, formats=formats
    )
    sheet.freeze_panes(10, 0)
```

Implement the called helpers with these exact interfaces and boundaries:

```python
def load_csv_directory(path: Path) -> dict[str, pd.DataFrame]:
    return {item.stem: pd.read_csv(item) for item in sorted(path.glob("*.csv"))}

def load_workbook_inputs(repo_dir: Path) -> dict[str, object]:
    history = load_csv_directory(repo_dir / "data" / "processed" / "history")
    fundamentals = load_csv_directory(repo_dir / "data" / "processed" / "fundamentals")
    climate = load_csv_directory(repo_dir / "data" / "processed" / "climate")
    market = load_csv_directory(repo_dir / "data" / "processed" / "market")
    signals = load_csv_directory(repo_dir / "data" / "processed" / "signals")
    monitor_path = repo_dir / "reports" / "monitoring" / "latest.json"
    monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (repo_dir / "data" / "processed" / "history" / "manifest.json").read_text(encoding="utf-8")
    )
    return {
        "monitor": monitor, "fundamentals": fundamentals, "climate": climate,
        "market": market, "signals": signals, "history_manifest": manifest,
        "events": history["enso_events"],
        "evidence": history["event_evidence_panel"],
        "price_paths": history["event_price_paths"],
        "cta_paths": history["event_cta_paths"],
        "cta_summary": history["event_cta_summary"],
        "daily_cta_summary": history["existing_daily_cta_summary"],
        "patterns": history["historical_patterns"],
        "l2_details": build_l2_details(fundamentals),
        "l2_summary": build_l2_summary(fundamentals, manifest["as_of"]),
    }

def create_formats(workbook) -> dict[str, object]:
    return {
        "title": workbook.add_format({"bold": True, "font_size": 18, "font_color": "#FFFFFF", "bg_color": "#17365D"}),
        "subtitle": workbook.add_format({"font_color": "#666666", "italic": True}),
        "header": workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#4472C4", "border": 1}),
        "body": workbook.add_format({"border": 1, "valign": "top"}),
        "percent": workbook.add_format({"border": 1, "num_format": "0.0%"}),
        "date": workbook.add_format({"border": 1, "num_format": "yyyy-mm-dd"}),
        "note": workbook.add_format({"font_color": "#7F6000", "bg_color": "#FFF2CC", "text_wrap": True}),
    }

def write_title(sheet, title: str, subtitle: str, formats: dict[str, object]) -> None:
    sheet.merge_range(0, 0, 0, 11, title, formats["title"])
    sheet.merge_range(1, 0, 1, 11, subtitle, formats["subtitle"])

def validate_workbook(path: Path) -> None:
    forbidden = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "Authorization", "Bearer ")
    token_pattern = re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    if workbook.sheetnames != EXPECTED_SHEETS:
        raise ValueError(f"Unexpected sheets: {workbook.sheetnames}")
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                text = "" if cell.value is None else str(cell.value)
                if any(item in text for item in forbidden) or token_pattern.search(text):
                    raise ValueError(f"Invalid workbook content at {sheet.title}!{cell.coordinate}")
```

Also implement these writer interfaces; each writer owns only its named sheet or table and never mutates input frames:

- `add_current_conclusion_sheet(workbook, frames, formats)`: read only `frames["monitor"]`; it must not access any history key.
- `add_logic_sheet(workbook, frames, formats)`: show `L1 → L2 → L3 → L4 → L5`, plus an explicit side branch `H 历史复盘（仅描述，不进入配置）`.
- `add_climate_sheet`, `add_market_sheet`, `add_cta_sheet`, and `add_audit_sheet`: preserve the existing indicators, sources, dates, meanings, positions, and limitations while consolidating them into the named eight-sheet layout.
- `write_event_summary_table(sheet, events, evidence, price_paths, cta_summary, daily_cta_summary, formats)`: one visible row per `event_id × commodity`, with event dates, peak ONI, climate/fundamental evidence coverage, 3/6/12/18-month price returns, Medium/Slow monthly CTA returns and drawdowns, plus available P/SR Fast/Medium/Slow daily-proxy results or explicit `UNAVAILABLE` coverage.
- `write_event_path_data_and_sparklines(sheet, price_paths, cta_paths, formats)`: write ordered `relative_month=-6..18` source ranges starting at column `AA`, hide those columns, and add visible price-index and CTA-equity sparklines.
- `write_pattern_table(sheet, patterns, formats)`: show sample size, direction counts/share, median, quartiles, strength, and limitation; omit allocation fields.
- `write_l2_summary_rows(sheet, summary, start_row, formats)`: write exactly six rows in the approved palm-oil/sugar and supply/demand/inventory order.
- `write_l2_detail_rows(sheet, details, fundamental_history, start_row, formats)`: show metric, meaning, value, unit, observation date, publication date, actual/forecast, percentile/current-position meaning, source, and limitation. Write each metric's chronologically ordered historical values to hidden columns starting at `BA` and add one row sparkline where at least two observations exist; derived palm-balance rows use their source columns, and the Brazil proxy uses an as-of merged crush/mix history.

Use `xlsxwriter.Workbook`. Reproduce the current title, section, table, conditional-format, freeze-pane, and width conventions. Keep `00_结论-CTA建议` content sourced only from the current monitor snapshot. Add a note in `01_总逻辑` that history is an independent research branch not connected to allocation logic.

Run the workbook structure test again.

Expected: PASS.

- [ ] **Step 3: Add history paths, CTA returns, and sparklines**

For every event/commodity row in `02_H_历次厄尔尼诺复盘`, write hidden-below-view source ranges containing `relative_month=-6..18`, `price_index`, Medium equity, and Slow equity. Add at least two visible sparklines per row: price path and CTA equity path. The visible summary columns must include 0—3/6/12/18-month commodity returns, CTA net returns, maximum drawdown, sample coverage, and limitation.

Add test assertions that the sheet XML contains `<x14:sparklineGroup` and that visible headings include `价格路径`, `CTA代理净值`, `商品收益`, and `CTA成本后收益`.

Run: `python -m pytest tests/test_research_workbook.py -q`

Expected: PASS.

- [ ] **Step 4: Add L2 summary and detail presentation**

Write six summary rows at the top of `04_L2供给基本面`, followed by the full driver table. Show actual/forecast status in a dedicated column. Display global sugar consumption and ending stocks separately, then stock-use ratio as a detail row. Label the Brazil metric exactly `巴西中南部制糖甘蔗量代理` and show `压榨量 × 制糖比；非食糖产量` in its meaning column.

Add test assertions for those labels and that a forecast scoring metric never carries a `TIGHT` or `LOOSE` category. Forecast background drivers must remain visible in the detail table and `driver_value_types` without changing an actual primary metric's category.

Run: `python -m pytest tests/test_research_workbook.py tests/test_fundamental_summary.py -q`

Expected: PASS.

- [ ] **Step 5: Scan formulas and workbook content for errors and secrets**

After closing the workbook, reopen with `openpyxl` and inspect all formulas/strings. Fail the build on `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`, `Authorization`, `Bearer `, or a JWT-like `eyJ` token prefix.

Add a test that injects `Bearer secret` into a fixture cell and asserts the validator rejects it.

Run: `python -m pytest tests/test_research_workbook.py -q`

Expected: PASS.

- [ ] **Step 6: Document workbook generation**

Add to `README.md`:

```powershell
elnino-cta history-refresh --as-of 2026-08-23
python scripts/build_research_workbook.py --as-of 2026-08-23
```

State that the history sheet is descriptive and excluded from the current CTA allocation gate.

- [ ] **Step 7: Commit the workbook builder**

```powershell
git add scripts/build_research_workbook.py tests/test_research_workbook.py README.md
git commit -m "feat: add ENSO history review workbook"
```

---

### Task 7: Run live refresh, validate research quality, and publish the latest workbook

**Files:**
- Modify: `data/processed/history/*.csv`
- Create: `research/厄尔尼诺农产品CTA研究框架_八页复盘版_20260824.xlsx`
- Modify: `.github/workflows/monitor.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: all completed implementation tasks and authorized network access.
- Produces: live historical CSVs, a validated workbook, CI refresh steps, and a concise evidence-backed pattern summary for user review.

- [ ] **Step 1: Run all unit tests before live data**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Refresh official historical data**

Run:

```powershell
elnino-cta history-refresh --as-of 2026-08-24
```

Expected: `data/processed/history/manifest.json` reports non-zero completed events, both World Bank commodities, four FAOSTAT country/crop pairs, and no duplicate primary keys.

- [ ] **Step 3: Run explicit data-quality checks**

Run a repository script or pytest integration test that asserts:

```python
assert events.query("status == 'COMPLETED'").shape[0] >= 4
assert not evidence.duplicated(["event_id", "commodity", "metric_id"]).any()
assert set(price_paths["relative_month"]).issubset(set(range(-6, 19)))
assert (price_paths["price"] > 0).all()
assert not cta_paths.duplicated(["event_id", "commodity", "speed", "date"]).any()
assert not any(c in current_monitor_json for c in ["historical_patterns", "event_cta_summary"])
```

Expected: exit 0; any failure blocks workbook publication.

- [ ] **Step 4: Generate and inspect the workbook**

Run:

```powershell
python scripts/build_research_workbook.py --as-of 2026-08-24
python -m pytest tests/test_research_workbook.py -q
```

Expected: the dated workbook exists, contains exactly eight visible sheets, has sparklines on the history and indicator pages, and passes the formula/secret scan.

- [ ] **Step 5: Visually inspect the four decision pages**

Render or open these ranges and check clipping, alignment, conditional colors, and readable chart scales:

- `00_结论-CTA建议` top conclusion and action block;
- `02_H_历次厄尔尼诺复盘` event table plus price/CTA sparklines;
- `04_L2供给基本面` six-row summary plus details;
- `07_L5数据审计` source/freshness/limitations.

Expected: no clipped headers, overlapping charts, unreadable rows, or hidden limitations.

- [ ] **Step 6: Add CI history refresh without Earthdata dependency**

Update `.github/workflows/monitor.yml` to run `elnino-cta history-refresh --as-of "$(date -u +%F)"` and the workbook builder after the existing monitor step. Do not reference `EARTHDATA_TOKEN` in Phase 1. Add `data/processed/history` and the dated workbook pattern to the commit step.

Run a YAML parse check and `python -m pytest -q`.

Expected: both pass.

- [ ] **Step 7: Write the user-facing review summary**

Update `README.md` with the latest workbook filename and record only observed patterns that meet the sample thresholds. Separate palm oil and sugar, state sample sizes, medians, ranges, and missing coverage. Do not add an allocation recommendation or confidence adjustment.

- [ ] **Step 8: Commit final live artifacts**

```powershell
git add data/processed/history research/厄尔尼诺农产品CTA研究框架_八页复盘版_20260824.xlsx .github/workflows/monitor.yml README.md
git commit -m "data: publish ENSO historical review"
```

- [ ] **Step 9: Final verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q src scripts
git status --short
```

Expected: tests and compilation pass; `git status --short` shows only the two pre-existing unrelated untracked user files, which remain untouched.
