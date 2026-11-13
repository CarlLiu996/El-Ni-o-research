# 日报输出契约（供 Codex 生成数据包，供现有08:00日报阅读）

每日 07:50 CST 冻结一版 \`report_manifest.json\`，不盲等所有源；对于未抓到数据列出缺口和最后可核实截至日。

## 结构

### 1. 首页九品种“多品种总览”

逐品种固定字段：
\`symbol, account_tag, last_known_stage, old_rating, new_rating, changed_rating, newly_disclosed_evidence, valuation_view, valuation_confidence, safety_margin, concentrated_setup, source_cutoff, missing_data\`

- 阶段：预期形成/基本面验证/价格确认/趋势启动/逻辑证伪。
- 星级：★–★★★★，若没有★★★★写“今日无★★★★集中表达标的”。
- 估值来源：基差历史分位、近远月结构、生产/压榨成本、进口利润、相对价差、库存-价格关系；若无合法真实序列写“仅做方向判断”。
- 今日无证据 = \`本次未取得可核实关键新增\`，**不等于客观市场没有变化**。

### 2. 新增证据账本（机器可读）

\`evidence_ledger.csv\` 推荐字段：
\`\`\`
report_date,symbol,metric_id,source_id,source_url,observation_date,published_at,
fetched_at,old_observation_date,old_value,new_value,unit,delta,percent_delta,
vintage,evidence_tag,already_reported,meaningful_change,impact_dimension,
bull_bear,contradictory_evidence,verification_status,quality_issues
\`\`\`

每个品种即使无关键新增，也应有 source_coverage 日志，包含**已查具体指标、是否有新披露、源访问失败、授权不足、未能核验的数据截止日**。

四种标记：
- \`TODAY_NEW\` / 【今日新增】：两次冻结时点之间正式新增披露或修订且具可核验时间。
- \`WEEK_NEW\` / 【本周新增】：本周较早已发布但未写过日报且仍需解释，注明初次披露日（不能叫“今日”）。
- \`CONTINUING\` / 【延续信号】：已披露且已有报道，解释与新信息的关系。
- \`BACKGROUND\` / 【旧数据/背景】：纯背景，不当作新数据。

### 3. 正文：变化品种优先

每个值得分析的品种，不受固定字数限制，写：
- **供应/成本变化**：何日披露，量/成本/天气/政策的新增；
- **需求和库存**：直接证据，销量/开工/到港/库存/仓单变化；不要把仓单注销自动认定为去库；
- **现货/基差/月差/外盘**：注明品种、期价类型、合约月份、数据日期，区分期权IV和期货行情；
- **冲突证据、估值、CTA操作及证伪条件**：已交易多少预期？风险收益对称吗？为何升降或维持星级？

每个品种数据覆盖率可在附表/QA中，而不是用“无变化”替代根本未查。

### 4. 历史图：仅画变化指标

- 只选择 \`meaningful_change=true\` 且有解释力/完整时序的数据。
- 能取到1年或3年的真实数据则选1y/3y历史位置和同期季节性。按同品质、地区、单位、交易价格类型。
- 图上标注最新点、来源、样本区间、频率、缺失率、n、发布日期。
- 每图四段短注：最新变化、历史位置、供需解释、CTA操作含义/证伪条件。
- 历史数据不够，正文给证据，但不画伪图或杜撰分位。

### 5. 今日优先级

按供需拐点接近程度、估值/安全边际、逻辑证伪概率、趋势与流动性综合排序。不因某品种价格单日波动而自动升星。

### 6. 最后固定“国投期货保证金及涨跌停板监测”

优先访问：
1. https://www.sdicfutures.com.cn/annc/business/11098.html 官方《交易提示》列表；
2. 从站内导航进入最近新公告，并解析披露日/适用交易日/适用合约；
3. https://www.sdicfutures.com.cn/service/publicity/11247.html 公司客户标准作辅助；
4. 交易所正式通告作对照，但不可当国投公司客户保证金。

逐项监测 SR CF RU NR M C P Y OI RM 的主力、2701及特殊合约。区分：
\`company_speculative_margin, company_hedge_margin, exchange_speculative_margin, exchange_hedge_margin, price_limit, margin_preference, delivery_restriction\`。

**有变化**：小表显示合约、原值→新值（不明写“旧值未核实”）、投机/套保、涨跌幅变化、生效日、公告链接。
**无官方相关新增公告**：“今日未发现涉及关注品种的新增保证金及涨跌停板调整公告（基于已核实公告）”。
**只有完整有效基线、无相关新公告且核验通过**：“今日关注品种保证金及涨跌停板均无变化”。
**无法查明**：“未能核实”，并标明哪些链接尝试失败。动态表仅出表头不是该网站不可用。
**休市**：不做盘面日变比较，但检查未来生效的公告；永不省略一行风控结论。

## 生成产物

\`\`\`text
reports/2026-11-13/
  report_manifest.json
  evidence_ledger.csv
  source_coverage.csv
  qa.json
  report.md
  charts/*.png
\`\`\`

\`qa.json\` 必须包含 \`complete_9_symbols\`、\`future_data_leakage_check\`、\`source_missing\`、\`unverified_metrics\`、\`exceptions\`、\`historical_series_available\`、\`margin_notice_checked_through\`。无法通过硬门禁时对应指标不进入图表、估值或评级推导。

日报属于投资研究，不是交易执行指令；禁止自动交易、私自调整账户保证金或任何资金操作。