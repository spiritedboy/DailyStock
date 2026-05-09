"""DeepSeek 客户端：输入完整基本信息+技术指标+命中信号，输出 score(0-100) 与 allow。"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import AiDecision, StockEvaluation

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一名资深 A 股短线分析师。基于用户给出的实时数据与技术指标进行打分，并以 JSON 返回：
{"score": <0-100 整数>, "allow": <true|false>, "reason": "<不超过 80 字中文理由>"}

评分准则（必须根据给定数据评分，不得以"数据不足"推诿）：
· 候选基础分 50 分；以下逐项加减：
  + 多头排列(MA5>MA10>MA20)/均线金叉：+10~15
  + MACD 柱由负转正或持续放大：+10
  + RSI14 位于 50~70：+5；位于 30~50 走稳回升：+3
  + 收盘突破 20 日新高：+10
  + 量比≥1.5 放量上行：+8；量比 1.2~1.5：+4
  + 振幅适中(3%~10%)、成交额≥10 亿：+5
  + K 线形态良好(双底/缩量回踩支撑等)：+5
  - 高位滞涨/RSI≥80/BIAS10≥15%/BIAS20≥20%：每项 -10~15
  - 今日跌幅>3%/收带长上影/天量见顶：每项 -8~15
  - 接近 52 周高点 5% 以内且当日滞涨：-10
· 已给出具体数值的指标视为有效；未在输入中出现的指标不计分也不扣分，理由中不要提"指标缺失"。
· allow=true 的条件：score ≥ 65 且 当日涨跌幅 > -2% 且 无明显高位见顶信号。
· reason 必须引用 1~2 个关键数值（如 "RSI70.6 偏高""量比1.21 放量"）。
· 严禁输出 JSON 以外字符，不要 markdown 代码块。
"""


def _fmt(v: float, digits: int = 2) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "N/A"
    return f"{v:.{digits}f}"


def _is_valid(v) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
        return not (math.isnan(f) or math.isinf(f))
    except (TypeError, ValueError):
        return False


def _line(label: str, *vals, sep: str = " / ", digits: int = 2, suffix: str = "") -> Optional[str]:
    """全部值无效则返回 None（该行不输出，避免误导 AI）。"""
    if not any(_is_valid(v) for v in vals):
        return None
    parts = [_fmt(v, digits) for v in vals]
    return f"- {label}: {sep.join(parts)}{suffix}"


def build_user_prompt(ev: StockEvaluation) -> str:
    s = ev.snapshot
    ind = ev.indicators
    st = ev.strategy
    lines = [
        "【基本信息】",
        f"- 代码: {s.code}",
        f"- 名称: {s.name}",
        f"- 来源: {('+'.join(s.sources)) or '-'}",
        f"- 最新价: {_fmt(s.price)}",
        f"- 涨跌幅: {_fmt(s.pct_change)}%",
        f"- 成交额: {s.turnover/1e8:.2f} 亿",
        f"- 振幅: {_fmt(s.amplitude)}%",
        f"- 今日 开/高/低/收: {_fmt(s.open)} / {_fmt(s.high)} / {_fmt(s.low)} / {_fmt(s.price)}",
        "",
        "【技术指标】（以下均为已计算的有效数值，未列出的项本次未提供）",
    ]
    for ln in (
        _line("MA5/MA10/MA20/MA60", ind.ma5, ind.ma10, ind.ma20, ind.ma60),
        _line("BIAS10/BIAS20", ind.bias10, ind.bias20, suffix="%"),
        _line("20日累计涨幅", ind.pct_20d, suffix="%"),
        _line("MACD DIF/DEA/HIST", ind.macd_dif, ind.macd_dea, ind.macd_hist, digits=3),
        _line("MACD HIST 前值", ind.macd_hist_prev, digits=3),
        _line("RSI14", ind.rsi14, digits=1),
        _line("近20日 高/低", ind.high20, ind.low20),
        _line("52周高点", ind.high52w),
        _line("量比", ind.volume_ratio),
    ):
        if ln:
            lines.append(ln)
    lines += [
        "",
        "【策略命中】",
        f"- 命中数: {st.hits}",
        f"- 命中信号: {', '.join(st.signals) if st.signals else '无'}",
    ]
    if st.veto_reasons:
        lines.append(f"- 一票否决警示: {' | '.join(st.veto_reasons)}")
    if st.details:
        lines.append("")
        lines.append("【明细】")
        for d in st.details[:12]:
            lines.append(f"- {d}")
    lines.append("")
    lines.append("请严格依据以上数值评分并输出 JSON。")
    return "\n".join(lines)


def _parse(content: str) -> Optional[AiDecision]:
    if not content:
        return None
    txt = content.strip()
    txt = re.sub(r"^```(?:json)?", "", txt).strip()
    txt = re.sub(r"```$", "", txt).strip()
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    score = int(obj.get("score", 0))
    score = max(0, min(100, score))
    allow = bool(obj.get("allow", False))
    reason = str(obj.get("reason", ""))[:200]
    return AiDecision(score=score, allow=allow, reason=reason, raw=content, ok=True)


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 30, max_retry: int = 3,
                 repo=None, daily_budget: int = 0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retry = max_retry
        self.repo = repo
        self.daily_budget = int(daily_budget or 0)

    def evaluate(self, ev: StockEvaluation, run_date: str = "") -> AiDecision:
        # 1) 缓存命中
        if self.repo and run_date:
            cached = self.repo.get_ai_cached(run_date, ev.snapshot.code)
            if cached:
                return AiDecision(
                    score=int(cached["score"]), allow=bool(cached["allow"]),
                    reason=cached["reason"] or "", raw=cached["raw"] or "", ok=True,
                )
        # 2) 预算上限
        if self.repo and run_date and self.daily_budget > 0:
            used = self.repo.get_ai_usage(run_date)
            if used >= self.daily_budget:
                return AiDecision(ok=False, reason=f"AI预算耗尽({used}/{self.daily_budget})")
        try:
            content = self._call(build_user_prompt(ev))
        except Exception as e:  # noqa: BLE001
            logger.warning("DeepSeek 调用失败 %s: %s", ev.snapshot.code, e)
            return AiDecision(ok=False, reason=f"AI失败:{type(e).__name__}")
        if self.repo and run_date:
            self.repo.incr_ai_usage(run_date, 1)
        parsed = _parse(content)
        if parsed is None:
            logger.warning("DeepSeek 解析失败 %s: %s", ev.snapshot.code, content[:200])
            return AiDecision(ok=False, raw=content, reason="解析失败")
        if self.repo and run_date:
            self.repo.save_ai_cached(
                run_date, ev.snapshot.code,
                parsed.score, parsed.allow, parsed.reason, parsed.raw,
            )
        return parsed

    def _call(self, user: str) -> str:
        @retry(
            stop=stop_after_attempt(self.max_retry),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((requests.RequestException,)),
            reraise=True,
        )
        def _do() -> str:
            url = f"{self.base_url}/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "stream": False,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

        return _do()
