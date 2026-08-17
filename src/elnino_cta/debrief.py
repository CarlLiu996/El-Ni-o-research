from __future__ import annotations

from pathlib import Path
from typing import Any


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def render_debrief(snapshot: dict[str, Any]) -> str:
    gate = snapshot["research_gate"]
    climate = snapshot["layers"]["climate"]
    fundamentals = snapshot["layers"]["fundamentals"]
    lines = [
        f"# 厄尔尼诺 × 农产品 CTA 每日复盘 — {snapshot['as_of']}",
        "",
        "## 结论先行",
        "",
        f"当前总状态：**{gate['overall']}**。气候={gate['climate']}，基本面={gate['fundamentals']}，市场/CTA={gate['market_cta']}。",
        "",
        "只有气候、供给基本面和 Medium/Slow CTA 趋势同时确认后，才进入 CTA 配置讨论；当前输出不是直接交易建议。",
        "",
        "## 三层监控",
        "",
        f"- 气候：ONI {_num(climate['oni']['oni'])}（{climate['oni']['phase']}），三个月变化 {_num(climate['oni']['change_3m'])}。",
    ]
    for region, metrics in climate["rainfall"]["regions"].items():
        lines.append(
            f"- 降水 {region}：近 30 日 {_num(metrics['rain_30d_mm'])} mm，历史同期偏差 {_pct(metrics['anomaly_pct'])}，连续少雨 {metrics['consecutive_dry_days']} 天。"
        )
    lines.extend([
        f"- 基本面：已就绪 {fundamentals['available']}/{fundamentals['required']}；缺失项保持阻断，不以价格替代供给证据。",
        "",
        "## 市场与 CTA Proxy",
        "",
        "| 品种 | 主力合约 | 收盘 | 20日价格 | 期限结构 | Fast 20日 | Medium 20日 | Slow 20日 |",
        "|---|---:|---:|---:|---|---:|---:|---:|",
    ])
    for symbol in ("P", "SR"):
        market = snapshot["layers"]["market"][symbol]
        cta = snapshot["layers"]["cta"][symbol]
        term = market["term_structure"]
        lines.append(
            f"| {symbol} | {market['contract']} | {_num(market['close'], 0)} | {_pct(market['price_return_20d'])} | {term.get('curve', '—')} {_pct(term.get('front_second_spread_pct'))} | {_pct(cta['fast']['net_return_20d'])} | {_pct(cta['medium']['net_return_20d'])} | {_pct(cta['slow']['net_return_20d'])} |"
        )
    lines.extend(["", "## 较上次变化", ""])
    changes = snapshot.get("changes")
    if not changes:
        lines.append("- 首次生成监控快照，暂无可比历史。")
    else:
        lines.append(f"- 对比日期：{changes['previous_as_of']}；研究门禁变化：{'是' if changes['gate_changed'] else '否'}。")
        lines.append(f"- P/SR 收盘变化：{_pct(changes['P_close_change_pct'])} / {_pct(changes['SR_close_change_pct'])}。")
        lines.append(f"- P Medium/Slow 20日收益变化：{_pct(changes['P_medium_pnl_20d_delta'])} / {_pct(changes['P_slow_pnl_20d_delta'])}。")
        lines.append(f"- SR Medium/Slow 20日收益变化：{_pct(changes['SR_medium_pnl_20d_delta'])} / {_pct(changes['SR_slow_pnl_20d_delta'])}。")
        if changes["added_alerts"]:
            lines.append(f"- 新增告警：{', '.join(changes['added_alerts'])}。")
        if changes["cleared_alerts"]:
            lines.append(f"- 已解除告警：{', '.join(changes['cleared_alerts'])}。")
    lines.extend(["", "## 告警", ""])
    if snapshot["alerts"]:
        for alert in snapshot["alerts"]:
            lines.append(f"- [{alert['severity'].upper()}] `{alert['code']}`：{alert['message']}")
    else:
        lines.append("- 无告警。")
    lines.extend(["", "## 研究缺口", ""])
    lines.extend(f"- {item}" for item in snapshot["scope_gaps"])
    lines.extend([
        "",
        "## 下一次复核",
        "",
        "1. 更新 MPOB、FFB、印度糖、UNICA 与全球库销比后重新判断供给兑现。",
        "2. 检查 P/SR Medium 与 Slow 20 日收益、仓位方向和持续天数是否共同改善。",
        "3. 检查近月价差是否与趋势方向一致，避免仅由换月或单一合约驱动。",
        "4. 取得境外长历史后补做 1982/83、1997/98、2015/16、2023/24 事件复盘。",
        "",
    ])
    return "\n".join(lines)


def write_debrief(snapshot: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"debrief_{snapshot['as_of'].replace('-', '')}.md"
    path.write_text(render_debrief(snapshot), encoding="utf-8")
    (output_dir / "latest.md").write_text(render_debrief(snapshot), encoding="utf-8")
    return path
