# 厄尔尼诺 × 农产品 CTA 每日复盘 — 2026-08-17

## 结论先行

当前总状态：**KEEP_MONITORING**。气候=CONFIRMED，基本面=INCOMPLETE，市场/CTA=NOT_CONFIRMED。

只有气候、供给基本面和 Medium/Slow CTA 趋势同时确认后，才进入 CTA 配置讨论；当前输出不是直接交易建议。

## 三层监控

- 气候：ONI 1.39（El Nino），三个月变化 1.28。
- 降水 india_sugar：近 30 日 294.79 mm，历史同期偏差 59.2%，连续少雨 1 天。
- 降水 malaysia_palm：近 30 日 233.93 mm，历史同期偏差 -3.4%，连续少雨 5 天。
- 基本面：已就绪 0/8；缺失项保持阻断，不以价格替代供给证据。

## 市场与 CTA Proxy

| 品种 | 主力合约 | 收盘 | 20日价格 | 期限结构 | Fast 20日 | Medium 20日 | Slow 20日 |
|---|---:|---:|---:|---|---:|---:|---:|
| P | P2701.DCE | 9,838 | 6.6% | contango -0.5% | -2.6% | 1.0% | 3.3% |
| SR | SR2701.ZCE | 5,309 | 3.6% | contango -0.3% | -4.2% | -4.7% | -4.6% |

## 较上次变化

- 首次生成监控快照，暂无可比历史。

## 告警

- [CRITICAL] `FUND_MPOB_PRODUCTION`：基本面指标缺失或过期：MPOB 产量
- [CRITICAL] `FUND_MPOB_INVENTORY`：基本面指标缺失或过期：MPOB 库存
- [CRITICAL] `FUND_MPOB_EXPORTS`：基本面指标缺失或过期：MPOB 出口
- [CRITICAL] `FUND_FFB_YIELD`：基本面指标缺失或过期：FFB 单产
- [CRITICAL] `FUND_INDIA_SUGAR_OUTPUT`：基本面指标缺失或过期：印度糖产量/压榨
- [CRITICAL] `FUND_UNICA_CRUSH`：基本面指标缺失或过期：UNICA 甘蔗压榨
- [CRITICAL] `FUND_UNICA_SUGAR_MIX`：基本面指标缺失或过期：UNICA 制糖比
- [CRITICAL] `FUND_GLOBAL_SUGAR_STOCK_USE`：基本面指标缺失或过期：全球糖库销比

## 研究缺口

- 境外 BMD FCPO、ICE Sugar No.11、ICE Coffee 长历史尚未接入，1982/83 与 1997/98 事件无法验证
- 管理人映射按项目目标延后至尽调数据可用后

## 下一次复核

1. 更新 MPOB、FFB、印度糖、UNICA 与全球库销比后重新判断供给兑现。
2. 检查 P/SR Medium 与 Slow 20 日收益、仓位方向和持续天数是否共同改善。
3. 检查近月价差是否与趋势方向一致，避免仅由换月或单一合约驱动。
4. 取得境外长历史后补做 1982/83、1997/98、2015/16、2023/24 事件复盘。
