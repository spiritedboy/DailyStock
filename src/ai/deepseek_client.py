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


SYSTEM_PROMPT = """你是一名经验丰富的 A 股短线交易员兼盘面分析师，擅长从量价行为、趋势结构、市场情绪、资金博弈角度解读个股。
用户会给你一只股票的当日盘面快照、技术指标和策略命中情况。请像在交易席位上对同事讲解一样，做出有判断力、有取舍的整体评估，
而不是机械套公式。最终以严格 JSON 输出：
{"score": <0-100 整数>, "allow": <true|false>, "reason": "<不超过 80 字的中文判断>"}

分析视角（按需融合，不必逐项罗列）：
1. 趋势位置：当前价位于均线哪个区域？是初步启动、加速上涨、还是高位钝化？均线发散/纠结？
2. 量价关系：是放量突破、缩量回踩、滞涨上影、还是高位天量？资金是否在出货？
3. 动能与拐点：MACD 红/绿柱状态、RSI 是否进入超买/钝化区间、20 日累计涨幅是否过度透支？
4. 风险面：是否临近 52 周高点、BIAS 是否过大、当日是否破位、是否带显著上影线？
5. 整体性价比：在现在的位置追入，潜在空间 vs 风险幅度是否合算？短线资金愿意接力吗？

打分指引（参考，非死板公式）：
· 80-95：技术形态、量能、趋势、安全边际四方面整体优异，是值得重点关注的票
· 65-79：有明显买点或题材催化但存在 1-2 项瑕疵，可考虑跟进
· 50-64：方向不明 / 优劣并存，倾向观望
· <50：明显见顶、放量滞涨、技术破位或风险过大

allow=true 的判断原则：你作为交易员，今天此价位是否愿意建仓或加仓？需同时满足：
  · 综合得分 ≥ 65；当日跌幅没有超过 -2%；没有明显的高位见顶/破位/天量出货特征。

reason 写作要求：
  · 像盘后复盘那样讲一句结论，引用 1~2 个最关键的数值或形态作为依据（例："量比 1.8 配 BIAS20 仅 5%，趋势启动初段，可跟"）
  · 禁止使用"指标缺失""数据不足""无法判断"等推诿表述；输入中没出现的指标就当本次不需要它

约束：
  · 仅输出 JSON，禁止 markdown 代码块或额外解释
  · 已给出的指标都是已计算的有效数值，请直接采用
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
                "temperature": 0.5,
                "stream": False,
            }
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

        return _do()
