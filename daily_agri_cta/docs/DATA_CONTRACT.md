# 数据库、时间序列与可复核计算协议

## 1. 数据与时间语义：一条证据至少有三个时间

- `observation_date`：行情交易日/库存统计期末/报告所属月份；禁止用爬取日替代。
- `published_at`：官方发布时刻（带时区；确实找不到应为 NULL 和明确 `unknown`）。
- `fetched_at`：Codex 实际获取时刻（UTC）。
- `as_of_cutoff`：报告允许看到的最后时刻，北京时间 07:50；在这之后公布的新数据不能写成该次08:00日报已知信息。
- `revised_at`：官方修订公布日；改写旧月数据应记录新 vintage，而非悄悄覆盖。
- `first_reported_on`：上一次日报中是否出现以及出现日期；用于防重复叙述。

```text
官方发布时间 == 昨天、统计日 == 上周日：仍属于今日新增披露
官方发布时间 == 上周一、昨天日报已讲过：今日是延续信号/背景
统计日 == 昨天、实际上明天才发布：今天不能提前引用
没有官方发布时间、今天首次发现：标记首次发现但不杜撰今日发布
```

## 2. DuckDB/Parquet 最小数据模型（实施建议）

```sql
CREATE TABLE IF NOT EXISTS source_document (
  document_id VARCHAR PRIMARY KEY, -- SHA256(raw bytes + normalized source URL)
  source_id VARCHAR NOT NULL,
  source_url VARCHAR NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  published_at TIMESTAMPTZ, -- NULL 表示 unknown
  period_start DATE,
  period_end DATE,
  content_type VARCHAR,
  rights_class VARCHAR,     -- public / licensed / restricted
  raw_path VARCHAR,        -- 受限原始文件不能推送至公开GitHub
  parse_status VARCHAR,
  error_message VARCHAR
);
CREATE TABLE IF NOT EXISTS observation (
  observation_id VARCHAR PRIMARY KEY,
  metric_id VARCHAR NOT NULL,
  commodity VARCHAR NOT NULL,
  observation_date DATE NOT NULL,
  contract_code VARCHAR,
  spot_location VARCHAR,
  quality_grade VARCHAR,
  price_type VARCHAR,   -- close / settlement / weighted / spot / non_price
  frequency VARCHAR NOT NULL,
  value DOUBLE,
  unit VARCHAR NOT NULL,
  document_id VARCHAR NOT NULL,
  published_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  revision_status VARCHAR DEFAULT 'first_release',
  data_status VARCHAR NOT NULL, -- verified / provisional / unverified
  FOREIGN KEY (document_id) REFERENCES source_document(document_id)
);
CREATE TABLE IF NOT EXISTS report_run (
  report_date DATE PRIMARY KEY,
  freeze_at TIMESTAMPTZ NOT NULL,
  prev_report_date DATE,
  code_commit VARCHAR,
  qa_passed BOOLEAN NOT NULL,
  report_manifest_path VARCHAR
);
CREATE TABLE IF NOT EXISTS reported_evidence (
  report_date DATE NOT NULL,
  observation_id VARCHAR NOT NULL,
  evidence_tag VARCHAR NOT NULL,   -- TODAY_NEW / WEEK_NEW / CONTINUING / BACKGROUND
  impact VARCHAR,
  cited_source_url VARCHAR,
  PRIMARY KEY(report_date, observation_id)
);
CREATE TABLE IF NOT EXISTS company_margin_notice (
  notice_id VARCHAR NOT NULL,
  contract_code VARCHAR NOT NULL,
  rule_type VARCHAR NOT NULL, -- company_speculative_margin / company_hedge_margin / price_limit / one_side_margin_rebate / delivery_rule
  previous_value DOUBLE,
  new_value DOUBLE,
  unit VARCHAR,
  effective_trade_date DATE,
  published_at TIMESTAMPTZ,
  official_notice_url VARCHAR NOT NULL,
  previous_value_status VARCHAR NOT NULL, -- verified / unknown
  PRIMARY KEY (notice_id, contract_code, rule_type)
);
```

### 原始数据策略

- 每次抓取保存原始 HTML/CSV/PDF/JSON 的 SHA256、URL、HTTP状态、时间和文件尺寸；同内容去重，但保留每次采集尝试/错误日志。
- 原始受限数据放私有存储（或环境内临时空间）；GitHub 公共分支只存源码、经许可引用的小量样本与生成规则。
- 不要将 PDF 来源截图视作数据序列。扫描 PDF 先尝试官方 Excel/CSV/表格；若不得已解析 PDF，记录提取误差与人工校验状态。

## 3. 数据质量门禁

**P0硬性阻断（不得出数/作图）：**
- 来源URL不明、无法核对交易日/统计期、币种单位未知、完整性不达标、同一日期重复冲突无法解决、源网页只取到表头、市场合约不对应、价格类型不一致。
- 价格不应为负/零（特殊跨品价差例外）；数量和库存单位必须可识别；日期不得晚于截止时点已知信息。
- 日行情缺测按交易所节假日/停盘原因分类，不用前值填充成新交易日。

**P1告警（可展示但必须注明）：**
- 周度/月度序列发布日期滞后；官方修订；历史系列换统计口径；低流动性合约；不同交易所夜盘日界；非原始转引；中国夜盘对海外日盘的时差。

## 4. 合约与价差

- `futures_close` 与 `futures_settlement` 分库存放。月差两腿都是收盘或都是结算，且日期匹配。
- `basis = spot_same_day_same_grade_same_location - futures_named_contract_same_date`；spot/期货时段不一致时标记 `asynchronous`，不可冒充严格同日同步。
- `spread = near_contract - far_contract`，例如 SR2701 − SR2705。远月升水 = spread < 0。
- `Y-P`、`OI-Y`、`M-RM` 必须指定两腿合约和价格类型、单位；若跨品乘数不同且要做套利损益，需做每手名义/吨位对齐。
- 主连只用于价格趋势观察，不可直接构造真实收益率或自动回测；应保留 contract_roll_event、roll_rule、raw_contract_id 及换月跳空处理。
- RU与NR不是同一品级，价格差额不能自动等同“无套利区间”；须考虑标准胶/20号胶、交易所和汇率差异。

## 5. 历史序列与分位

建议至少按可用样本程度分档：
- 3个月日度：>=45个有效交易日；1年日度：>=180个有效交易日；3年日度：>=540个有效交易日，且有效覆盖>=80%（节假日已剔除）。
- 月度1年：>=10个有效月份；月度3年：>=30个月；季节性“往年同月”最好>=3年且同比口径一致。
- 采样不满上述要求，标注 `insufficient_history`，可披露最新观察值但不披露伪历史分位。
- 分位在给定观察窗口、同一指标/合约/地区/价格类型内按经验分布（ECDF）计算，报告明确窗口和有效样本量。若指标频率为月度，不把月频重复插值成日频拉高样本量。
- 禁止“取两个月预测值画成3年真实库存趋势”；只有确实更新的指标可入当天图表，历史底图用于定位最新变化。

## 6. 每日新增的判定（示例）

1. 取 `freeze_at` 前已公开且通过验证的 observation，保留有效最新 vintage。
2. 取上次报告 `prev_freeze_at` 时可见的同指标最后一个有效披露。
3. `newly_published` = 发布/修订时刻落在 `(prev_freeze_at, freeze_at]`；没有官方发布时间的需 `first_seen` + 备注“非确证今日公布”。
4. `meaningful_change` = 相对上一有效披露增量超业务阈值，或库存方向/基差/月差符号变化、政策/交割风险、异常公检/仓单等证据，即使评级未动也需分析。
5. `already_covered` = 是否存在上一日报同 observation_id/同 vintage；避免周度旧新闻重复计数。
6. `upgrade_ready` = 至少有供需 + 库存/现货结构 + 趋势一致且不被重大反证否定；可人工审核而非自动打星。

## 7. 证据/数据差异测试要求

应提供以下可重复单测（fixture 明确为 synthetic，不入市场数据表）：
- 旧月MPOB官方修订 → 新 vintage，不误判成今天10月新生产数据。
- 周度青岛库存11月8日统计、11月12日首次发布 → 正确归类今日新增披露。
- 昨天报告提过的11月10日USDA数据 → 今天归类延续，非今日新增。
- 现货+2701收盘 与 2705结算不能相减得日差。
- 国投只公布新费率22%未给旧值 → old_value=unknown，不算变化幅度。
- 公告 RU2611 自然人归零 → rule_type=delivery_rule，不写成保证金比例变化。
- 新合约不成交与节假日缺交易 → 不做前值填充和假涨跌。
