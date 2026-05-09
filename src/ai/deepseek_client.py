"""DeepSeek 客户端：输入完整基本信息+技术指标+命中信号，输出 score(0-100) 与 allow。"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Optional

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import AiDecision, StockEvaluation

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一名常年活跃在 A 股的一线短线游资操盘手。你深知当前输入的股票已经是全市场“成交额前 100”或“热度前 100”的高人气核心标的。你的任务是基于提供的实时量价数据、指标形态及（如有）概念题材，透视资金合力，给该股票的短线博弈价值进行综合评分，并严格以 JSON 格式返回：
{"score": <0-100 整数>, "allow": <true|false>, "reason": "<不超过 80 字中文核心逻辑>"}

【评分核心逻辑（必须进行综合定性推演，非机械叠加）】：

1. 资金承接与量价健康度（权重 40%）：
   - 优：底部或突破位的温和放量、缩量精准回踩核心均线、分歧后的强势转一致。
   - 劣：高位爆天量滞涨（派发）、放量长上影线（诱多被砸）、量价严重背离。

2. 结构位置与趋势阻力（权重 35%）：
   - 优：上方无明显套牢盘（创阶段新高）、多头排列且现价紧贴 5日/10日均线（进攻形态）。
   - 劣：面临重大技术压力位、均线空头排列下的超跌反抽、乖离率极大（现价偏离 10/20 日均线超 15% 以上的加速赶顶）。

3. 盈亏比与防守空间（权重 25%）：
   - 优：止损位极度清晰（如紧挨某条大级别支撑线），向上的预期利润空间远大于向下破位的风险。
   - 劣：现价处于不上不下的“悬空状态”，止损代价极大。


【输出规则与红线】：

· allow=true 的铁律：score 必须 ≥ 75 分，且当日绝不能出现“高位放量滞涨”、“严重乖离”或“破位长阴”等致命见顶/破位信号。
· reason 必须像职业交易员复盘一样，一针见血指出核心看多或否决的逻辑，并至少引用 1~2 个关键数据点（如：“高位爆出 2.5 倍天量但收长上影，资金明显派发”、“紧贴 10 日线缩量企稳，盈亏比极佳”）。
· 无论数据多寡，必须给出确定性结论。
· 严禁输出任何 markdown 格式（包括 ```json 标识），严禁在 JSON 前后附加任何多余字符，直接输出原始 JSON 字符串。
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
                 repo=None, daily_budget: int = 0, use_cache: bool = True):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retry = max_retry
        self.repo = repo
        self.daily_budget = int(daily_budget or 0)
        self.use_cache = use_cache
        # 提示词版本号：SYSTEM_PROMPT 变化即缓存自动失效
        self.prompt_ver = hashlib.md5(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:10]

    def evaluate(self, ev: StockEvaluation, run_date: str = "") -> AiDecision:
        # 1) 缓存命中（按提示词版本隔离）
        if self.use_cache and self.repo and run_date:
            cached = self.repo.get_ai_cached(run_date, ev.snapshot.code, self.prompt_ver)
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
                self.prompt_ver,
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
