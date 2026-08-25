# 数据源与口径

## NOAA CPC ONI

- 地址：`https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt`
- 字段：重叠三个月季节、年份、Niño 3.4 SST、ONI 异常值。
- 本项目把每个季节映射到中间月月初，仅用于对齐；原始季节标签始终保留。

## NOAA RONI、SOI 与 DMI

- RONI：`https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/`。NOAA自2026年起将相对Niño3.4三个月均值用于官方ENSO监测，以降低热带整体变暖对绝对海温异常的干扰；
- SOI：`https://www.cpc.ncep.noaa.gov/data/indices/soi`。本项目用负SOI确认厄尔尼诺的大气耦合，不用海温单指标独立升级气候门槛；
- DMI：`https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data`。作为IOD方向修正项，用于判断印马与印度降水遥相关可能被增强或抵消，不单独触发配置。

## NASA POWER

- 地址：`https://power.larc.nasa.gov/api/temporal/daily/point`
- 参数：`PRECTOTCORR`、`GWETROOT`、`community=AG`、经纬度、起止日期、JSON。
- 当前覆盖马来西亚、印度尼西亚、印度糖区和巴西中南部糖区，共12个代表点；仍不等同于种植面积加权天气。正式研究应增加行政区网格、产量权重并与当地官方站点交叉校验。

## Tushare Pro（正式期货数据）

- `fut_mapping`：连续/主力代码到实际月合约的逐日映射；
- `fut_daily`：实际月合约 OHLC、结算价、成交量、持仓量；
- P 连续代码：`P.DCE`；SR 连续代码：`SR.ZCE`；
- 权限和积分以 Tushare 官方账户为准，Token 只从环境变量读取。
- 可通过 `TUSHARE_HTTP_URL` 指向兼容 Tushare Pro 协议的代理；代理地址和 Token 均不写入仓库。

该路径支持审计换月，但仍需在研究阶段明确：换月成交时点、价差处理、涨跌停、滑点、手续费和夜盘时间戳。

## 供给基本面

- MPOB棕榈油产量、库存、出口：Wind EDB `S5022944`、`S5022948`、`S5022955`，底层来源为 Malaysian Palm Oil Board；
- 马来西亚FFB单产：MPOB BEPI官方月表，页面 `https://bepi.mpob.gov.my/index.php/component/content/article/1241-ffb-yield-crude-palm-oil-yield-of-oil-palm-estates-2026?Itemid=482&catid=339`；
- 印度糖月度产量：Wind EDB `Z9808072`，底层来源为 ChiniMandi，单位为10万吨；
- 巴西中南部累计甘蔗压榨与制糖比：Wind EDB `S5201214`、`W5495204`，底层来源为 UNICA；
- 全球糖期末库存与总消费：Wind EDB `S5010514`、`S5010513`，底层来源为 USDA；库销比由本项目计算为期末库存÷总消费。
- 全球棕榈油期末库存与国内消费：USDA FAS `Oilseeds: World Markets and Trade` 2026年8月刊 Table 11；库销比由本项目计算，当前2026/27为预测值而非已实现值。

结构化文件位于 `data/processed/fundamentals/`。同季节同比用于月度/双周位置判断；UNICA累计压榨每个榨季重置，不能直接用相邻期增速解释；印度糖月度值季节性强，下一步还需增加榨季累计口径。

## AkShare/Sina（探索性后备）

- 代码：`P0`、`SR0`；
- 优点：无需 Token，适合验证端到端管线；
- 限制：供应商未在返回数据中披露每日主力合约和回溯调整规则。

所有基于该源生成的文件名和 manifest 均标记 `exploratory`。

## 最低数据质量门禁

- 日期可解析且无重复；
- 价格非空且大于零；
- 合约数据保留实际合约代码；
- 信号至少滞后一个交易日；
- 披露交易日历、时区、缺失值、换月政策和成本；
- 未完成样本外、参数平台、成本与市场状态检验前，不标记为可部署。

## 历史复盘专用数据源与边界

- FAOSTAT Crops and livestock products：`https://bulks-faostat.fao.org/production/Crops_Livestock_E_All_Data_(Normalized).zip`。保留印度/巴西甘蔗与马来西亚/印度尼西亚油棕果的产量、收获面积、单产、原始单位和 `Flag`。FAOSTAT 日历年不等同于作物年度，历史基准只使用目标年前五个已知年份，不回填未来修订值。
- World Bank Commodity Price Data（Pink Sheet）：`https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/related/CMO-Historical-Data-Monthly.xlsx`。使用月度 Palm oil（USD/mt）和 Sugar, world（USD/kg）现货基准。它们不是中国期货可交易连续合约，不能代表换月、基差、保证金或实际成交成本。
- 历史气候复用 NOAA CPC ONI/SOI、NOAA PSL DMI 与 NASA POWER。事件统一以 ONI 连续至少五个重叠季达到 0.5℃ 定义；天气和价格固定为 T0 至 T+12/T+18 窗口，ONGOING 事件不进入规律强弱统计。
- 历史 CTA 月频代理只设置 Medium/Slow，目标年化波动统一为 20%，仓位滞后一期、杠杆上限 2 倍、单边换手成本 2bp；现有 P/SR 日频 Fast/Medium/Slow 只在可覆盖年份展示，否则标为 `UNAVAILABLE`。
- `data/processed/history/manifest.json` 记录每个产物的来源、行数、日期范围、下载时间和 SHA-256。历史复盘是独立描述性旁路，不把规律标签、价格结果或 CTA 代理收益导入当前 CTA 配置门槛、仓位建议或置信度修正。
