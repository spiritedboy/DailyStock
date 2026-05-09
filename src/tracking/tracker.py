"""推送票多周期跟踪 + 策略归因 + AI 评分校准。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

PERIODS = [1, 3, 5, 10, 20]  # 跟踪 T+N 的相对收益


def update_pick_returns(repo, fetch_klines) -> int:
    """对所有已推送票，按 T+N 计算累计涨跌幅写入 pick_returns 表。

    fetch_klines: callable(code, days=80) -> DataFrame(date, close, ...)
    返回更新条数。
    """
    pending = repo.fetch_pending_returns(max_periods=max(PERIODS))
    if not pending:
        return 0
    by_code: Dict[str, List[dict]] = {}
    for row in pending:
        by_code.setdefault(row["code"], []).append(row)

    updated = 0
    for code, rows in by_code.items():
        df = fetch_klines(code, 80)
        if df is None or df.empty or "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = df["date"].astype(str)
        date_to_close: Dict[str, float] = dict(zip(df["date"], df["close"].astype(float)))
        sorted_dates = list(df["date"])
        for r in rows:
            push_date = r["run_date"]
            push_price = float(r["price"]) if r["price"] else 0.0
            if push_price <= 0:
                continue
            if push_date not in date_to_close:
                # 用第一个 >= push_date 的交易日作为 T0（避免假期错配）
                fut = [d for d in sorted_dates if d >= push_date]
                if not fut:
                    continue
                push_date = fut[0]
                push_price = date_to_close[push_date]
            try:
                idx = sorted_dates.index(push_date)
            except ValueError:
                continue
            for n in PERIODS:
                target_idx = idx + n
                if target_idx >= len(sorted_dates):
                    continue
                d = sorted_dates[target_idx]
                close = date_to_close[d]
                if push_price <= 0:
                    continue
                ret = (close - push_price) / push_price * 100
                repo.upsert_pick_return(
                    run_date=r["run_date"],
                    run_slot=r["run_slot"],
                    code=code,
                    period=n,
                    target_date=d,
                    push_price=push_price,
                    target_price=float(close),
                    ret_pct=float(ret),
                )
                updated += 1
    return updated


def signal_breakdown(repo, days: int = 60) -> List[dict]:
    """各信号策略的命中后 N 日表现。"""
    return repo.signal_performance(days=days, period=5)


def ai_calibration(repo, days: int = 60) -> List[dict]:
    """AI 评分分箱后的 5 日平均收益与胜率。"""
    return repo.ai_score_calibration(days=days, period=5)


def render_overall_stats(repo, days: int = 30, period: int = 5) -> dict:
    rows = repo.list_returns(days=days, period=period)
    if not rows:
        return {"days": days, "period": period, "n": 0, "avg": 0.0, "win": 0.0}
    s = pd.Series([r["ret_pct"] for r in rows])
    return {
        "days": days,
        "period": period,
        "n": len(s),
        "avg": float(s.mean()),
        "win": float((s > 0).mean() * 100),
        "max": float(s.max()),
        "min": float(s.min()),
    }
