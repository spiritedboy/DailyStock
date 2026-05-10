"""策略管线：并发拉K线 + 计算指标 + 执行策略。"""
from __future__ import annotations

import logging
from typing import List, Optional

from ..config import StrategyConfig
from ..data.cache import KlineCache
from ..data.fetcher import fetch_klines_concurrent
from ..models import StockEvaluation, StockSnapshot
from .rules import evaluate

logger = logging.getLogger(__name__)


def run_pipeline(
    snapshots: List[StockSnapshot],
    cfg: StrategyConfig,
    cache: Optional[KlineCache] = None,
    max_workers: int = 6,
    klines_days: int = 250,
) -> List[StockEvaluation]:
    """对所有 snapshot 并发取 K 线、计算指标、执行策略。"""
    codes = [s.code for s in snapshots]
    klines_map = fetch_klines_concurrent(
        codes, days=klines_days, cache=cache, max_workers=max_workers,
    )

    evals: List[StockEvaluation] = []
    vetoed_n = 0
    for s in snapshots:
        klines = klines_map.get(s.code)
        result, ind = evaluate(s, klines, cfg)
        ev = StockEvaluation(snapshot=s, indicators=ind, strategy=result)
        evals.append(ev)
        if result.vetoed:
            vetoed_n += 1
    candidates = [e for e in evals if e.is_candidate(cfg.min_signals)]
    logger.info(
        "策略完成: 总计=%d 否决=%d 候选(hits>=%d且未否决)=%d",
        len(snapshots), vetoed_n, cfg.min_signals, len(candidates),
    )
    return evals
