# 厄尔尼诺 × 农产品 CTA 研究

这是一个可复现的数据工程与研究原型，用来检验 ENSO、主产区天气、棕榈油/白糖供给、期货定价与 CTA 趋势策略之间的证据链。

## 最新研究入口

优先阅读七页精简工作簿：

- `research/厄尔尼诺农产品CTA研究框架_七页精简版_20260823.xlsx`

工作簿按以下顺序组织：

1. CTA 配置结论；
2. 总逻辑；
3. L1 气候天气；
4. L2 供给基本面；
5. L3 市场定价；
6. L4 CTA 环境；
7. L5 数据审计。

当前结论是 `KEEP_MONITORING`：RONI 与 SOI 已确认厄尔尼诺海气耦合，印尼棕榈区近30日降水明显偏少，但L2九项时效门槛只有八项通过，且MPOB库存、印度糖、UNICA与全球库销比尚未形成一致收紧；P/SR 的中慢速 CTA 也未跨品种共振。因此，不建议仅因厄尔尼诺主题额外追买 CTA；长期分散配置可维持基准仓位或小额分批进入。

## 已实现

- NOAA CPC ONI/RONI/SOI 与 NOAA PSL DMI 月度数据下载和标准化；
- NASA POWER 印马棕榈油、印度及巴西中南部白糖12个代表点的日降雨与根区土壤湿度；
- Tushare 逐月合约、主力映射、P/SR 主力行情；
- Fast / Medium / Slow 趋势代理；
- 次日执行、波动率目标、杠杆上限和换手成本；
- 缺失、重复、非法日期和非正价格的数据质量检查；
- 气候、供给、市场、CTA 与数据审计五层监控；
- 每日机器快照、历史序列和 Markdown 复盘；
- GitHub Actions 工作日自动刷新和复盘。
- MPOB产量/库存/出口、印度糖、UNICA压榨/制糖比，以及USDA全球棕榈油/糖库销比的结构化供给数据；
- 基本面监控按 `published_at` 和各自发布频率做时效门禁，避免把年度观测日期误判为数据过期。

## 快速开始

```powershell
python -m pip install -e ".[dev]"
elnino-cta refresh --start 2023-01-01 --end 2026-08-14
elnino-cta monitor --as-of 2026-08-17
elnino-cta debrief
python -m pytest -q
```

已安装 Wind MCP skill 的环境可刷新供给数据（脚本不包含 Token）：

```powershell
node scripts/fetch_supply_fundamentals.mjs
elnino-cta monitor --as-of 2026-08-18
```

正式研究建议通过环境变量配置 Tushare，不把 Token 或代理地址写入仓库：

```powershell
$env:TUSHARE_TOKEN="你的 Token"
$env:TUSHARE_HTTP_URL="https://你的代理地址/"
elnino-cta refresh --start 2015-01-01 --end 2026-08-14 --futures-provider tushare
```

## 五层研究门槛

`气候天气 → 供给基本面 → 市场定价 → CTA 可捕捉性 → 数据审计 → 配置结论`

- L1 用RONI/ONI、SOI、DMI、四大目标产区降水、连续少雨与根区湿度回答厄尔尼诺是否海气耦合并落到产区；
- L2 用产量/单产/压榨、出口需求、库存/库销比、加工分流与政策否决项验证天气是否转化为基本面收紧；
- L3 验证价格、基差和期限结构是否共同定价；
- L4 验证中慢速、多品种、成本后趋势是否共振；
- L5 检查来源、时效、缺失、换月与成本方法。

只有关键供给数据齐备并显示收紧，同时 Medium/Slow 趋势持续确认，才进入 CTA 主题加仓复核。上游气候信号不能替代下游供给或市场证据。

## CTA 速度定义

- Fast：20/60 日收益方向均值，20 日实现波动率；
- Medium：60/120 日收益方向均值，60 日实现波动率；
- Slow：120/250 日收益方向均值，120 日实现波动率；
- 信号形成后的下一交易日执行；
- 默认年化目标波动率 20%，最大绝对杠杆 2 倍，单边换手成本 2 bps。

当前 CTA 结果是研究代理，不是可部署回测。主力合约直接拼接会把换月跳空计入收益；正式验证需使用后复权连续序列或真实换月成交 P&L，并扩展到跨资产、等风险的完整期货池。

## 数据与文档

- 处理后数据：`data/processed/`
- 监控输出：`reports/monitoring/`
- 监控定义：`docs/MONITORING.md`
- 基本面导入规范：`docs/FUNDAMENTAL_DATA_CONTRACT.md`
- 数据源说明：`docs/DATA_SOURCES.md`

气候数据覆盖 2015-01-01 至 2026-08-22；P/SR 主力行情当前截至 2026-08-14、各 2,823 行，因此2026-08-23监控明确标记行情过期。原始逐月合约数据保留在 `data/processed/futures/`，用于期限结构和换月审计。
