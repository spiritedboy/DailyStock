"""策略管线：对每只股票计算指标并执行信号制策略。"""
from __future__ import annotations

import logging
import time
from typing import List

from ..config import StrategyConfig
from ..data.fetcher import fetch_recent_klines
from ..models import StockEvaluation, StockSnapshot
from .rules import evaluate

logger = logging.getLogger(__name__)


def run_pipeline(
    snapshots: List[StockSnapshot],
    cfg: StrategyConfig,
    min_signals: int = 2,
    fetch_kline_throttle: float = 0.15,
) -> List[StockEvaluation]:
    """对每只股票计算指标并跑信号策略。返回完整 evaluations（含未通过的）。

    候选池由调用方通过 ev.is_candidate 过滤（risk通过且 hits>=min_signals）。
    """
    evals: List[StockEvaluation] = []
    for idx, s in enumerate(snapshots, 1):
        klines = fetch_recent_klines(s.code, days=80)
        if fetch_kline_throttle > 0:
            time.sleep(fetch_kline_throttle)
        result, ind = evaluate(s, klines, cfg)
        # 兼容外部判定：临时 hack，让 is_candidate 用 min_signals
        result.hits  # noqa: B018
        ev = StockEvaluation(snapshot=s, indicators=ind, strategy=result)
        evals.append(ev)
        if idx % 20 == 0:
            logger.info("策略进度 %d/%d", idx, len(snapshots))
    candidates = [e for e in evals if e.strategy.risk_passed and e.strategy.hits >= min_signals]
    logger.info(
        "策略完成: 总计=%d 候选(hits>=%d)=%d", len(snapshots), min_signals, len(candidates)
    )
    return evals
