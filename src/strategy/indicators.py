"""技术指标计算。"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def ma(series: pd.Series, n: int) -> float:
    if series is None or len(series) < n:
        return float("nan")
    return float(series.tail(n).mean())


def ma_series(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n, min_periods=n).mean()


def avg_volume(df: pd.DataFrame, n: int = 5) -> float:
    if df is None or df.empty or "volume" not in df.columns or len(df) < n + 1:
        return float("nan")
    return float(df["volume"].iloc[-(n + 1):-1].mean())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """返回 (DIF, DEA, HIST)。"""
    if close is None or len(close) < slow + signal:
        empty = pd.Series(dtype=float)
        return empty, empty, empty
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    if close is None or len(close) < period + 1:
        return pd.Series(dtype=float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def recent_high_low(close: pd.Series, n: int = 20) -> Tuple[float, float]:
    if close is None or len(close) < n + 1:
        return float("nan"), float("nan")
    window = close.iloc[-(n + 1):-1]
    return float(window.max()), float(window.min())


def detect_double_bottom(low: pd.Series, close: pd.Series, lookback: int = 30, tol: float = 0.03) -> bool:
    """简化双底：近 lookback 日内有两个相近低点（差异<=tol），间隔>=3日，当前价高于两低点 2%。"""
    if low is None or close is None or len(low) < lookback:
        return False
    seg = low.tail(lookback).reset_index(drop=True)
    sorted_idx = seg.nsmallest(2).index.tolist()
    if len(sorted_idx) < 2:
        return False
    i1, i2 = sorted(sorted_idx)
    if i2 - i1 < 3:
        return False
    v1, v2 = float(seg.iloc[i1]), float(seg.iloc[i2])
    if v1 <= 0:
        return False
    if abs(v1 - v2) / v1 > tol:
        return False
    return float(close.iloc[-1]) > max(v1, v2) * 1.02


def detect_head_shoulders_top(high: pd.Series, lookback: int = 30) -> bool:
    """简化头肩顶：lookback 日中部最高，左右肩低于头部且彼此接近。"""
    if high is None or len(high) < lookback:
        return False
    seg = high.tail(lookback).reset_index(drop=True)
    head_idx = int(seg.idxmax())
    if head_idx < 5 or head_idx > lookback - 6:
        return False
    left = seg.iloc[:head_idx]
    right = seg.iloc[head_idx + 1:]
    if left.empty or right.empty:
        return False
    ls, rs = float(left.max()), float(right.max())
    head = float(seg.iloc[head_idx])
    if head <= 0 or ls >= head * 0.98 or rs >= head * 0.98:
        return False
    return abs(ls - rs) / max(ls, rs) <= 0.05
