"""推送名单后过滤：涨停/一字板剔除、行业去集中。"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from ..models import StockEvaluation

logger = logging.getLogger(__name__)


def _limit_pct(code: str) -> float:
    """返回涨停涨幅 %。

    - 创业板 30、科创板 68 ±20%
    - 北交所 4 / 8 / 920 ±30%
    - 上证 B 股 9 (900xxx) / 深证 B 股 200 ±10%
    - 主板 60 / 00 ±10%
    """
    if code.startswith("30"):  # 创业板
        return 20.0
    if code.startswith("68"):  # 科创板
        return 20.0
    if code.startswith(("4", "8", "920")):  # 北交所
        return 30.0
    # 其他（沪A 60、深A 00、沪B 9、深B 200）默认 10%
    return 10.0


def is_daily_limit_up(snapshot, eps: float = 0.3) -> bool:
    """涨停判定：涨跌幅 >= 涨停阈值 - eps。"""
    limit = _limit_pct(snapshot.code)
    return snapshot.pct_change >= limit - eps


def is_yiziban(snapshot, eps_pct: float = 0.3) -> bool:
    """一字板判定：涨停 + 开=高=低=收（容差 eps_pct % of price）。"""
    if not is_daily_limit_up(snapshot):
        return False
    p = snapshot.price
    if p <= 0:
        return False
    span = max(snapshot.high, p) - min(snapshot.low if snapshot.low > 0 else p, p)
    return abs(snapshot.open - p) / p * 100 < eps_pct and span / p * 100 < eps_pct


def filter_unbuyable(evals: List[StockEvaluation]) -> Tuple[List[StockEvaluation], List[StockEvaluation]]:
    """剔除涨停票（含一字板）。返回 (kept, removed)。"""
    kept: List[StockEvaluation] = []
    removed: List[StockEvaluation] = []
    for e in evals:
        # 涨停票当日通常难买到，统一剔除；一字板是其子集。
        if is_daily_limit_up(e.snapshot):
            removed.append(e)
        else:
            kept.append(e)
    return kept, removed


def diversify_by_industry(
    evals: List[StockEvaluation],
    industry_map: Dict[str, str],
    max_per_industry: int = 2,
) -> Tuple[List[StockEvaluation], List[StockEvaluation]]:
    """同一行业最多 max_per_industry 只。输入按优先级降序。"""
    if max_per_industry <= 0:
        return evals, []
    counts: Dict[str, int] = defaultdict(int)
    kept: List[StockEvaluation] = []
    removed: List[StockEvaluation] = []
    for e in evals:
        ind = industry_map.get(e.snapshot.code, "")
        if not ind:
            kept.append(e)
            continue
        if counts[ind] >= max_per_industry:
            removed.append(e)
            continue
        counts[ind] += 1
        kept.append(e)
    return kept, removed
