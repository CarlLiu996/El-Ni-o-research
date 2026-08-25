# 厄尔尼诺 × 农产品 CTA 每日复盘 — 2026-08-25

## 结论先行

当前总状态：**KEEP_MONITORING**。气候=CONFIRMED，基本面=INCOMPLETE，市场/CTA=NOT_CONFIRMED。

只有气候、供给基本面和 Medium/Slow CTA 趋势同时确认后，才进入 CTA 配置讨论；当前输出不是直接交易建议。

## 三层监控

- 气候状态：RONI 1.00（El Nino），ONI 1.39；SOI -2.40（El Nino coupling），DMI 0.15（Neutral）。
- 产区 brazil_cs_sugar：近30日降水 1.13 mm，历史同期偏差 -84.4%，连续少雨 30 天，根区湿度偏差 -22.0%。
- 产区 india_sugar：近30日降水 215.12 mm，历史同期偏差 35.5%，连续少雨 4 天，根区湿度偏差 5.4%。
- 产区 indonesia_palm：近30日降水 170.75 mm，历史同期偏差 -2.3%，连续少雨 5 天，根区湿度偏差 -9.2%。
- 产区 malaysia_palm：近30日降水 306.28 mm，历史同期偏差 53.5%，连续少雨 5 天，根区湿度偏差 -2.6%。
- 基本面：已就绪 8/9；缺失项保持阻断，不以价格替代供给证据。

## 市场与 CTA Proxy

| 品种 | 主力合约 | 收盘 | 20日价格 | 期限结构 | Fast 20日 | Medium 20日 | Slow 20日 |
|---|---:|---:|---:|---|---:|---:|---:|
| P | P2701.DCE | 9,838 | 6.6% | contango -0.5% | -5.1% | 2.0% | 6.7% |
| SR | SR2701.ZCE | 5,309 | 3.6% | contango -0.3% | -7.2% | -8.3% | -8.3% |

## 较上次变化

- 首次生成监控快照，暂无可比历史。

## 告警

- [CRITICAL] `P_PRICE_STALE`：P 期货数据已过期
- [CRITICAL] `SR_PRICE_STALE`：SR 期货数据已过期
- [WARNING] `BRAZIL_CS_SUGAR_DRY`：brazil_cs_sugar 近 30 日降水显著低于历史同期
- [CRITICAL] `FUND_UNICA_SUGAR_MIX`：基本面指标缺失或过期：UNICA 制糖比

## 研究缺口

- 境外 BMD FCPO、ICE Sugar No.11、ICE Coffee 长历史尚未接入；早期事件仅能用世界银行现货基准复盘，不能验证可交易期货与跨市场CTA
- 管理人映射按项目目标延后至尽调数据可用后

## 下一次复核

1. 更新 MPOB、FFB、印度糖、UNICA 与全球库销比后重新判断供给兑现。
2. 检查 P/SR Medium 与 Slow 20 日收益、仓位方向和持续天数是否共同改善。
3. 检查近月价差是否与趋势方向一致，避免仅由换月或单一合约驱动。
4. 取得境外期货长历史后，用可交易合约替换世界银行现货代理并复核早期事件。
