"""决策分层：从 AI 结果中筛出重点股票。"""
from __future__ import annotations

from typing import List

from ..models import StockEvaluation


def select_focus(
    evals: List[StockEvaluation], focus_score: int, top_k: int
) -> List[StockEvaluation]:
    """重点 = ai.ok 且 ai.allow 且 score>=focus_score，按 score 降序取前 top_k。"""
    cands = [e for e in evals if e.ai and e.ai.ok and e.ai.allow and e.ai.score >= focus_score]
    cands.sort(key=lambda e: e.ai.score, reverse=True)
    return cands[:top_k] if top_k > 0 else cands
