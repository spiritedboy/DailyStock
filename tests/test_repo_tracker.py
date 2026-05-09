"""Repository + tracker 单元测试（使用临时 SQLite）。"""
from pathlib import Path

import pandas as pd

from src.models import (
    AiDecision, IndicatorBundle, StockEvaluation, StockSnapshot, StrategyResult,
)
from src.storage.repository import Repository
from src.tracking.tracker import update_pick_returns


def _make_repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "test.db", tmp_path / "csv")


def _ev(code="600000", price=10.0, score=80, allow=True) -> StockEvaluation:
    return StockEvaluation(
        snapshot=StockSnapshot(code=code, name=code, price=price, sources=["turnover"]),
        indicators=IndicatorBundle(),
        strategy=StrategyResult(hits=2, signals=["MA多头", "MACD金叉"]),
        ai=AiDecision(score=score, allow=allow, reason="ok", ok=True),
        is_pushed=True,
    )


def test_ai_cache_roundtrip(tmp_path: Path):
    repo = _make_repo(tmp_path)
    assert repo.get_ai_cached("2025-05-09", "600000") is None
    repo.save_ai_cached("2025-05-09", "600000", 85, True, "good", "{}")
    got = repo.get_ai_cached("2025-05-09", "600000")
    assert got is not None and got["score"] == 85 and got["allow"] is True


def test_ai_usage_counter(tmp_path: Path):
    repo = _make_repo(tmp_path)
    assert repo.get_ai_usage("2025-05-09") == 0
    assert repo.incr_ai_usage("2025-05-09", 1) == 1
    assert repo.incr_ai_usage("2025-05-09", 3) == 4
    assert repo.get_ai_usage("2025-05-09") == 4


def test_pick_returns_and_signal_breakdown(tmp_path: Path):
    repo = _make_repo(tmp_path)
    rid = repo.new_run_id()
    ev = _ev("600000", price=10.0)
    repo.save_picks(rid, "2025-05-06", "close", [ev])

    # 模拟 fetch_klines 返回 80 天数据：5 日后 +5%
    def _fetch(code, days=80):
        rows = []
        # 起始日期不重要，但需包含 2025-05-06 及后续
        dates = pd.date_range("2025-05-01", periods=20).strftime("%Y-%m-%d").tolist()
        prices = [10.0] * 5 + [10.5] * 15  # T+5 +5%
        for d, p in zip(dates, prices):
            rows.append({"date": d, "open": p, "close": p, "high": p, "low": p,
                         "volume": 1, "turnover": 1, "amplitude": 0, "pct_change": 0})
        return pd.DataFrame(rows)

    n = update_pick_returns(repo, _fetch)
    assert n >= 1
    sig = repo.signal_performance(days=365, period=5)
    sigs = {r["signal"] for r in sig}
    assert "MA多头" in sigs and "MACD金叉" in sigs
    rets = repo.list_returns(days=365, period=5)
    assert any(abs(r["ret_pct"] - 5.0) < 0.01 for r in rets)


def test_list_reports(tmp_path: Path):
    repo = _make_repo(tmp_path)
    rid = repo.new_run_id()
    repo.save_run(rid, "2025-05-08", "close", total=100, vetoed=10,
                  candidates=5, ai_called=3, ai_allowed=2, focus_count=1)
    rows = repo.list_reports(limit=10)
    assert rows and rows[0]["run_date"] == "2025-05-08"
