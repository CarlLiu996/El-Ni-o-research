# 厄尔尼诺 × 农产品 CTA 研究

一个可复现的数据工程与研究原型，用于检验 ENSO、主产区降水、棕榈油/白糖期货趋势和 CTA Proxy 之间的关系。

## 已实现

- NOAA CPC ONI（月度重叠季）自动下载与标准化；
- NASA POWER 马来西亚棕榈油、印度白糖代表产区日降水；
- P、SR 两级期货数据源：
  - 正式研究：Tushare 逐月合约 + 主力映射（需要 `TUSHARE_TOKEN`）；
  - 原型验证：AkShare/Sina 主连（无需 Token，但换月规则不可审计）；
- Fast / Medium / Slow 趋势代理；
- 次日执行、波动率目标、杠杆上限、换手成本；
- 缺失、重复、非法日期和非正价格的数据质量检查；
- 每次刷新生成 `manifest.json`，记录来源、样本与限制。
- 三层持续监控：气候、基本面、市场/CTA；
- 数据新鲜度、期限结构、趋势持续性、逆向波动和缺口告警；
- 每日机器快照、历史序列和 Markdown 复盘归档；
- GitHub Actions 工作日自动刷新、复盘并提交最新状态。

## 快速开始

```powershell
python -m pip install -e ".[dev]"
elnino-cta refresh --start 2023-01-01 --end 2026-08-14
python -m pytest -q
```

未配置 Tushare Token 时，`auto` 会自动使用无需密钥的探索性数据源。正式研究前配置：

```powershell
$env:TUSHARE_TOKEN="你的 Token"
elnino-cta refresh --start 2015-01-01 --end 2026-08-14 --futures-provider tushare
```

如使用兼容代理，可额外设置 `$env:TUSHARE_HTTP_URL="https://你的代理地址/"`。Token 与代理地址均通过环境变量注入，不写入数据文件或仓库。

也可显式选择 `--futures-provider sina` 进行管线冒烟测试。

## 监控与复盘

```powershell
elnino-cta monitor --as-of 2026-08-17
elnino-cta debrief
```

输出位于 `reports/monitoring/`。当前研究门禁遵循项目原始目标：只有“气候确认 + 基本面兑现 + Medium/Slow CTA 趋势确认”同时成立，才进入 CTA 配置复核。MPOB、FFB、印度糖、UNICA 等数据尚未接入时，系统会明确输出 `KEEP_MONITORING`，不会用价格变化替代基本面证据。

详细口径见 [docs/MONITORING.md](docs/MONITORING.md)，基本面导入规范见 [docs/FUNDAMENTAL_DATA_CONTRACT.md](docs/FUNDAMENTAL_DATA_CONTRACT.md)。

## 输出结构

```text
data/processed/
├── climate/
│   ├── noaa_oni.csv
│   └── nasa_power_precipitation.csv
├── futures/
│   ├── P_contracts_tushare.csv
│   ├── P_contract_metadata_tushare.csv
│   ├── P_dominant_mapping_tushare.csv
│   ├── P_dominant_tushare.csv
│   └── SR_*.csv
├── signals/
│   ├── P_fast_proxy_research.csv
│   └── ...
└── manifest.json
```

本次正式数据覆盖 2015-01-01 至 2026-08-14：P/SR 主力序列各 2,823 行，逐月合约行情分别 33,876 行和 19,524 行。最新版汇总工作簿位于 `research/厄尔尼诺农产品CTA研究数据库_监控复盘版_20260817.xlsx`。

## 研究口径

- Fast：20/60 日收益方向均值，20 日实现波动率；
- Medium：60/120 日收益方向均值，60 日实现波动率；
- Slow：120/250 日收益方向均值，120 日实现波动率；
- 仓位在信号形成后的下一交易日生效；
- 默认年化波动率目标 10%、最大绝对杠杆 2 倍、单边换手成本 2 bps。

当前结果是研究基础设施，不是交易建议。尤其不能把 AkShare/Sina 主连结果当作已验证回测；正式结论需使用可审计的逐月合约与换月映射，并完成样本外、参数平台、成本与市场状态压力测试。

更多接口和字段口径见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。
