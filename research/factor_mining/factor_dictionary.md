# 因子字典

所有气候、天气和基本面变量至少滞后一个月；市场变量使用月末状态，预测从下一个月开始。

| 品种 | 层级 | 因子 | 公式/含义 |
|---|---|---|---|
| 棕榈油 | L1气候 | `oni` | ONI，滞后1个月 |
| 棕榈油 | L1局地天气 | `palm_dryness3` | 马来西亚与印尼3个月降雨异常的相反数 |
| 棕榈油 | L1局地天气 | `palm_soil_dryness3` | 马来西亚与印尼3个月土壤湿度异常的相反数 |
| 棕榈油 | L1作物滞后 | `palm_dryness_lag6` | 棕榈油主产区干旱信号滞后6个月 |
| 棕榈油 | L1作物滞后 | `palm_dryness_lag9` | 棕榈油主产区干旱信号滞后9个月 |
| 棕榈油 | L1作物滞后 | `palm_dryness_lag12` | 棕榈油主产区干旱信号滞后12个月 |
| 棕榈油 | L1作物滞后 | `palm_soil_dryness_lag6` | 棕榈油主产区土壤偏干信号滞后6个月 |
| 棕榈油 | L1作物滞后 | `palm_soil_dryness_lag9` | 棕榈油主产区土壤偏干信号滞后9个月 |
| 棕榈油 | L1作物滞后 | `palm_soil_dryness_lag12` | 棕榈油主产区土壤偏干信号滞后12个月 |
| 棕榈油 | L1交互 | `climate_stress` | 暖ENSO强度×局地干旱 |
| 棕榈油 | L2基本面 | `inventory_tightness` | MPOB库存相对过去同月的Z分数之相反数，滞后1个月 |
| 棕榈油 | L2基本面 | `production_shortfall` | MPOB产量相对过去同月的Z分数之相反数，滞后1个月 |
| 棕榈油 | L2基本面 | `export_strength` | MPOB出口相对过去同月的Z分数，滞后1个月 |
| 棕榈油 | L1×L2 | `stress_x_inventory` | 气候压力×正向库存紧张 |
| 棕榈油 | L3市场 | `mom3` | 过去3个月可交易收益动量 |
| 棕榈油 | L3市场 | `mom6` | 过去6个月可交易收益动量 |
| 棕榈油 | L4 CTA | `market_trend` | 中慢速同向时取其方向，否则为0 |
| 棕榈油 | L2×L3 | `fundamental_x_market` | 基本面紧张方向×3个月动量 |
| 棕榈油 | 全链条 | `full_conversion` | 气候压力×库存紧张×正向价格动量 |
| 白糖 | L1气候 | `oni` | ONI，滞后1个月 |
| 白糖 | L1局地天气 | `india_dryness3` | 印度糖蔗区3个月降雨异常的相反数 |
| 白糖 | L1局地天气 | `brazil_dryness3` | 巴西中南部糖蔗区3个月降雨异常的相反数 |
| 白糖 | L1作物日历 | `brazil_growth_dryness_legacy` | 巴西12月至3月生长期偏干信号，向后保留8个月 |
| 白糖 | L1作物日历 | `brazil_harvest_dryness` | 仅保留巴西4月至11月压榨期偏干信号 |
| 白糖 | L1作物长势 | `india_ndvi_stress` | 印度糖蔗NDVI Z分数的相反数 |
| 白糖 | L1季风 | `monsoon_shortfall` | 印度季风累计偏差的相反数 |
| 白糖 | L1作物日历 | `india_monsoon_legacy` | 印度9月季风最终偏差，自10月起保留至下一榨季 |
| 白糖 | L1交互 | `climate_stress` | 暖ENSO强度×印度局地干旱 |
| 白糖 | L2基本面 | `india_output_shortfall` | 印度糖产量相对过去同月的Z分数之相反数，滞后1个月 |
| 白糖 | L2基本面 | `unica_crush_shortfall` | UNICA压榨量相对过去同月的Z分数之相反数，滞后1个月 |
| 白糖 | L1×L2 | `stress_x_output` | 气候压力×印度产量短缺 |
| 白糖 | L3市场 | `mom3` | 过去3个月可交易收益动量 |
| 白糖 | L3市场 | `mom6` | 过去6个月可交易收益动量 |
| 白糖 | L4 CTA | `market_trend` | 中慢速同向时取其方向，否则为0 |
| 白糖 | L2×L3 | `fundamental_x_market` | 基本面紧张方向×3个月动量 |
| 白糖 | 全链条 | `full_conversion` | 气候压力×产量短缺×正向价格动量 |