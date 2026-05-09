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


SYSTEM_PROMPT = """你是一名资深A股短线分析师。基于用户给出的实时数据与技术指标，做出严格判断并以JSON返回：
{"score": <0-100整数>, "allow": <true|false>, "reason": "<不超过80字的中文理由>"}
要求：
- score 综合考虑技术面、量能、趋势与风险，越高越值得关注
- 仅当 score >= 70 且无明显风险/见顶信号时 allow=true
- 严禁输出 JSON 以外的字符，不要使用 markdown 代码块
"""


def _fmt(v: float, digits: int = 2) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "N/A"
    return f"{v:.{digits}f}"


def build_user_prompt(ev: StockEvaluation) -> str:
    s = ev.snapshot
    ind = ev.indicators
    st = ev.strategy
    lines = [
        "【基本信息】",
        f"- 代码: {s.code}",
        f"- 名称: {s.name}",
        f"- 最新价: {_fmt(s.price)}",
        f"- 涨跌幅: {_fmt(s.pct_change)}%",
        f"- 成交额: {s.turnover/1e8:.2f} 亿",
        f"- 成交量: {s.volume:.0f} 股",
        f"- 振幅: {_fmt(s.amplitude)}%",
        "",
        "【技术指标】",
        f"- MA5/MA10/MA20/MA60: {_fmt(ind.ma5)} / {_fmt(ind.ma10)} / {_fmt(ind.ma20)} / {_fmt(ind.ma60)}",
        f"- MACD: DIF={_fmt(ind.macd_dif,3)} DEA={_fmt(ind.macd_dea,3)} HIST={_fmt(ind.macd_hist,3)} (前值 {_fmt(ind.macd_hist_prev,3)})",
        f"- RSI14: {_fmt(ind.rsi14,1)}",
        f"- 近20日 高/低: {_fmt(ind.high20)} / {_fmt(ind.low20)}",
        f"- 5日均量: {ind.avg_vol5:.0f}  量比: {_fmt(ind.volume_ratio)}",
        "",
        "【策略命中】",
        f"- 命中数: {st.hits}",
        f"- 命中信号: {', '.join(st.signals) if st.signals else '无'}",
        f"- 未命中: {', '.join([m.split(']')[0].strip('[') for m in st.misses]) if st.misses else '无'}",
        "",
        "【明细】",
    ]
    for d in st.details[:12]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("请基于以上信息，输出严格 JSON。")
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
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 30, max_retry: int = 3):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retry = max_retry

    def evaluate(self, ev: StockEvaluation) -> AiDecision:
        try:
            content = self._call(build_user_prompt(ev))
        except Exception as e:  # noqa: BLE001
            logger.warning("DeepSeek 调用失败 %s: %s", ev.snapshot.code, e)
            return AiDecision(ok=False, reason=f"AI失败:{type(e).__name__}")
        parsed = _parse(content)
        if parsed is None:
            logger.warning("DeepSeek 解析失败 %s: %s", ev.snapshot.code, content[:200])
            return AiDecision(ok=False, raw=content, reason="解析失败")
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
