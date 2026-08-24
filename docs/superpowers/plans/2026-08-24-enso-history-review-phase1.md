# ENSO 历史复盘一期实施计划

> **供执行代理使用：** 必须使用子技能 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施本计划。各步骤使用复选框（`- [ ]`）跟踪进度。

**目标：** 为棕榈油和白糖建立可复现、无未来函数的厄尔尼诺历史复盘；在独立工作表中加入按事件对齐的价格与 CTA 代理证据；理顺当前 L2 供给、需求、库存展示，但不改动 CTA 配置建议。

**架构：** 数据源适配器把 NOAA、NASA、FAOSTAT 和世界银行数据标准化为 CSV。独立的 `historical_review` 模块识别已结束的 ONI 事件，构建 L1—L5 事件证据，以 `T0` 对齐价格与 CTA 路径，并且只输出描述性总结。仓库内置的 Python 工作簿生成器离线读取处理后文件，生成八个可见工作表；当前 CTA 建议和监控门槛不导入历史复盘输出。

**技术栈：** Python 3.10+、pandas、requests、openpyxl、XlsxWriter、pytest，以及现有由 Tushare 生成的 P/SR 文件。

**设计文档：** `docs/superpowers/specs/2026-08-24-enso-history-and-india-observations-design.md`

## 全局约束

- 一期只覆盖历史复盘和 L2 展示；实时 MODIS NDVI 与 IMD 事件数据接入留到后续计划。
- 已结束历史事件的定义为：NOAA ONI 至少连续五个重叠季节达到或超过 `0.5°C`；样本末尾尚未结束的连续区间标为 `ONGOING`，不计入已结束事件频率。
- 事件分析只使用规定历史窗口内可获得的数值；年度单产比较使用同比和此前五年中位数，禁止用未来年份拟合。
- 世界银行月度价格只作为不含期货期限结构的研究基准；月度 CTA 结果是代理，不是可直接交易的期货回测。
- 月度 CTA 仓位在信号形成后一个月执行，年化目标波动率统一为 20%，绝对杠杆上限为 2 倍，每单位换手收取 2 个基点成本。
- 现有 P/SR 日频 Fast、Medium、Slow 输出保持不变，并在有覆盖的事件中单独展示。
- `T0` 是事后识别的 NOAA 事件锚点，不假设实时可知；价格与 CTA 路径只是描述性叠加。CTA 代理仓位只使用自身滞后价格信号，绝不依据事后 ENSO 标签交易。
- `00_结论-CTA建议`、`monitoring.py` 的门槛状态及配置置信度不得读取 `data/processed/history/` 下的文件。
- 甘蔗压榨量乘以制糖比必须标为 `制糖甘蔗量代理`，不能称为食糖产量，因为缺少 ATR/出糖率数据。
- 预测值与实际观测值必须分开；保留数据源 URL、数据版本、单位和局限性。
- 代码、日志、CSV、工作簿单元格和提交中均不得出现密钥、授权头、含凭据 URL 或用户令牌。

---

### 任务 1：增加官方历史数据源适配器

**文件：**
- 修改：`pyproject.toml`
- 新建：`src/elnino_cta/sources/faostat.py`
- 新建：`src/elnino_cta/sources/world_bank.py`
- 新建：`tests/test_faostat.py`
- 新建：`tests/test_world_bank.py`

**接口：**
- 输入：FAOSTAT 官方批量下载和世界银行 Pink Sheet 月度工作簿的 HTTP 字节内容。
- 输出：具有稳定标准化结构的 `fetch_faostat_crop_history() -> pd.DataFrame` 和 `fetch_world_bank_prices() -> pd.DataFrame`。

- [ ] **步骤 1：增加工作簿解析和写入依赖**

在 `pyproject.toml` 的依赖中加入：

```toml
  "openpyxl>=3.1",
  "XlsxWriter>=3.2",
```

运行：`python -m pip install -e ".[dev]"`

预期：安装完成，且 `python -c "import openpyxl, xlsxwriter"` 退出码为 0。

- [ ] **步骤 2：编写预期失败的 FAOSTAT 解析测试**

在内存中创建包含标准 FAOSTAT 字段的 ZIP 测试数据，并断言筛选结果、单位、标记和唯一数据粒度：

```python
def test_parse_faostat_crop_history_filters_required_series():
    frame = parse_faostat_crop_history(make_faostat_zip())
    assert set(frame["area"]) == {"India", "Brazil", "Indonesia", "Malaysia"}
    assert set(frame["item"]) == {"Sugar cane", "Oil palm fruit"}
    assert set(frame["element"]) == {"Production", "Area harvested", "Yield"}
    assert not frame.duplicated(["area", "item", "element", "year"]).any()
    assert {"unit", "value", "flag", "source", "downloaded_at"}.issubset(frame.columns)
```

运行：`python -m pytest tests/test_faostat.py -q`

预期：失败，因为 `parse_faostat_crop_history` 尚不存在。

- [ ] **步骤 3：实现 FAOSTAT 适配器**

在 `src/elnino_cta/sources/faostat.py` 中定义：

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

解析器必须读取 ZIP 中的标准化 CSV，把 FAOSTAT 字段名映射为稳定的小写字段，筛选 `Production`、`Area harvested` 和 `Yield`，保留来源标记，拒绝重复键，并设置 `source=FAOSTAT_CROPS_URL`。缺少任何必需目标序列时必须抛出 `ValueError`。

运行：`python -m pytest tests/test_faostat.py -q`

预期：通过。

- [ ] **步骤 4：编写预期失败的世界银行解析测试**

在内存中构造包含 `Monthly Prices` 工作表的 XLSX，并断言显式别名映射：

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

运行：`python -m pytest tests/test_world_bank.py -q`

预期：失败，因为 `parse_world_bank_prices` 尚不存在。

- [ ] **步骤 5：实现世界银行适配器**

在 `src/elnino_cta/sources/world_bank.py` 中定义：

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

使用 `openpyxl` 读取工作簿，通过 `1960M01` 格式或日期值定位数据行，不能假定固定行号；把日期标准化为月末，并明确设置 Pink Sheet 单位（棕榈油为 `USD/mt`，世界糖价为 `USD/kg`）。任一品种缺失时必须明确报错。

运行：`python -m pytest tests/test_world_bank.py -q`

预期：通过。

- [ ] **步骤 6：提交数据源适配器**

```powershell
git add pyproject.toml src/elnino_cta/sources/faostat.py src/elnino_cta/sources/world_bank.py tests/test_faostat.py tests/test_world_bank.py
git commit -m "feat: add FAOSTAT and World Bank history sources"
```

---

### 任务 2：识别 ENSO 事件并构建无未来函数的气候与基本面证据

**文件：**
- 新建：`src/elnino_cta/historical_review.py`
- 新建：`tests/test_historical_review.py`
- 修改：`src/elnino_cta/pipeline.py`

**接口：**
- 输入：标准化 ONI、SOI、DMI，NASA POWER 天气数据和 FAOSTAT 作物历史数据框。
- 输出：`identify_enso_events`、`build_weather_evidence`、`build_yield_evidence` 和 `build_event_evidence_panel`。

- [ ] **步骤 1：编写预期失败的 ONI 事件识别测试**

```python
def test_identify_enso_events_requires_five_seasons_and_excludes_open_run():
    oni = synthetic_oni_runs(completed_length=6, short_length=4, open_length=5)
    events = identify_enso_events(oni)
    assert events["status"].tolist() == ["COMPLETED", "ONGOING"]
    assert events.loc[events["status"] == "COMPLETED", "duration_seasons"].iloc[0] == 6
    assert not events["event_id"].duplicated().any()
```

运行：`python -m pytest tests/test_historical_review.py::test_identify_enso_events_requires_five_seasons_and_excludes_open_run -q`

预期：失败，因为 `identify_enso_events` 尚不存在。

- [ ] **步骤 2：实现事件识别**

精确定义返回字段：

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

按日期排序、去重后，把连续满足条件的记录分组。若满足条件的连续区间止于最后一个 ONI 观测，则标为 `ONGOING`；其他区间标为 `COMPLETED`。峰值日期取最大 ONI 首次出现的日期，确保并列峰值的处理结果确定。

重新运行该单项测试。

预期：通过。

- [ ] **步骤 3：编写预期失败的天气与单产证据测试**

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

运行：`python -m pytest tests/test_historical_review.py -q`

预期：失败，因为证据构建函数尚不存在。

- [ ] **步骤 4：实现气候与基本面证据构建函数**

增加以下精确接口：

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

天气证据使用 `T0` 至 `T+12` 月窗口，并以 1991—2020 年相同日历月份均值作为基准。单产证据把事件开始年和下一年分别存为 `year_offset=0` 与 `year_offset=1`，同时保存同比和相对此前五年中位数的偏差。合并后的面板采用长表结构，每个 `event_id × commodity × metric_id` 组合只能有一行。保留 FAOSTAT 标记，并为每条年度作物观测设置 `calendar_crop_year_caveat=True`。

运行：`python -m pytest tests/test_historical_review.py -q`

预期：通过。

- [ ] **步骤 5：扩展历史气候刷新边界**

在 `pipeline.py` 中增加辅助函数，调用现有 NOAA 和 NASA 适配器，但不触发期货数据刷新：

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

该函数必须写入完整 ONI、SOI、DMI 历史数据，以及所有配置地区自 1981-01-01 起的 NASA POWER 数据，并为清单返回行数和日期覆盖范围。

运行：`python -m pytest tests/test_noaa.py tests/test_nasa_power.py tests/test_historical_review.py -q`

预期：通过。

- [ ] **步骤 6：提交事件与证据逻辑**

```powershell
git add src/elnino_cta/historical_review.py src/elnino_cta/pipeline.py tests/test_historical_review.py
git commit -m "feat: build no-look-ahead ENSO event evidence"
```

---

### 任务 3：构建按事件对齐的价格路径和月度 CTA 代理

**文件：**
- 修改：`src/elnino_cta/historical_review.py`
- 修改：`tests/test_historical_review.py`

**接口：**
- 输入：已结束或进行中的事件，以及标准化世界银行月度价格。
- 输出：`build_event_price_paths`、`build_monthly_cta_proxy`、`build_event_cta_paths` 和 `summarize_event_cta`。

- [ ] **步骤 1：编写预期失败的价格路径对齐测试**

```python
def test_event_price_paths_are_t0_normalized_and_span_minus6_plus18():
    paths = build_event_price_paths(events(), monthly_prices())
    one = paths.query("event_id == '1997-1998' and commodity == 'palm_oil'")
    assert one["relative_month"].min() == -6
    assert one["relative_month"].max() == 18
    assert one.loc[one["relative_month"] == 0, "price_index"].iloc[0] == pytest.approx(100.0)
```

运行：`python -m pytest tests/test_historical_review.py::test_event_price_paths_are_t0_normalized_and_span_minus6_plus18 -q`

预期：失败，因为 `build_event_price_paths` 尚不存在。

- [ ] **步骤 2：实现按事件对齐的价格路径**

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

返回 `event_id`、`status`、`commodity`、`date`、`relative_month`、`price`、`price_index`、`source`。必须存在有效的 `T0` 价格；对 `ONGOING` 事件，尚未发生的未来月份保持缺失。

重新运行价格路径测试。

预期：通过。

- [ ] **步骤 3：编写预期失败的月度 CTA 测试**

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

运行：`python -m pytest tests/test_historical_review.py::test_monthly_cta_is_lagged_scaled_and_costed -q`

预期：失败，因为 `build_monthly_cta_proxy` 尚不存在。

- [ ] **步骤 4：实现月度 CTA 与事件汇总**

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

使用 `sqrt(12)` 年化波动率，取两个动量方向的平均值，目标仓位滞后一个月执行，并按仓位绝对变化计算成本。汇总窗口为 0—3、0—6、0—12 和 0—18 个月，输出复合净收益、最大回撤、正收益月份占比、平均绝对仓位和总换手率。

运行：`python -m pytest tests/test_historical_review.py -q`

预期：通过。

- [ ] **步骤 5：在不改变日频信号的前提下增加 P/SR 事件窗口汇总**

增加：

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

只汇总现有信号 CSV 中已有的日期。不得重建或拼接主力合约。对 2015 年以前的事件明确返回覆盖缺失。

增加测试：断言 2015 年以前的记录为 `coverage_status="UNAVAILABLE"`，2015 年及以后的记录必须精确复合现有 `net_return`。

运行：`python -m pytest tests/test_historical_review.py -q`

预期：通过。

- [ ] **步骤 6：提交价格与 CTA 复盘逻辑**

```powershell
git add src/elnino_cta/historical_review.py tests/test_historical_review.py
git commit -m "feat: add event price and CTA proxy paths"
```

---

### 任务 4：生成描述性历史规律和 L2 六类汇总

**文件：**
- 新建：`src/elnino_cta/fundamental_summary.py`
- 修改：`src/elnino_cta/historical_review.py`
- 新建：`tests/test_fundamental_summary.py`
- 修改：`tests/test_historical_review.py`

**接口：**
- 输入：事件证据、事件价格/CTA 汇总和现有基本面 CSV。
- 输出：描述性规律表，以及当前 L2 恰好六个分类行。

- [ ] **步骤 1：编写预期失败的历史规律隔离测试**

```python
def test_pattern_labels_respect_minimum_sample_and_emit_no_allocation_fields():
    patterns = summarize_patterns(event_results())
    assert set(patterns["pattern_strength"]) <= {
        "STABLE_HISTORY", "LIMITED_SAMPLE", "INSUFFICIENT_EVIDENCE"
    }
    assert not any("allocation" in c or "confidence_adjustment" in c for c in patterns.columns)
```

运行：`python -m pytest tests/test_historical_review.py::test_pattern_labels_respect_minimum_sample_and_emit_no_allocation_fields -q`

预期：失败，因为 `summarize_patterns` 尚不存在。

- [ ] **步骤 2：实现描述性规律汇总**

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

`make_pattern_input` 把气候/基本面证据、3/6/12/18 个月价格收益，以及 Medium/Slow CTA 成本后收益转换成统一长表粒度，并排除 `ONGOING` 事件。棕榈油和白糖分别分组。输出样本量、正/负/持平次数、同向占比、中位数、四分位区间及设计文档规定的标签。不得创建操作、配置、建议仓位或置信度修正字段。

重新运行规律测试。

预期：通过。

- [ ] **步骤 3：编写预期失败的 L2 六行汇总测试**

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

运行：`python -m pytest tests/test_fundamental_summary.py -q`

预期：失败，因为汇总模块尚不存在。

- [ ] **步骤 4：实现 L2 汇总模块**

定义：

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

使用 `pd.merge_asof` 将 UNICA 制糖比按时间向后匹配到累计压榨量，禁止向前取值。计算 `brazil_cane_for_sugar_proxy = crush_tonnes * sugar_mix_pct / 100`。从棕榈油库存消费文件中分别提取 `total_use_1000t` 和 `ending_stocks_1000t` 作为独立明细行；白糖数据源文件已经把消费和期末库存分开。预测属性的判断顺序为：显式字段优先，其次检查 forecast/projection/estimate 备注，最后比较观测期结束日是否晚于发布日期。分类方向只能为 `TIGHT`、`NEUTRAL`、`LOOSE` 或 `INSUFFICIENT`。棕榈油三个分类及白糖需求、库存只评分各自主指标；白糖供给同时评分印度实际产量和巴西制糖甘蔗量代理，二者冲突时返回 `NEUTRAL`。任一评分指标属于预测值或缺少分位数时返回 `INSUFFICIENT`。不参与评分的预测型背景驱动仍须显示在 `driver_value_types` 中，但不能覆盖实际主指标的分类结果。

运行：`python -m pytest tests/test_fundamental_summary.py -q`

预期：通过。

- [ ] **步骤 5：提交历史规律与 L2 汇总逻辑**

```powershell
git add src/elnino_cta/historical_review.py src/elnino_cta/fundamental_summary.py tests/test_historical_review.py tests/test_fundamental_summary.py
git commit -m "feat: summarize ENSO patterns and L2 fundamentals"
```

---

### 任务 5：增加历史刷新命令并写入处理后结果

**文件：**
- 修改：`src/elnino_cta/cli.py`
- 修改：`src/elnino_cta/pipeline.py`
- 新建：`tests/test_history_pipeline.py`
- 修改：`docs/DATA_SOURCES.md`

**接口：**
- 输入：官方数据源适配器和现有处理后 P/SR 信号文件。
- 输出：八个 `data/processed/history/*.csv` 文件和一份来源清单，不改变当前监控快照。

- [ ] **步骤 1：使用注入数据框编写预期失败的端到端管线测试**

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

运行：`python -m pytest tests/test_history_pipeline.py -q`

预期：失败，因为 `refresh_history` 尚不存在。

- [ ] **步骤 2：实现输出编排**

在 `pipeline.py` 中增加：

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

当 `source_bundle=None` 时，从官方来源取数，并从 `output_dir/signals` 读取当前信号 CSV；注入数据框时不得发起网络请求。先写临时文件，再用 `Path.replace` 原子替换。向 `data/processed/history/manifest.json` 写入来源 URL、行数、最早/最晚日期、下载时间和文件 SHA-256。

运行：`python -m pytest tests/test_history_pipeline.py -q`

预期：通过。

- [ ] **步骤 3：增加命令行命令**

扩展 `cli.py`：

```python
history_parser = sub.add_parser("history-refresh", help="Build independent ENSO history review data")
history_parser.add_argument("--output", default="data/processed")
history_parser.add_argument("--as-of", required=True)
```

调用 `refresh_history(Path(args.output), args.as_of)`，并以 JSON 打印清单。该命令不得调用 `monitor` 或 `debrief`。

运行：`python -m pytest tests/test_history_pipeline.py -q`

预期：通过。

- [ ] **步骤 4：记录数据来源与解释边界**

在 `docs/DATA_SOURCES.md` 中补充官方 URL、产品名称、覆盖范围、单位、修订机制、FAOSTAT 标记保留方式、世界银行现货价格的局限，并明确历史输出不会被当前 CTA 门槛导入。

运行：`python -m compileall -q src`

预期：退出码为 0。

- [ ] **步骤 5：提交历史复盘管线**

```powershell
git add src/elnino_cta/cli.py src/elnino_cta/pipeline.py tests/test_history_pipeline.py docs/DATA_SOURCES.md
git commit -m "feat: add independent ENSO history refresh"
```

---

### 任务 6：生成包含历史复盘和 L2 六行汇总的八页工作簿

**文件：**
- 新建：`scripts/build_research_workbook.py`
- 新建：`tests/test_research_workbook.py`
- 修改：`README.md`

**接口：**
- 输入：当前监控、基本面、市场、信号 CSV，以及 `data/processed/history/*.csv`。
- 输出：`research/厄尔尼诺农产品CTA研究框架_八页复盘版_YYYYMMDD.xlsx`。

- [ ] **步骤 1：编写预期失败的工作簿结构测试**

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

运行：`python -m pytest tests/test_research_workbook.py -q`

预期：失败，因为工作簿生成器尚不存在。

- [ ] **步骤 2：实现可复用的工作簿格式和八个工作表**

在 `scripts/build_research_workbook.py` 中定义：

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

按以下精确接口和边界实现被调用的辅助函数：

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

同时实现以下写入接口；每个写入函数只负责其命名工作表或表格，不得修改输入数据框：

- `add_current_conclusion_sheet(workbook, frames, formats)`：只读取 `frames["monitor"]`，不得访问任何历史复盘键。
- `add_logic_sheet(workbook, frames, formats)`：展示 `L1 → L2 → L3 → L4 → L5`，并增加明确的旁路 `H 历史复盘（仅描述，不进入配置）`。
- `add_climate_sheet`、`add_market_sheet`、`add_cta_sheet` 和 `add_audit_sheet`：在整合进八页布局时，保留现有指标、来源、日期、含义、位置和局限。
- `write_event_summary_table(sheet, events, evidence, price_paths, cta_summary, daily_cta_summary, formats)`：每个 `event_id × commodity` 对应一个可见行，包含事件日期、ONI 峰值、气候/基本面证据覆盖、3/6/12/18 个月价格收益、Medium/Slow 月度 CTA 收益和回撤，以及可用的 P/SR Fast/Medium/Slow 日频代理结果；无覆盖时明确显示 `UNAVAILABLE`。
- `write_event_path_data_and_sparklines(sheet, price_paths, cta_paths, formats)`：从 `AA` 列开始写入按 `relative_month=-6..18` 排序的源数据区，隐藏这些列，并增加可见的价格指数与 CTA 净值迷你折线图。
- `write_pattern_table(sheet, patterns, formats)`：显示样本量、方向次数/占比、中位数、四分位数、规律强度和局限；不显示配置字段。
- `write_l2_summary_rows(sheet, summary, start_row, formats)`：严格按照已确认的棕榈油/白糖、供给/需求/库存顺序写入六行。
- `write_l2_detail_rows(sheet, details, fundamental_history, start_row, formats)`：显示指标、含义、数值、单位、观测日期、发布日期、实际/预测、分位数/当前位置含义、来源和局限。从 `BA` 列开始把每个指标按时间排序的历史值写入隐藏区；历史记录不少于两条时增加行级迷你折线图。棕榈油衍生平衡指标使用各自来源字段，巴西代理使用只向后匹配的压榨量/制糖比历史。

使用 `xlsxwriter.Workbook`。复用当前标题、分区、表格、条件格式、冻结窗格和列宽规范。`00_结论-CTA建议` 的内容只能来自当前监控快照。在 `01_总逻辑` 中注明历史复盘是独立研究分支，不接入配置逻辑。

重新运行工作簿结构测试。

预期：通过。

- [ ] **步骤 3：增加历史路径、CTA 收益和迷你折线图**

对 `02_H_历次厄尔尼诺复盘` 中每个事件/品种行，写入视图区外的隐藏源数据区，其中包含 `relative_month=-6..18`、`price_index`、Medium 净值和 Slow 净值。每行至少增加两条可见迷你折线图：价格路径和 CTA 净值路径。可见汇总字段必须包含 0—3/6/12/18 个月商品收益、CTA 净收益、最大回撤、样本覆盖和局限。

增加测试断言：工作表 XML 包含 `<x14:sparklineGroup`，且可见表头包含 `价格路径`、`CTA代理净值`、`商品收益` 和 `CTA成本后收益`。

运行：`python -m pytest tests/test_research_workbook.py -q`

预期：通过。

- [ ] **步骤 4：增加 L2 汇总与明细展示**

在 `04_L2供给基本面` 顶部写入六行汇总，其后展示完整驱动指标表。使用专门一列展示实际/预测状态。全球食糖消费和期末库存必须分开显示，库销比作为独立明细行。巴西指标必须精确标为 `巴西中南部制糖甘蔗量代理`，含义列显示 `压榨量 × 制糖比；非食糖产量`。

增加测试断言：上述标签准确；预测型评分指标不得产生 `TIGHT` 或 `LOOSE` 分类。预测型背景驱动必须保留在明细表和 `driver_value_types` 中，但不能改变实际主指标的分类。

运行：`python -m pytest tests/test_research_workbook.py tests/test_fundamental_summary.py -q`

预期：通过。

- [ ] **步骤 5：扫描公式、错误值和敏感信息**

关闭工作簿后，使用 `openpyxl` 重新打开并检查所有公式和字符串。发现 `#REF!`、`#DIV/0!`、`#VALUE!`、`#NAME?`、`#N/A`、`Authorization`、`Bearer ` 或类似 JWT 的 `eyJ` 令牌前缀时，构建必须失败。

增加一个测试，把 `Bearer secret` 注入测试单元格，并断言校验器拒绝该工作簿。

运行：`python -m pytest tests/test_research_workbook.py -q`

预期：通过。

- [ ] **步骤 6：记录工作簿生成方式**

在 `README.md` 中增加：

```powershell
elnino-cta history-refresh --as-of 2026-08-23
python scripts/build_research_workbook.py --as-of 2026-08-23
```

注明历史复盘页只作描述，不进入当前 CTA 配置门槛。

- [ ] **步骤 7：提交工作簿生成器**

```powershell
git add scripts/build_research_workbook.py tests/test_research_workbook.py README.md
git commit -m "feat: add ENSO history review workbook"
```

---

### 任务 7：执行实时刷新、验证研究质量并发布最新工作簿

**文件：**
- 修改：`data/processed/history/*.csv`
- 新建：`research/厄尔尼诺农产品CTA研究框架_八页复盘版_20260824.xlsx`
- 修改：`.github/workflows/monitor.yml`
- 修改：`README.md`

**接口：**
- 输入：前述全部已完成任务和获授权的网络访问。
- 输出：实时历史 CSV、通过校验的工作簿、CI 刷新步骤，以及供用户审阅的简洁、证据驱动的规律总结。

- [ ] **步骤 1：实时取数前运行全部单元测试**

运行：`python -m pytest -q`

预期：全部测试通过。

- [ ] **步骤 2：刷新官方历史数据**

运行：

```powershell
elnino-cta history-refresh --as-of 2026-08-24
```

预期：`data/processed/history/manifest.json` 显示已结束事件数量大于零、包含世界银行两个品种、包含 FAOSTAT 四个国家/作物组合，且不存在重复主键。

- [ ] **步骤 3：运行明确的数据质量检查**

运行仓库脚本或 pytest 集成测试，并断言：

```python
assert events.query("status == 'COMPLETED'").shape[0] >= 4
assert not evidence.duplicated(["event_id", "commodity", "metric_id"]).any()
assert set(price_paths["relative_month"]).issubset(set(range(-6, 19)))
assert (price_paths["price"] > 0).all()
assert not cta_paths.duplicated(["event_id", "commodity", "speed", "date"]).any()
assert not any(c in current_monitor_json for c in ["historical_patterns", "event_cta_summary"])
```

预期：退出码为 0；任何失败都必须阻止工作簿发布。

- [ ] **步骤 4：生成并检查工作簿**

运行：

```powershell
python scripts/build_research_workbook.py --as-of 2026-08-24
python -m pytest tests/test_research_workbook.py -q
```

预期：带日期的工作簿已生成，恰好包含八个可见工作表，历史页和指标页具有迷你折线图，并通过公式与敏感信息扫描。

- [ ] **步骤 5：目视检查四个关键页面**

渲染或打开以下区域，检查文字截断、对齐、条件色和图表尺度可读性：

- `00_结论-CTA建议`：顶部结论与操作区；
- `02_H_历次厄尔尼诺复盘`：事件表以及价格/CTA 迷你折线图；
- `04_L2供给基本面`：六行汇总和明细；
- `07_L5数据审计`：来源、时效性和局限。

预期：不存在被截断的表头、重叠图表、不可读行或被隐藏的局限说明。

- [ ] **步骤 6：增加不依赖 Earthdata 的 CI 历史刷新**

更新 `.github/workflows/monitor.yml`：在现有监控步骤后运行 `elnino-cta history-refresh --as-of "$(date -u +%F)"` 和工作簿生成器。一期不得引用 `EARTHDATA_TOKEN`。在提交步骤中加入 `data/processed/history` 和带日期工作簿的匹配规则。

运行 YAML 解析检查和 `python -m pytest -q`。

预期：两项均通过。

- [ ] **步骤 7：编写面向用户的复盘总结**

在 `README.md` 中更新最新工作簿文件名，并且只记录满足样本量门槛的已观察规律。棕榈油和白糖分开描述，列明样本量、中位数、区间和缺失覆盖。不得加入配置建议或置信度修正。

- [ ] **步骤 8：提交最终实时产物**

```powershell
git add data/processed/history research/厄尔尼诺农产品CTA研究框架_八页复盘版_20260824.xlsx .github/workflows/monitor.yml README.md
git commit -m "data: publish ENSO historical review"
```

- [ ] **步骤 9：最终验证**

运行：

```powershell
python -m pytest -q
python -m compileall -q src scripts
git status --short
```

预期：测试和编译均通过；`git status --short` 只显示原先存在的两个无关、未跟踪用户文件，且这两个文件未被改动。
