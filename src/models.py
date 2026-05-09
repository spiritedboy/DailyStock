"""通用数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class StockSnapshot:
    code: str
    name: str
    price: float = 0.0
    pct_change: float = 0.0
    volume: float = 0.0
    turnover: float = 0.0
    amplitude: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    pre_close: float = 0.0
    sources: List[str] = field(default_factory=list)  # 来源: turnover / ths_hot
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IndicatorBundle:
    ma5: float = float("nan")
    ma10: float = float("nan")
    ma20: float = float("nan")
    ma60: float = float("nan")
    macd_dif: float = float("nan")
    macd_dea: float = float("nan")
    macd_hist: float = float("nan")
    macd_hist_prev: float = float("nan")
    rsi14: float = float("nan")
    high20: float = float("nan")
    low20: float = float("nan")
    high52w: float = float("nan")
    pct_20d: float = float("nan")  # 20日累计涨幅 %
    avg_vol5: float = float("nan")
    volume_ratio: float = float("nan")
    bias10: float = float("nan")  # (close-MA10)/MA10 * 100
    bias20: float = float("nan")

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class StrategyResult:
    """硬过滤 + 一票否决 + 信号制策略评估结果。"""
    risk_passed: bool = True
    risk_reason: str = ""
    vetoed: bool = False
    veto_reasons: List[str] = field(default_factory=list)
    hits: int = 0
    signals: List[str] = field(default_factory=list)
    misses: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


@dataclass
class AiDecision:
    score: int = 0
    allow: bool = False
    reason: str = ""
    raw: str = ""
    ok: bool = True


@dataclass
class StockEvaluation:
    snapshot: StockSnapshot
    indicators: IndicatorBundle = field(default_factory=IndicatorBundle)
    strategy: StrategyResult = field(default_factory=StrategyResult)
    ai: Optional[AiDecision] = None
    is_focus: bool = False
    is_pushed: bool = False  # 本次是否被纳入推送列表

    @property
    def is_candidate(self) -> bool:
        st = self.strategy
        return st.risk_passed and not st.vetoed and st.hits >= 2
