# 独立实施与运行方法

所有命令在 `daily_agri_cta/` 执行（Python 3.12 实测）。不调用现有ChatGPT任务，不设置任何cron。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/python -m agri_cta.cli audit --data data_audit --output reports/network-audit
.venv/bin/python -m agri_cta.cli backfill --source mpob
.venv/bin/python -m agri_cta.cli backfill --source noaa
.venv/bin/python -m agri_cta.cli backfill --source shfe --start 2025-01-01 --end 2025-12-31
.venv/bin/python -m agri_cta.cli collect --source shfe_daily --start 2026-11-12 --end 2026-11-12
.venv/bin/python -m agri_cta.cli backfill --source receipts --start 2026-11-09 --end 2026-11-12
.venv/bin/python -m agri_cta.cli validate
.venv/bin/python -m agri_cta.cli watch-risk --data data_risk --output reports/risk-validation
```

年度 `--start/--end` 选择所跨的完整年度附件，不裁剪年度内日期。日频请求以所给日期为候选；404只记未取得，不擅自认定节假日。`collect`与`backfill`共用采集管道；增量以标准化键/值/状态幂等入库，非仅按文件哈希去重。官网修订值或初值/修订标记变化产生新vintage。取数错误会记录`unverified`；即使CLI完成也必须检查`last_run.json`，不能当所有源成功。

生成报告必须显式提供两个带时区的冻结时间，例如：

```bash
.venv/bin/python -m agri_cta.cli report --output reports/validation \
  --previous-freeze 2026-11-12T07:50:00+08:00 \
  --freeze 2026-11-13T07:50:00+08:00
```

冻结时点早于首次实际取得的数据会被排除。11月13日上午08:00后才取得的数据，不能回填成该日08:00日报已知数据。本PR的历史库诊断报告用实际采集完成后的UTC时间冻结，并不冒充07:50生产日报。图表是历史库验证底图；尚未用于“今日新增”投资判断。欠30个月不作月度历史分位；没有同地区/品质现货，不计算基差。

## 文件与时间

- `data/history.duckdb`：持久化本地历史库；DuckDB单写者，采集、回放、报告串行执行。
- `data/*.parquet`：源文件元数据、每次采集尝试、历史观测及已报道证据导出。
- `data/raw/<source>/<attempt_uuid>/{response.bin,metadata.json}`：每次尝试独立文件、HTTP状态、UTC、URL、SHA256，失败响应亦保留。
- `published_at=NULL` 表示官方公布时间未核实。SHFE附件内交易日不是公布日；NOAA基期不能混接；HTTP Last-Modified、ZIP文件修改时间和研究报告日期都不替代官方release。
- 旧值不因原页面覆盖而消失，所有观测vintage仍在`observation`，`latest`仅取有效最新版本。
- 首轮开发日行情的字段名类型经过明确schema迁移：同值重复行保留为`duplicate_schema_migration`，排除规范视图；冲突标为`unverified_schema_conflict`。不删除原始证据。
- 授权CSV/XLSX导入器是库函数`parse_authorized`，需显式`authorized=True`，未知发布时间留空，冲突重复整文件拒绝；尚未接任何商业数据授权账户。

## 持久化与Actions

本次真实库保存在选定工作区磁盘，原始受限文件和完整库不提交公开GitHub。工作区不等于已配置长期云存储。手动workflow默认仅测试；**不设schedule、不触碰08:00任务**。真实采集前必须配置私有S3：

- `CTA_S3_BUCKET`, `CTA_AWS_ACCESS_KEY_ID`, `CTA_AWS_SECRET_ACCESS_KEY`, `CTA_AWS_REGION`，均为GitHub Secrets；IAM只允许专用前缀读写和桶Public Access Block查询。
- 安装`boto3`（workflow已安装）；`persistence.sync`先检查四项Block Public Access全部开启，再保存SSE-AES256快照及SHA256索引。下次手动运行先恢复，再采集，再持久化。保留快照，不只覆盖数据库。
- 本次没有云存储凭据，**S3及Actions真实联网取数未部署/未实测**，不得声称云端持续运行。新增存储还需实际恢复演练、权限复核、失败恢复和成本验证。
- 预计单次年度文件约73MiB，解压约75MiB；Python逐格解析需数分钟。每日单日JSON/HTML请求量很小；完整年度不应每日反复下载。当前默认完整回填仅供手动验证，尚未优化成生产增量计划。

## 交接门槛

M1尚不能整体标完成：仓单全年回填和外部持久化尚有缺口。M2未完成：SR/CF/OI/RM、M/C/P/Y的官方逐合约及P0产业指标仍未接通。主力换月规则、可交易收益序列、完整基差与价差计算和实时报表评级尚未实现。禁止以通用框架代替这些真实数据验收。

随后顺序：修复仓单历史归档发现；为CZCE/DCE取得可合法稳定下载的官方文件；按产品注册表逐指标补源/授权导入；国投公告附件结构与费率基线人工核验；最后开展五个境内交易日并行差分验证。现有08:00推送保留原状。

离线回放集成验收（只读取已保存官方文件，不需外网）：

```bash
.venv/bin/python scripts/replay.py --data data --year 2025
```

回放每个成功文档，任何新增记录立即失败；重跑不会把首次发现时刻更新成现在。检查原始文件完整性时可将`source_document.sha256`与对应`data/raw/.../response.bin`重算核对。移动/恢复数据根目录时，元数据中的raw_path相对于根目录保存。
