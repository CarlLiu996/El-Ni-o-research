# 2026-11-19：修复日报只读契约、读不到真实数据的问题

## 根因与实际变更

11月19日原ChatGPT任务已执行，但只读到GitHub的AGENTS和报告契约，未访问Codex数据工作区。上轮更新prompt没有建立数据交接，不足以让该任务读取私有数据库。原任务回读显示非当前会话所有、environment_selection=inherited且environments=null。

本轮从当前Codex项目会话创建了项目数据版任务，回读确认is_self_owned_automation=true，继承当前实际云环境、cwd=/workspace。原任务已暂停；没有修改其他四个定时任务。计划名额已满，因此同一个新版任务分两个阶段：北京时间07:00准备数据且不另发日报，08:00读取生产冻结包并发布唯一一份研究报告。对应配置存于config/daily_briefing_prompt.md。

新任务ID：Automation_d98151445b48819181272485b7fa9dba。原暂停任务：6a9bc7951fb08191be0301c8191372a1。第一次计划准备为2026-11-20 07:00，研究报告为08:00。**本轮未把下一次自动运行、用户通知回执或私有S3恢复标成已通过。**

## 已实取和读取

常规沙箱的代理桥接不可达。按云环境网络诊断保留代理/TLS配置，在沙箱外对同一官方文件重试返回HTTP200；不是绕过网站403/412/429。

- 已真实更新11月18日SHFE/INE行情：151个字段观测；仓单3条。原文、URL、实际采集时间、SHA256保存于原私有data根目录。
- MPOB本年表、CPC复取新增0；HadISST复取新增0。
- PSL ERSSTv6本轮同基期原文件出现919个月数值修订，保留新旧vintage；946个月覆盖不变，未拼接CPC/HadISST。原文页脚仍为ERSST V6、1981–2010，不能把修订称作新增919个月或今日气候突然变化。
- 国投最新交易提示已实取，完整客户费率范围基线仍未完成。
- 从实际automation provider记录恢复11月18日完整报告和9个历史评级；11月19日报告也已归档，其评级缺项不会擦掉18日观点。

私有交接首次实测冻结：2026-11-19T00:56:26.148645+00:00，即北京时间08:56恢复版，mode=parallel。**没有倒填为今天07:50已经采集或落盘。**

冻结库41710条as-of观测，286个使用中的原始文档哈希复核通过；行情截止日2026-11-18，完整上期报告日期2026-11-18，历史评级可读9个。内嵌图表输入14项；没有生成PNG图片包。恢复版已通过独立只读打开冻结DuckDB、Parquet与所有包文件SHA校验。

具体可复核样例：11月18日RU仓单125190吨；NR仓库仓单3527吨、厂库仓单明确为0。官方数据不再只停留在网页摘要线索。仓单变化仍须解释注销、转现货与实际出库，不能自动升评级。

## 持久化交接协议

新增agri_cta.continuity：

- report_snapshot保存完整原文、真实provider run和发布时间、实际归档时点及内容哈希；同一次实际报告重放幂等。
- rating_snapshot只解析九品种总览的评级列，不从正文示例或metrics.yaml猜星级。
- 新报告没有评级时保留之前有明确日期的历史观点；“旧评级→待复核”和生成草稿不冒充当前或已发布评级。
- published_result与generated_draft分开。未来运行先导入实际已完成provider结果，生成前归档草稿不能当已送达。

新增agri_cta.handoff：

- 按实际freeze先过滤可见信息再选vintage，创建独立frozen.duckdb和observations_asof.parquet；冻结库物理上不含截止之后才取得或公布的记录。
- 保存previous_report.md、continuity_state.json、rating_snapshot.json、evidence_ledger.csv、source_coverage.csv、chart_data.json、qa.json和manifest.json。
- 全部包文件及所用原文哈希检查通过后，才原子更新data/handoff/latest.json；旧包保留。失败或坏原文不能替换上次有效指针。
- verify逐文件校验、实际只读打开冻结库并检查记录数/时间过滤；不把仅能读取契约算作成功读取数据。
- chart_data提供真实序列和两期对比，标样本、频率、单位、合约、价种及缺失，不专门出图像；待核资料不进入已验证图。两期对比明确n=2。

完整库、原文及报告原文只留私有工作区，没有公开上传。公开仓库仅源码、synthetic测试、说明及派生验收元数据。

## 实际命令

代码目录：/workspace/cta-runtime-20261119/daily_agri_cta。私有数据根：/workspace/El-Ni-o-research/daily_agri_cta/data。现有Python：/workspace/El-Ni-o-research/.venv/bin/python。设置PYTHONPATH=src。

```bash
python -m agri_cta.cli collect --data <private-data> --source all --start 2026-11-18 --end 2026-11-18
python -m agri_cta.cli watch-risk --data <private-data> --output <private-data>/risk_2026_11_19
python -m agri_cta.handoff archive-report --data <private-data> --report-json <actual-provider-result.json>
python -m agri_cta.handoff prepare --data <private-data> --report-date 2026-11-19 --mode parallel
python -m agri_cta.handoff verify --data <private-data> --report-date 2026-11-19
python -m pytest -q
```

测试：86 passed、97条依赖弃用提示、9.53秒。新增15项专门覆盖报告幂等、评级缺项保留、草稿不晋升、截断/哈希/日期错误拒绝、物理冻结无未来修订、旧包/篡改拒读、坏原文不更新指针，以及晚取数据不能冒充07:50生产、按照provider scheduled_for而不是延迟启动时间区分07/08阶段。

## 尚未完成

九品种P0仍只核验5/47，CZCE/DCE和产业指标缺口没有因为读库修复而变成完成。当前评级复核、完整公司费率范围、外部私有存储恢复和五交易日对照仍未通过。云工作区文件这次恢复与读取成功，不等于外部S3持久化已验收。未来任务若环境不可用须报警，不能无声退回纯网页搜索并声称读过库。

本轮范围是修复真实数据和历史观点的读取交接；自动定时执行及交付仍需下一次实际运行证据。
