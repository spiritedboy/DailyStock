"""策略管线：对每只股票计算指标并执行策略。"""
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
    fetch_kline_throttle: float = 0.15,
) -> List[StockEvaluation]:
    """对所有 snapshot 取 K 线、计算指标、执行策略。"""
    evals: List[StockEvaluation] = []
    total = len(snapshots)
    vetoed_n = 0
    for idx, s in enumerate(snapshots, 1):
        klines = fetch_recent_klines(s.code, days=250)
        if fetch_kline_throttle > 0:
            time.sleep(fetch_kline_throttle)
        result, ind = evaluate(s, klines, cfg)
        ev = StockEvaluation(snapshot=s, indicators=ind, strategy=result)
        evals.append(ev)
        if result.vetoed:
            vetoed_n += 1
        if idx % 20 == 0:
            logger.info("策略进度 %d/%d (vetoed=%d)", idx, total, vetoed_n)
    candidates = [e for e in evals if e.is_candidate]
    logger.info(
        "策略完成: 总计=%d 否决=%d 候选(hits>=%d且未否决)=%d",
        total, vetoed_n, cfg.min_signals, len(candidates),
    )
    return evals
