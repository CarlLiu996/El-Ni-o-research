# 从历史库到每日简报替代：实施与切换门禁

本轮新增独立 daily 流程，不修改或停止原ChatGPT北京时间08:00任务。既有 report 命令保留为诊断历史库；daily 用于冻结日报，不会把所有旧历史图反复当作当天新增。

## 手动运行

在 daily_agri_cta/ 安装项目依赖后：

    python -m agri_cta.cli daily --mode production --report-date 2026-11-18 --output data/daily/2026-11-18
    python -m agri_cta.cli daily --mode parallel --refresh --output data/parallel/current

production 必须以北京时间07:50冻结。冻结尚未发生时拒绝生成生产日报。parallel 使用实际时点并明确标注，不能倒填为当日07:50已知数据。--refresh 逐源串行取数，一个源失败仍保留其他源的尝试和缺口；不并行写DuckDB。

数据包包含 overview.csv（完整九品种总览）、source_coverage.csv（逐项P0）、evidence_ledger.csv、risk_ledger.json、report.md、report_manifest.json、briefing.json 和 readiness.json。完整包在私有data目录；不自动把受限完整行情上传公开GitHub。

## 证据与图表

- 先按实际首次可见和官方发布时间过滤，再选择vintage。已知旧月份修订单列，避免被新月份遮蔽。
- 上期已知的周期变化不能再次当作本期新变化。历史回填仅保留每序列最新观察及真实修订，不把几百个月同时当作今日新增。
- 仓单变化达到100吨或1%、命名期货价格变化达到0.5%、流动性足够的成交持仓变化达到5%才进入变化筛选；这些仅为研究分流阈值，不是自动评级/交易规则。开高低价保留账本，不单独触发研究信号。
- 只有本期实质变化且历史样本充足才画图。月频至少30点、日频至少180点；不足不画，月频断档不插值。
- 原星级只标“历史基线未复核”，当前评级留空，P0不足不自动升级。
- 现货基差缺少同日、同合约、地区及质量两腿时拒绝；跨来源冲突不混成官方修订。

## 上线门禁

readiness.json 分别检查生产冻结时点、九品种P0、研究复核、国投最新公告与费率范围、私有存储恢复、真实定时采集、真实投递回执以及五个有官方交易日证明的并行复核记录。任何一项未通过，replacement_ready=false。当前数据仍有CZCE/DCE及产业P0缺口，不能接管。

生成报告仅写daily_run/daily_evidence；不把文件生成或HTTP200当作已送达。delivery_attempt独立记录：accepted_delivery_unknown与delivered明确分开。只有可信接收端明确返回status=delivered及receipt_id才登记投递验证；这仍不代表用户已阅读。

可选HTTPS webhook适配器仅在用户明确配置CTA_DELIVERY_WEBHOOK_URL后使用；默认不配置、不会发送。delivery-probe显式发送“链路测试”而非正式简报；未过门禁的parallel报告不能当作生产替代投递。ChatGPT原任务的通知状态仍需平台实际回执核验，本适配器不会冒充已经接入ChatGPT。

    python -m agri_cta.cli delivery-probe --data data
    python -m agri_cta.cli delivery-status --data data --run-id <run_id>

## 持久化与调度

当前没有私有存储凭据。沿用原私有S3备份实现作为待配置接口；S3真实恢复、定时运行和投递未验收，不能把本地工作区当作长期外部存储。7:10采集/7:50冻结/8:00投递的调度方案尚未启用。

2026-11-18本轮工作区在新模块测试前被平台标为unavailable。断开前取得11月16–17日官方增量，但尚未完成本轮数据包与哈希复核，不据此发布新的已验证覆盖数。已提交的历史验收仍以2026-11-16记录为准。

已转用GitHub保存本轮代码和synthetic-only PR CI，不向公开CI上传受限原文/完整库。新增模块需以对应提交的CI结果为测试依据。CI成功也只证明代码与合成场景，不等于真实定时、私有云存储或投递已完成。原08:00任务保持不变，待实源补齐、部署验证与五交易日对照后再切换。
