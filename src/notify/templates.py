"""钉钉消息模板：汇总 markdown + 重点 actionCard。"""
from __future__ import annotations

import math
from datetime import datetime
from typing import List

from ..models import StockEvaluation
from ..utils import safe_fmt


SLOT_LABEL = {"midday": "午盘 12:00", "close": "收盘 16:00"}


def _fmt(v: float, digits: int = 2) -> str:
    return safe_fmt(v, digits=digits, default="N/A")


def render_summary_markdown(
    slot: str,
    total: int,
    vetoed: int,
    candidates: int,
    ai_called: int,
    ai_allowed: int,
    focus: List[StockEvaluation],
    failures: dict | None = None,
) -> tuple[str, str]:
    label = SLOT_LABEL.get(slot, slot)
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = f"A股选股汇总 - {label}"
    lines: List[str] = []
    lines.append(f"## A股选股汇总 - {label}")
    lines.append(f"> 时间: {date}")
    lines.append("")
    lines.append("**统计**")
    lines.append(f"- 候选池样本(成交额∪热榜): **{total}**")
    lines.append(f"- 一票否决: **{vetoed}**")
    lines.append(f"- 信号命中≥2: **{candidates}**")
    lines.append(f"- AI 调用: **{ai_called}**")
    lines.append(f"- AI 允许: **{ai_allowed}**")
    lines.append(f"- 重点股票: **{len(focus)}**")
    if failures:
        lines.append("")
        lines.append("**异常**")
        for k, v in failures.items():
            lines.append(f"- {k}: {v}")
    if focus:
        lines.append("")
        lines.append("**重点股票（按AI评分降序）**")
        lines.append("")
        lines.append("| 代码 | 名称 | 来源 | 涨跌幅 | 成交额(亿) | 命中 | 评分 | 理由 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for e in focus:
            s, a = e.snapshot, e.ai
            reason = (a.reason or "").replace("|", "/")[:40]
            src = "+".join(s.sources) or "-"
            lines.append(
                f"| {s.code} | {s.name} | {src} | {s.pct_change:.2f}% | {s.turnover/1e8:.2f} | {e.strategy.hits} | {a.score} | {reason} |"
            )
    else:
        lines.append("")
        lines.append("> 本时段无重点股票")
    return title, "\n".join(lines)


def render_focus_action_card(ev: StockEvaluation, slot: str) -> tuple[str, str, str, str]:
    s, a, ind, st = ev.snapshot, ev.ai, ev.indicators, ev.strategy
    label = SLOT_LABEL.get(slot, slot)
    title = f"重点关注 {s.name}({s.code}) 评分{a.score}"
    text_lines = [
        f"### 重点关注 - {label}",
        f"**{s.name} ({s.code})**  来源: {('+'.join(s.sources)) or '-'}",
        "",
        f"- 最新价: {_fmt(s.price)}  涨跌幅 **{_fmt(s.pct_change)}%**",
        f"- 成交额: **{s.turnover/1e8:.2f} 亿**  振幅 {_fmt(s.amplitude)}%",
        f"- MA5/10/20: {_fmt(ind.ma5)} / {_fmt(ind.ma10)} / {_fmt(ind.ma20)}",
        f"- BIAS10/20: {_fmt(ind.bias10)}% / {_fmt(ind.bias20)}%  20日累计: {_fmt(ind.pct_20d)}%",
        f"- MACD HIST: {_fmt(ind.macd_hist,3)}  RSI14: {_fmt(ind.rsi14,1)}  量比: {_fmt(ind.volume_ratio)}",
        f"- 近20日 高/低: {_fmt(ind.high20)} / {_fmt(ind.low20)}  52周高: {_fmt(ind.high52w)}",
        f"- AI 评分: **{a.score} / 100**  判定: **{'允许买入' if a.allow else '不建议'}**",
        "",
        f"> {a.reason}",
        "",
        f"**命中信号 ({st.hits})**: {', '.join(st.signals) if st.signals else '无'}",
    ]
    text = "\n".join(text_lines)
    single_title = "查看东方财富行情"
    single_url = f"https://quote.eastmoney.com/{_em_market(s.code)}{s.code}.html"
    return title, text, single_title, single_url


def _em_market(code: str) -> str:
    code = code.strip()
    if code.startswith(("60", "68", "9")):
        return "sh"
    if code.startswith(("00", "30", "2")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "sh"
