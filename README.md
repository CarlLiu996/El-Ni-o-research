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

本次正式数据覆盖 2015-01-01 至 2026-08-14：P/SR 主力序列各 2,823 行，逐月合约行情分别 33,876 行和 19,524 行。最新版汇总工作簿位于 `research/厄尔尼诺农产品CTA研究数据库_最新版_20260816.xlsx`。

## 研究口径

- Fast：20/60 日收益方向均值，20 日实现波动率；
- Medium：60/120 日收益方向均值，60 日实现波动率；
- Slow：120/250 日收益方向均值，120 日实现波动率；
- 仓位在信号形成后的下一交易日生效；
- 默认年化波动率目标 10%、最大绝对杠杆 2 倍、单边换手成本 2 bps。

当前结果是研究基础设施，不是交易建议。尤其不能把 AkShare/Sina 主连结果当作已验证回测；正式结论需使用可审计的逐月合约与换月映射，并完成样本外、参数平台、成本与市场状态压力测试。

更多接口和字段口径见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。
