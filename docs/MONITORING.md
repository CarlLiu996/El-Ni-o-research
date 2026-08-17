# 数据监控与每日复盘

## 目标

监控严格对应项目研究链条：

`气候冲击 → 基本面兑现 → 市场趋势/波动/期限结构 → CTA Proxy → 配置复核`

系统不会因为 ONI 达到厄尔尼诺阈值就直接给出配置确认。只有以下三层同时通过，状态才会变为 `READY_FOR_ALLOCATION_REVIEW`：

1. `climate=CONFIRMED`：ONI 处于厄尔尼诺区间；
2. `fundamentals=OK`：项目定义的 MPOB、FFB、印度糖、UNICA 和全球库存指标已更新；
3. `market_cta=CONFIRMED`：P/SR 的 Medium 与 Slow CTA Proxy 近期表现与持续性满足门禁。

这仍然只是进入 CTA 管理人配置复核的前置条件，不是期货方向交易建议。

## 运行

```powershell
elnino-cta monitor --as-of 2026-08-17
elnino-cta debrief
```

`monitor` 一次生成：

- `reports/monitoring/latest.json`：完整机器可读状态；
- `reports/monitoring/history.csv`：每日关键指标历史，按日期幂等更新；
- `reports/monitoring/latest.md`：最新每日复盘；
- `reports/monitoring/debrief_YYYYMMDD.md`：按日归档复盘。

使用 `--fail-on-critical` 时，存在关键数据告警会以退出码 2 结束，适合 CI 告警门禁。

## 监控内容

### 气候层

- ONI 最新值、三个月变化、阶段和时滞；
- 马来西亚棕榈油与印度糖代表点近 30 日降水；
- 相对历史同期偏差、少雨天数、连续少雨天数；
- 代表点覆盖与缺失观测。

### 基本面层

按 `config/monitoring.json` 检查所需文件是否存在、观测日期、实际发布日期和新鲜度。优先用 `published_at` 计算数据何时可获得，并按月度、双周、年度设置不同阈值；因此年度平衡表不会因观测期末较早而被误判。数据契约见 `docs/FUNDAMENTAL_DATA_CONTRACT.md`。

### 市场与 CTA 层

- 主力价格 5/20/60/120/250 日变化；
- 20 日实现波动率、60 日最大回撤；
- 最近两个可用月合约价差与 contango/backwardation；
- Fast/Medium/Slow 的 5/20/60 日净收益、目标仓位、执行仓位、仓位持续天数、换手和回撤；
- P/SR 同时确认情况。

## 自动化

`.github/workflows/monitor.yml` 在工作日 UTC 10:30（北京时间 18:30）刷新并复盘。仓库需要配置：

- Secret：`TUSHARE_TOKEN`
- Variable：`TUSHARE_HTTP_URL`（仅在使用兼容代理时设置）

工作流会提交更新后的 `data/processed` 和 `reports/monitoring`。Wind 基本面刷新脚本需在已安装并授权 Wind MCP skill 的环境运行；未刷新或超时的数据会形成明确告警，不会用价格替代供给证据。

## 当前边界

- 当前境内数据只覆盖 2015 年至今，能覆盖 2015/16 与 2023/24，不能验证 1982/83 与 1997/98；
- 尚需接入 BMD FCPO、ICE Sugar No.11、ICE Coffee 长历史；
- 管理人映射保持延后，直到取得尽调和净值数据。
