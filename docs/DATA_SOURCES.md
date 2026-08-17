# 数据源与口径

## NOAA CPC ONI

- 地址：`https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt`
- 字段：重叠三个月季节、年份、Niño 3.4 SST、ONI 异常值。
- 本项目把每个季节映射到中间月月初，仅用于对齐；原始季节标签始终保留。

## NASA POWER

- 地址：`https://power.larc.nasa.gov/api/temporal/daily/point`
- 参数：`PRECTOTCORR`、`community=AG`、经纬度、起止日期、JSON。
- 当前是代表点平均框架，不等同于种植面积加权降水。正式研究应增加行政区网格和产量权重。

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
