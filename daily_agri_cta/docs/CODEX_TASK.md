# 请 Codex Cloud 在此仓库完成：九品种 CTA 日报历史库与增量取数

**仓库：** CarlLiu996/El-Ni-o-research
**独立目录：** daily_agri_cta/
**任务日期：** 2026-11-13
**目标：** 从“每次临时搜索”升级成“官方时间序列有源可查 + 历史回填 + 每日增量披露检测 + QA + 研究输入”，先完成可运行数据工程，不改现有 ChatGPT 北京时间08:00 自动化。

## 开始前阅读
1. \`daily_agri_cta/AGENTS.md\`
2. \`daily_agri_cta/README.md\`
3. \`daily_agri_cta/config/source_registry.csv\`
4. \`daily_agri_cta/config/metrics.yaml\`
5. \`daily_agri_cta/docs/DATA_CONTRACT.md\`
6. \`daily_agri_cta/docs/REPORT_CONTRACT.md\`
7. 仓库已有 \`CODEX_CONTEXT_ElNino_Agri_CTA.md\`；它是独立 ENSO 研究，不能覆盖。

## M0：云端环境诊断（先做）

在 Codex 执行环境中检查外网访问、Python、GitHub Actions 是否可用、官方源HTTP/HTML、robots与数据许可。输出 \`docs/CONNECTIVITY_AUDIT.md\`：每个源**能打开页面**还是**能获取多年的结构化数据**、是否需账户授权、是否存在反爬/限频/历史缺口、可靠解析方式、时区和数据延迟。**不能将网页可访问等同于已实现采集**。报告网络受限时不要编造成功记录。

## M1：最小可运行纵向切片（优先且必须真的运行）

实现标准 \`fetch -> raw snapshot -> parse -> validate -> DuckDB/Parquet -> report ledger\`：

1. **MPOB（官方）**：年表 https://bepi.mpob.gov.my/stat/web_report1.php?val=202684 作为2026年候选入口。核实其历史年度表的URL规则，回填至少2023–2026月度产量、棕榈油出口与月末总库存。保留修订值和原始官方页面。10月2026月末库存可用官方年表作为一致性单点验证，但绝不能为其他年份硬填假值。
2. **国内交易所（日度）**：优先挑一个交易所与品种，按**真实逐合约**回填至少1年（例如郑商所SR，亦可大商所P），含 close/settlement/volume/open_interest/contract/trade_date；再取仓单。记录每个文件的官方来源和交易日。之后扩展CZCE/DCE/SHFE/INE至九品种和NR。
3. **NOAA Niño3.4**：从 https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii 或 NOAA PSL正式数据下载可取得的历史序列；标明指数版本和1981–2010/1991–2020等基期，不把不同版本拼接。
4. 开展端到端重跑和测试：首次运行行数、再次运行新增行数（应为0），官方数据修订如何增加 vintage，漏数时错误如何显式记录。

**M1验收：** 至少3个真实、可复现、带URL/哈希/发布日期/观测日期的官方长序列：MPOB、一个国内交易所、NOAA。输出对应CSV样例、QA及至少一张真实历史图。未实现不可标完成。

## M2：扩展所有九品种

- 扩展交易所期货逐合约 OHLC/结算/成交量/持仓量/仓单，国内公开行情优先；构建规范主力换月规则，保存每次换月事件。
- 逐一扩展 config/metrics.yaml 中 P0 产业指标。能用公开官网取数则实现采集器；只能从 Wind/iFind/数据商合法获得的做**CSV/XLSX授权导入器**，附字段映射、去重和错误报告，不存任何密钥。
- MPOB、USDA、UNICA等官方发布为主，现货和专业机构为交叉验证。棉花公检、橡胶青岛库存、油脂基差优先修复已出现的日报漏报。
- 每品种有 source_coverage.csv：已查、最新数据所属日、最新发布时间、成功/缺失/授权待办。
- 由于国内部分期货/现货网站存在高反爬和数据使用限制，必须清楚列出不能自动回填的字段，不要进行违规爬取。

## M3：增量披露引擎与真实时间序列分析

实现:
- 两个 report freeze cutoff 的“首次可见信息”比较（依据 official publication/first seen），避免前日已报道再次当今日新增。
- 统计期早于披露日（例如11月8日青岛库存至11月12日才发布）必须正确判定为“今日新增披露/旧统计期”。
- 库存水平、环比/同比、同月季节性、历史ECDF分位；至少保证欠样本时跳过历史分位。
- 同日同合约同价格类型基差、2701-2705、Y-P/OI-Y/M-RM；当合约不存在或换月时按规则回退或拒算，不能偷换月份。
- 输出每日报表的 machine-readable evidence_ledger.csv + report_manifest.json + qa.json，并由真实时序生成图表。

## M4：国投期货变动监测

- 检索 https://www.sdicfutures.com.cn/annc/business/11098.html 官方交易提示（可能官网迁移，经站内链接查），逐篇比对上次检查后新增公告；检查九品种/NR、主力、2701及特殊合约。
- 将公司客户投机/套保保证金、交易所底线、涨跌停板、组合优惠/交割限制**分别建字段**。
- 官方动态表只出表头则保留 \`unverified\`，查交易提示文章；公告仅列新值时旧值是 NULL/unknown，不推算。
- 输出精简新增变动；没有已核实变动时须写基于已核实公告，不宣称整站费率全无变化。
- 单独检验RU2611/NR2611自然人清仓限制与保证金优惠退出不被误写成公司保证金率统一上调。

## M5：云端定时与交接（不要抢跑）

- 首先提供可手动运行的 GitHub Actions \`workflow_dispatch\` 和可复跑 \`python -m agri_cta.cli collect/backfill/validate/report\` 命令。
- 等真实数据通过审核，再增加北京时间07:00前后的增量调度（例如 GitHub Actions 23:10 UTC = 北京次日07:10，UTC固定+8）；避免过密请求和超出许可。
- 云端存储须持久化：GitHub Actions runner是临时机，不能仅写到 runner 目录。使用合法的私有对象存储/持久化DB/可访问 artifact，并在安全域内管理密钥。
- GitHub Actions schedule不保证精确触发。**原 ChatGPT 每天08:00任务保持不变**；在5个境内交易日并行核对“漏报、错误、差分、时间可得性”后，再提出对接方案。确认无法自动推送时明确告知，不能声称接管成功。

## PR 交付清单

- \`src/agri_cta/\` 真正采集/解析/QA/变动检测/图表模块及CLI。
- \`tests/\` synthetic fixture单测 + 合法小样例的集成测试；网络集成测试可以单独标签并提供真实运行日志。
- \`requirements.txt\` 或 \`pyproject.toml\`，无硬编码凭据。
- \`docs/CONNECTIVITY_AUDIT.md\`，所有无法合法稳定采集的源明细。
- \`data/raw\` 和大体积/受限数据必须 .gitignore；示例数据明确许可。
- \`reports/示例日期/\` 中的数据血缘、质量报告和可核验图表。
- PR 中注明：实测成功源数、年/月/日历史覆盖率、采集耗时、每日报告冻结时点、哪些指标仍依赖Wind/iFind授权、已通过测试与未通过项。

**重要：Codex 工作不是自行编造一个漂亮日报，而是让日报上每个数字都能沿血缘回溯到某日期、某一版官方或合规机构数据。**
