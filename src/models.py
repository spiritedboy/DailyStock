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
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IndicatorBundle:
    """一只股票计算后的核心指标，用于策略与AI输入。"""
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
    avg_vol5: float = float("nan")
    volume_ratio: float = float("nan")  # 当日量 / 5日均量

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class StrategyResult:
    """信号制策略评估：每条信号策略命中算 1 票。"""
    risk_passed: bool = True  # 硬过滤是否通过
    risk_reason: str = ""
    hits: int = 0
    signals: List[str] = field(default_factory=list)  # 命中的信号策略名
    misses: List[str] = field(default_factory=list)  # 未命中信号
    details: List[str] = field(default_factory=list)  # 每条信号的描述


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

    @property
    def is_candidate(self) -> bool:
        return self.strategy.risk_passed and self.strategy.hits >= 2

    @property
    def is_focus(self) -> bool:
        return bool(self.ai and self.ai.ok and self.ai.allow)
