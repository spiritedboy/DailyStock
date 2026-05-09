"""HTML 报告渲染。

输出路径：{reports_dir}/{YYYY-MM}/{MM-DD}-{noon|afternoon}.html
对外 URL：{report_host}/{YYYY-MM}/{MM-DD}-{noon|afternoon}.html
"""
from __future__ import annotations

import html
import math
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ..models import StockEvaluation

SLOT_FILE_LABEL = {"midday": "noon", "close": "afternoon"}
SLOT_DISPLAY = {"midday": "中午 12:00", "close": "下午 16:00"}


def _slot_to_filename(slot: str) -> str:
    return SLOT_FILE_LABEL.get(slot, slot)


def report_paths(
    reports_dir: Path,
    report_host: str,
    run_date: str,
    slot: str,
) -> Tuple[Path, str]:
    """返回 (本地文件路径, 对外 URL)。"""
    dt = datetime.strptime(run_date, "%Y-%m-%d")
    month = dt.strftime("%Y-%m")
    day_slot = f"{dt.strftime('%m-%d')}-{_slot_to_filename(slot)}"
    rel = f"{month}/{day_slot}.html"
    local = Path(reports_dir) / month / f"{day_slot}.html"
    url = f"{report_host.rstrip('/')}/{rel}"
    return local, url


def _fmt(v, nd: int = 2) -> str:
    try:
        f = float(v)
        if math.isnan(f):
            return "-"
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  margin: 24px auto; max-width: 1080px; padding: 0 16px; line-height: 1.55; color: #222; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 28px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #ddd; }
.meta { color: #666; font-size: 13px; margin-bottom: 12px; }
.stat { display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 8px 0 16px; font-size: 13px; }
.stat span b { color: #1a73e8; }
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px; }
th, td { border: 1px solid #e0e0e0; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f5f7fa; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
.pos { color: #d93025; font-weight: 600; }
.neg { color: #188038; font-weight: 600; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-right: 4px; }
.tag-tov { background: #e8f0fe; color: #1a73e8; }
.tag-hot { background: #fce8e6; color: #d93025; }
.tag-focus { background: #fef7e0; color: #b06000; }
.tag-allow { background: #e6f4ea; color: #137333; }
.tag-deny { background: #fce8e6; color: #c5221f; }
details { margin: 4px 0; }
summary { cursor: pointer; color: #1a73e8; font-size: 12px; }
.reason { color: #444; font-size: 12px; white-space: pre-wrap; }
.empty { color: #888; font-style: italic; }
footer { color: #888; font-size: 12px; margin-top: 24px; border-top: 1px solid #eee; padding-top: 8px; }
"""


def _pct_html(v) -> str:
    try:
        f = float(v)
        if math.isnan(f):
            return "-"
        cls = "pos" if f > 0 else ("neg" if f < 0 else "")
        sign = "+" if f > 0 else ""
        return f'<span class="{cls}">{sign}{f:.2f}%</span>'
    except (TypeError, ValueError):
        return "-"


def _sources_html(sources) -> str:
    if isinstance(sources, str):
        srcs = [s for s in sources.split(",") if s]
    else:
        srcs = list(sources or [])
    out = []
    for s in srcs:
        cls = "tag-tov" if s == "turnover" else ("tag-hot" if s == "ths_hot" else "tag")
        label = "成交额" if s == "turnover" else ("热榜" if s == "ths_hot" else s)
        out.append(f'<span class="tag {cls}">{label}</span>')
    return "".join(out) or "-"


def _render_pushed_table(pushed: List[StockEvaluation]) -> str:
    if not pushed:
        return '<p class="empty">本次未触发推送（候选池为空或 AI 全部否决）。</p>'
    rows = []
    for e in pushed:
        s, ind, st, ai = e.snapshot, e.indicators, e.strategy, e.ai
        score = ai.score if ai else 0
        allow_html = ""
        reason = ""
        if ai and ai.ok:
            allow_html = (
                '<span class="tag tag-allow">允许</span>' if ai.allow else
                '<span class="tag tag-deny">不建议</span>'
            )
            reason = ai.reason or ""
        elif ai:
            allow_html = '<span class="tag tag-deny">AI 异常</span>'
        focus_html = '<span class="tag tag-focus">重点</span>' if e.is_focus else ""

        details_lines = []
        if st.signals:
            details_lines.append("<b>命中信号</b>: " + _esc(", ".join(st.signals)))
        if st.misses:
            details_lines.append(
                "<b>未命中</b>:<br>" + "<br>".join(_esc(m) for m in st.misses)
            )
        if st.veto_reasons:
            details_lines.append(
                "<b>否决</b>: " + _esc(" | ".join(st.veto_reasons))
            )
        details_html = (
            "<details><summary>查看策略命中详情</summary><div class='reason'>"
            + "<br>".join(details_lines) + "</div></details>"
        ) if details_lines else ""

        ind_line = (
            f"MA5/10/20 {_fmt(ind.ma5)}/{_fmt(ind.ma10)}/{_fmt(ind.ma20)} · "
            f"BIAS10/20 {_fmt(ind.bias10)}%/{_fmt(ind.bias20)}% · "
            f"RSI14 {_fmt(ind.rsi14, 1)} · MACD {_fmt(ind.macd_hist, 3)} · "
            f"量比 {_fmt(ind.volume_ratio)}"
        )

        rows.append(f"""
<tr>
  <td>{_esc(s.code)}<br>{_esc(s.name)}<br>{_sources_html(s.sources)}{focus_html}</td>
  <td>{_fmt(s.price)}<br>{_pct_html(s.pct_change)}</td>
  <td>{s.turnover/1e8:.2f} 亿<br>振幅 {_fmt(s.amplitude)}%</td>
  <td>{score} / 100<br>{allow_html}</td>
  <td>{st.hits} 票<br>{_esc(", ".join(st.signals)) or "-"}</td>
  <td><div class="reason">{_esc(reason) or "-"}</div>
      <div class="reason" style="color:#888;margin-top:4px;">{_esc(ind_line)}</div>
      {details_html}
  </td>
</tr>
""")
    return f"""
<table>
  <thead>
    <tr><th>代码/名称</th><th>最新价/涨跌幅</th><th>成交额</th><th>AI 评分</th><th>策略</th><th>AI 理由 / 指标</th></tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
"""


def _render_prev_table(
    prev_date: str,
    prev_slot: str,
    prev_picks: List[dict],
    spot_now: dict,
) -> str:
    if not prev_picks:
        return '<p class="empty">无历史推送可对比（首次运行或上次未触发推送）。</p>'
    rows = []
    sum_chg = 0.0
    cnt = 0
    pos = 0
    for p in prev_picks:
        code = p["code"]
        cur = spot_now.get(code)
        push_price = float(p.get("price") or 0)
        if cur and push_price > 0:
            cur_price, _, _ = cur
            chg = (cur_price - push_price) / push_price * 100
            sum_chg += chg
            cnt += 1
            if chg > 0:
                pos += 1
            chg_html = _pct_html(chg)
            cur_html = _fmt(cur_price)
        else:
            chg_html = "-"
            cur_html = "暂无行情"
        focus_html = '<span class="tag tag-focus">重点</span>' if p.get("is_focus") else ""
        rows.append(f"""
<tr>
  <td>{_esc(code)}<br>{_esc(p.get('name'))}<br>{_sources_html(p.get('sources'))}{focus_html}</td>
  <td>{_fmt(push_price)}</td>
  <td>{cur_html}</td>
  <td>{chg_html}</td>
  <td>{p.get('ai_score') or 0}</td>
  <td><div class="reason">{_esc(p.get('ai_reason') or '-')}</div></td>
</tr>
""")
    avg = sum_chg / cnt if cnt else 0.0
    win = (pos / cnt * 100) if cnt else 0.0
    summary = (
        f"<p class='stat'><span>对比基准: <b>{prev_date} {SLOT_DISPLAY.get(prev_slot, prev_slot)}</b></span>"
        f"<span>样本: <b>{cnt}</b></span>"
        f"<span>平均涨跌幅: <b>{avg:+.2f}%</b></span>"
        f"<span>胜率: <b>{win:.0f}%</b></span></p>"
    )
    return summary + f"""
<table>
  <thead>
    <tr><th>代码/名称</th><th>推送时价</th><th>当前价</th><th>涨跌幅</th><th>当时AI</th><th>当时理由</th></tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
"""


def render_html(
    run_date: str,
    slot: str,
    pushed: List[StockEvaluation],
    stats: dict,
    prev_date: str,
    prev_slot: str,
    prev_picks: List[dict],
    spot_now: dict,
) -> str:
    title = f"DailyStock 报告 - {run_date} {SLOT_DISPLAY.get(slot, slot)}"
    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pushed_html = _render_pushed_table(pushed)
    prev_html = _render_prev_table(prev_date, prev_slot, prev_picks, spot_now)
    stat_line = (
        f"<span>候选池(成交额∪热榜): <b>{stats.get('total', 0)}</b></span>"
        f"<span>一票否决: <b>{stats.get('vetoed', 0)}</b></span>"
        f"<span>信号≥{stats.get('min_signals', 2)}: <b>{stats.get('candidates', 0)}</b></span>"
        f"<span>AI 调用: <b>{stats.get('ai_called', 0)}</b></span>"
        f"<span>AI 允许: <b>{stats.get('ai_allowed', 0)}</b></span>"
        f"<span>本次推送: <b>{len(pushed)}</b></span>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
  <h1>{_esc(title)}</h1>
  <div class="meta">生成时间 {gen_at}</div>
  <div class="stat">{stat_line}</div>

  <h2>本次推送（AI 评分前 {stats.get('push_top_n', 5)} ∪ 重点票）</h2>
  {pushed_html}

  <h2>上次推送回看</h2>
  {prev_html}

  <footer>
    数据来源：东方财富 / 同花顺；AI 判定：DeepSeek。仅供研究，不构成投资建议。
  </footer>
</body>
</html>
"""


def write_report(
    reports_dir: Path,
    report_host: str,
    run_date: str,
    slot: str,
    pushed: List[StockEvaluation],
    stats: dict,
    prev_date: str,
    prev_slot: str,
    prev_picks: List[dict],
    spot_now: dict,
) -> Tuple[Path, str]:
    local, url = report_paths(reports_dir, report_host, run_date, slot)
    local.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(
        run_date, slot, pushed, stats, prev_date, prev_slot, prev_picks, spot_now
    )
    local.write_text(html_text, encoding="utf-8")
    return local, url
