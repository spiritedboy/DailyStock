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
    from ..utils import safe_fmt
    return safe_fmt(v, digits=nd, default="-")


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


_CSS = """
:root {
  color-scheme: light dark;
  --bg:#f6f7f9; --card:#ffffff; --fg:#1a1d23; --muted:#6b7280; --line:#e5e7eb;
  --row-alt:#f9fafb; --th:#f3f4f6; --link:#2563eb; --pos:#dc2626; --neg:#16a34a;
  --shadow: 0 1px 2px rgba(0,0,0,.04), 0 2px 8px rgba(0,0,0,.04);
  --tag-tov-bg:#dbeafe; --tag-tov-fg:#1d4ed8;
  --tag-hot-bg:#fee2e2; --tag-hot-fg:#b91c1c;
  --tag-focus-bg:#fef3c7; --tag-focus-fg:#92400e;
  --tag-allow-bg:#dcfce7; --tag-allow-fg:#15803d;
  --tag-deny-bg:#fee2e2; --tag-deny-fg:#b91c1c;
  --score-bg: linear-gradient(90deg,#22c55e,#3b82f6);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0f1115; --card:#1a1d23; --fg:#e5e7eb; --muted:#9ca3af; --line:#2a2f37;
    --row-alt:#1f242c; --th:#222831; --link:#60a5fa; --pos:#f87171; --neg:#4ade80;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 2px 8px rgba(0,0,0,.3);
    --tag-tov-bg:#1e293b; --tag-tov-fg:#93c5fd;
    --tag-hot-bg:#3f1d1d; --tag-hot-fg:#fca5a5;
    --tag-focus-bg:#3a2e10; --tag-focus-fg:#fcd34d;
    --tag-allow-bg:#14361f; --tag-allow-fg:#86efac;
    --tag-deny-bg:#3f1d1d; --tag-deny-fg:#fca5a5;
  }
}
* { box-sizing: border-box; }
html, body { margin:0; padding:0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  background: var(--bg); color: var(--fg);
  line-height: 1.55;
  -webkit-text-size-adjust: 100%;
  font-size: 15px;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 16px; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Header */
.hdr h1 { font-size: 20px; margin: 0 0 4px; line-height: 1.3; }
.hdr .meta { color: var(--muted); font-size: 13px; margin-bottom: 12px; }
.hdr .meta a { margin: 0 4px; }

/* Section */
h2 { font-size: 17px; margin: 24px 0 12px; padding-left: 10px; border-left: 3px solid var(--link); }
h3 { font-size: 15px; margin: 16px 0 8px; }

/* Stat chips */
.stat { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 16px; padding:0; }
.stat span {
  background: var(--card); border:1px solid var(--line); border-radius: 999px;
  padding: 4px 12px; font-size: 12.5px; color: var(--muted);
  box-shadow: var(--shadow);
}
.stat span b { color: var(--fg); margin-left: 4px; font-weight: 600; }

/* Cards */
.cards { display: grid; gap: 12px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px; box-shadow: var(--shadow);
}
.card-head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; margin-bottom: 10px; }
.card-title { font-weight: 700; font-size: 16px; letter-spacing: .3px; }
.card-title small { font-weight: 400; color: var(--muted); margin-left: 6px; font-size: 12px; }
.card-score {
  font-weight: 700; font-size: 18px;
  background: var(--score-bg); -webkit-background-clip: text; background-clip: text; color: transparent;
}
.kv { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:8px 12px; margin: 8px 0; }
.kv .k { color: var(--muted); font-size: 11.5px; }
.kv .v { font-size: 14px; font-weight: 600; margin-top: 2px; }
.reason { font-size: 13px; color: var(--fg); margin-top: 6px; line-height: 1.6; }
.ind { font-size: 12px; color: var(--muted); margin-top: 4px; word-break: break-all; }
.empty { color: var(--muted); font-style: italic; padding: 12px 0; }

/* Tags / pills */
.tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 4px; line-height: 1.6; }
.tag-tov { background: var(--tag-tov-bg); color: var(--tag-tov-fg); }
.tag-hot { background: var(--tag-hot-bg); color: var(--tag-hot-fg); }
.tag-focus { background: var(--tag-focus-bg); color: var(--tag-focus-fg); }
.tag-allow { background: var(--tag-allow-bg); color: var(--tag-allow-fg); }
.tag-deny { background: var(--tag-deny-bg); color: var(--tag-deny-fg); }

.pos { color: var(--pos); font-weight: 700; }
.neg { color: var(--neg); font-weight: 700; }

details { margin-top: 8px; }
summary { cursor: pointer; color: var(--link); font-size: 12.5px; padding: 4px 0; }
details[open] summary { margin-bottom: 6px; }

/* Table (used by prev/breakdown) */
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 12px; border:1px solid var(--line); background: var(--card); box-shadow: var(--shadow); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { background: var(--th); font-weight: 600; color: var(--muted); font-size: 12px; }
tr:last-child td { border-bottom: 0; }
tr:nth-child(even) td { background: var(--row-alt); }
td .reason { white-space: normal; min-width: 220px; }

/* K线 */
.kline-cell { width: 100%; min-width: 260px; height: 110px; }

footer { color: var(--muted); font-size: 12px; margin-top: 32px; padding: 16px 0; border-top: 1px solid var(--line); text-align: center; }

/* Mobile tweaks */
@media (max-width: 640px) {
  body { font-size: 14.5px; }
  .wrap { padding: 12px; }
  .hdr h1 { font-size: 18px; }
  h2 { font-size: 16px; margin: 20px 0 10px; }
  .card { padding: 12px; border-radius: 10px; }
  .card-title { font-size: 15px; }
  .card-score { font-size: 16px; }
  .kv { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .kline-cell { height: 90px; min-width: 220px; }
}
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
    cards = []
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

        def _has(v):
            if v is None:
                return False
            try:
                return not math.isnan(float(v))
            except (TypeError, ValueError):
                return False
        has_ind = any(
            _has(v)
            for v in (ind.ma5, ind.ma10, ind.ma20, ind.bias10, ind.bias20,
                      ind.rsi14, ind.macd_hist, ind.volume_ratio)
        )
        if has_ind:
            ind_line = (
                f"MA5/10/20 {_fmt(ind.ma5)}/{_fmt(ind.ma10)}/{_fmt(ind.ma20)} · "
                f"BIAS10/20 {_fmt(ind.bias10)}%/{_fmt(ind.bias20)}% · "
                f"RSI14 {_fmt(ind.rsi14, 1)} · MACD {_fmt(ind.macd_hist, 3)} · "
                f"量比 {_fmt(ind.volume_ratio)}"
            )
            ind_html = f'<div class="ind">{_esc(ind_line)}</div>'
        else:
            ind_html = '<div class="ind">指标缺失（K 线未拉到）</div>'

        signals_html = _esc(", ".join(st.signals)) if st.signals else "-"
        cards.append(f"""
<div class="card">
  <div class="card-head">
    <div class="card-title">{_esc(s.code)} <small>{_esc(s.name)}</small></div>
    <div class="card-score">{score}<small style="font-size:12px;color:var(--muted);font-weight:400;"> /100</small></div>
  </div>
  <div>{_sources_html(s.sources)}{focus_html}{allow_html}</div>
  <div class="kv">
    <div><div class="k">最新价</div><div class="v">{_fmt(s.price)}</div></div>
    <div><div class="k">涨跌幅</div><div class="v">{_pct_html(s.pct_change)}</div></div>
    <div><div class="k">成交额</div><div class="v">{s.turnover/1e8:.2f} 亿</div></div>
    <div><div class="k">振幅</div><div class="v">{_fmt(s.amplitude)}%</div></div>
    <div><div class="k">命中</div><div class="v">{st.hits} 票</div></div>
    <div><div class="k">信号</div><div class="v" style="font-size:12px;font-weight:500;">{signals_html}</div></div>
  </div>
  <div class="reason">{_esc(reason) or "-"}</div>
  {ind_html}
  {details_html}
</div>
""")
    return f'<div class="cards">{"".join(cards)}</div>'


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
<div class="table-wrap"><table>
  <thead>
    <tr><th>代码/名称</th><th>推送时价</th><th>当前价</th><th>涨跌幅</th><th>当时AI</th><th>当时理由</th></tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table></div>
"""


def _render_overall_stats(overall: dict | None) -> str:
    if not overall or not overall.get("n"):
        return ""
    return (
        f'<h2>历史推送表现（近 {overall.get("days", 30)} 天，T+{overall.get("period", 5)}）</h2>'
        f'<p class="stat">'
        f'<span>样本: <b>{overall.get("n", 0)}</b></span>'
        f'<span>平均收益: <b>{overall.get("avg", 0):+.2f}%</b></span>'
        f'<span>胜率: <b>{overall.get("win", 0):.1f}%</b></span>'
        f'<span>最高: <b>{overall.get("max", 0):+.2f}%</b></span>'
        f'<span>最低: <b>{overall.get("min", 0):+.2f}%</b></span>'
        f'</p>'
    )


def _render_signal_breakdown(rows: list) -> str:
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{_esc(r['signal'])}</td><td>{r['n']}</td>"
        f"<td>{r['avg']:+.2f}%</td><td>{r['win']:.1f}%</td></tr>"
        for r in rows
    )
    return (
        '<h2>策略归因（按信号 5 日表现）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>信号</th><th>样本</th><th>平均收益</th><th>胜率</th></tr></thead>'
        f'<tbody>{body}</tbody></table></div>'
    )


def _render_ai_calibration(rows: list) -> str:
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{_esc(r['bucket'])}</td><td>{r['n']}</td>"
        f"<td>{r['avg']:+.2f}%</td><td>{r['win']:.1f}%</td></tr>"
        for r in rows
    )
    return (
        '<h2>AI 评分校准（5 日表现按分箱）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>分箱</th><th>样本</th><th>平均收益</th><th>胜率</th></tr></thead>'
        f'<tbody>{body}</tbody></table></div>'
    )


def _render_kline_section(pushed: List[StockEvaluation], klines_map: dict) -> str:
    """嵌入 ECharts 蜡烛小图：每只推送票一张。"""
    if not pushed or not klines_map:
        return ""
    items = []
    series_js = []
    for e in pushed:
        s = e.snapshot
        df = klines_map.get(s.code)
        if df is None or df.empty:
            continue
        df2 = df.tail(60)
        try:
            data = []
            dates = []
            for _, row in df2.iterrows():
                dates.append(str(row.get("date", "")))
                data.append([
                    float(row["open"]), float(row["close"]),
                    float(row["low"]), float(row["high"]),
                ])
        except Exception:  # noqa: BLE001
            continue
        chart_id = f"k_{s.code}"
        items.append(
            f'<tr><td>{_esc(s.code)} {_esc(s.name)}</td>'
            f'<td><div id="{chart_id}" class="kline-cell"></div></td></tr>'
        )
        import json as _json
        series_js.append(
            f"renderK('{chart_id}', {_json.dumps(dates)}, {_json.dumps(data)});"
        )
    if not items:
        return ""
    return (
        '<h2>推送票 60 日 K 线</h2>'
        f'<div class="table-wrap"><table><tbody>{"".join(items)}</tbody></table></div>'
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>'
        '<script>'
        'function renderK(id, dates, data){'
        ' var c=echarts.init(document.getElementById(id));'
        ' c.setOption({grid:{left:30,right:8,top:8,bottom:18},'
        '  xAxis:{type:"category",data:dates,axisLabel:{fontSize:9},axisTick:{show:false}},'
        '  yAxis:{scale:true,axisLabel:{fontSize:9}},'
        '  series:[{type:"candlestick",data:data,'
        '    itemStyle:{color:"#d93025",color0:"#188038",borderColor:"#d93025",borderColor0:"#188038"}}]'
        ' });'
        '}'
        + "".join(series_js) +
        '</script>'
    )


def render_html(
    run_date: str,
    slot: str,
    pushed: List[StockEvaluation],
    stats: dict,
    prev_date: str,
    prev_slot: str,
    prev_picks: List[dict],
    spot_now: dict,
    overall: dict | None = None,
    signal_rows: list | None = None,
    ai_rows: list | None = None,
    klines_map: dict | None = None,
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
    overall_html = _render_overall_stats(overall)
    signal_html = _render_signal_breakdown(signal_rows or [])
    ai_html = _render_ai_calibration(ai_rows or [])
    kline_html = _render_kline_section(pushed, klines_map or {})
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hdr">
    <h1>{_esc(title)}</h1>
    <div class="meta">生成时间 {gen_at} · <a href="index.html">月度索引</a> · <a href="../index.html">全部报告</a></div>
  </header>
  <div class="stat">{stat_line}</div>

  <h2>本次推送（AI 评分前 {stats.get('push_top_n', 5)} ∪ 重点票）</h2>
  {pushed_html}

  {kline_html}

  <h2>上次推送回看</h2>
  {prev_html}

  {overall_html}
  {signal_html}
  {ai_html}

  <footer>
    数据来源：东方财富 / 同花顺；AI 判定：DeepSeek。仅供研究，不构成投资建议。
  </footer>
</div>
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
    overall: dict | None = None,
    signal_rows: list | None = None,
    ai_rows: list | None = None,
    klines_map: dict | None = None,
) -> Tuple[Path, str]:
    local, url = report_paths(reports_dir, report_host, run_date, slot)
    local.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(
        run_date, slot, pushed, stats, prev_date, prev_slot, prev_picks, spot_now,
        overall=overall, signal_rows=signal_rows, ai_rows=ai_rows, klines_map=klines_map,
    )
    local.write_text(html_text, encoding="utf-8")
    return local, url


def write_index(reports_dir: Path, runs: list) -> Path:
    """写出 reports/index.html（全部）+ reports/{YYYY-MM}/index.html（每月）。"""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 按月分组
    by_month: dict = {}
    for r in runs:
        d = r["run_date"]
        try:
            ym = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m")
        except ValueError:
            continue
        by_month.setdefault(ym, []).append(r)

    # 月度索引
    for ym, items in by_month.items():
        items.sort(key=lambda r: (r["run_date"], 0 if r["run_slot"] == "midday" else 1), reverse=True)
        rows = []
        for r in items:
            slot = r["run_slot"]
            try:
                dt = datetime.strptime(r["run_date"], "%Y-%m-%d")
            except ValueError:
                continue
            fname = f"{dt.strftime('%m-%d')}-{_slot_to_filename(slot)}.html"
            rows.append(
                f'<tr><td><a href="{fname}">{_esc(r["run_date"])} {SLOT_DISPLAY.get(slot, slot)}</a></td>'
                f'<td>{r.get("total") or 0}</td><td>{r.get("candidates") or 0}</td>'
                f'<td>{r.get("focus_count") or 0}</td><td>{r.get("vetoed") or 0}</td></tr>'
            )
        page = (
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>DailyStock {ym}</title><style>{_CSS}</style></head><body>'
            '<div class="wrap"><header class="hdr">'
            f'<h1>DailyStock 报告 - {ym}</h1>'
            '<div class="meta"><a href="../index.html">← 返回总索引</a></div></header>'
            '<div class="table-wrap"><table><thead><tr><th>报告</th><th>候选池</th><th>候选</th><th>重点</th><th>否决</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></div></body></html>'
        )
        (reports_dir / ym).mkdir(parents=True, exist_ok=True)
        (reports_dir / ym / "index.html").write_text(page, encoding="utf-8")

    # 总索引
    months = sorted(by_month.keys(), reverse=True)
    rows = []
    for ym in months:
        rows.append(
            f'<tr><td><a href="{ym}/index.html">{ym}</a></td>'
            f'<td>{len(by_month[ym])} 份</td></tr>'
        )
    total_page = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>DailyStock 报告</title><style>{_CSS}</style></head><body>'
        '<div class="wrap"><header class="hdr"><h1>DailyStock 报告索引</h1></header>'
        '<div class="table-wrap"><table><thead><tr><th>月份</th><th>报告数</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></div></body></html>'
    )
    out = reports_dir / "index.html"
    out.write_text(total_page, encoding="utf-8")
    return out
