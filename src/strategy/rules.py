"""信号制策略集合。

硬过滤：
- risk: 名称含 ST/*ST/退、价格<=0 直接淘汰

信号策略（每命中算 1 票，hits>=MIN_SIGNALS 进候选池）：
1. ma_cross    均线金叉/多头排列：MA5 上穿 MA10 (或多头 MA5>MA10>MA20)
2. macd        MACD 金叉或柱状由负转正
3. rsi         RSI 健康区 30-70（趋势型）或 <30 超卖反弹（含当日上涨）
4. breakout    收盘突破近 20 日高点
5. pattern     双底形态或多头排列+创新高（非头肩顶）
6. volume      当日成交量 >= 近5日均量 × ratio
7. pct_range   涨跌幅在 [pct_min, pct_max]
8. liquidity   成交额 >= floor 且振幅 <= cap
"""
from __future__ import annotations

import logging
from typing import Tuple

import pandas as pd

from ..config import StrategyConfig
from ..models import IndicatorBundle, StockSnapshot, StrategyResult
from .indicators import (
    avg_volume,
    detect_double_bottom,
    detect_head_shoulders_top,
    ma,
    macd,
    recent_high_low,
    rsi,
)

logger = logging.getLogger(__name__)


def compute_indicators(snapshot: StockSnapshot, klines: pd.DataFrame) -> IndicatorBundle:
    ind = IndicatorBundle()
    if klines is None or klines.empty or "close" not in klines.columns:
        return ind
    close = klines["close"]
    ind.ma5 = ma(close, 5)
    ind.ma10 = ma(close, 10)
    ind.ma20 = ma(close, 20)
    ind.ma60 = ma(close, 60) if len(close) >= 60 else float("nan")

    dif, dea, hist = macd(close)
    if len(dif) >= 2:
        ind.macd_dif = float(dif.iloc[-1])
        ind.macd_dea = float(dea.iloc[-1])
        ind.macd_hist = float(hist.iloc[-1])
        ind.macd_hist_prev = float(hist.iloc[-2])

    rsi_s = rsi(close, 14)
    if len(rsi_s) > 0 and not pd.isna(rsi_s.iloc[-1]):
        ind.rsi14 = float(rsi_s.iloc[-1])

    h20, l20 = recent_high_low(close, 20)
    ind.high20, ind.low20 = h20, l20

    avg5 = avg_volume(klines, 5)
    ind.avg_vol5 = avg5
    cur_vol = snapshot.volume or float(klines["volume"].iloc[-1]) if "volume" in klines.columns else 0
    if avg5 and avg5 == avg5 and avg5 > 0 and cur_vol:
        ind.volume_ratio = cur_vol / avg5
    return ind


# ----- 硬过滤 -----
def risk_check(snapshot: StockSnapshot) -> Tuple[bool, str]:
    name = snapshot.name or ""
    for kw in ("ST", "*ST", "退"):
        if kw in name:
            return False, f"名称含{kw}"
    if snapshot.price <= 0:
        return False, "价格异常(可能停牌)"
    return True, "OK"


# ----- 信号策略 -----
def sig_ma_cross(klines: pd.DataFrame, ind: IndicatorBundle) -> Tuple[bool, str]:
    if klines is None or klines.empty or len(klines) < 21:
        return False, "K线不足"
    close = klines["close"]
    ma5_now, ma10_now, ma20_now = ind.ma5, ind.ma10, ind.ma20
    ma5_prev = ma(close.iloc[:-1], 5)
    ma10_prev = ma(close.iloc[:-1], 10)
    if any(pd.isna(x) for x in (ma5_now, ma10_now, ma20_now, ma5_prev, ma10_prev)):
        return False, "均线缺失"
    golden = (ma5_prev <= ma10_prev) and (ma5_now > ma10_now)
    bull_align = ma5_now > ma10_now > ma20_now and float(close.iloc[-1]) > ma20_now
    if golden:
        return True, f"MA5金叉MA10 ({ma5_now:.2f}>{ma10_now:.2f})"
    if bull_align:
        return True, f"多头排列 MA5>{ma10_now:.2f}>{ma20_now:.2f}"
    return False, f"无金叉且非多头 MA5={ma5_now:.2f}/MA10={ma10_now:.2f}/MA20={ma20_now:.2f}"


def sig_macd(ind: IndicatorBundle) -> Tuple[bool, str]:
    if any(pd.isna(x) for x in (ind.macd_dif, ind.macd_dea, ind.macd_hist, ind.macd_hist_prev)):
        return False, "MACD缺失"
    cross_up = ind.macd_hist_prev <= 0 < ind.macd_hist
    above = ind.macd_dif > ind.macd_dea and ind.macd_hist > 0 and ind.macd_hist >= ind.macd_hist_prev
    if cross_up:
        return True, f"MACD柱由负转正 hist={ind.macd_hist:.3f}"
    if above:
        return True, f"MACD多头持续 DIF>{ind.macd_dea:.3f}"
    return False, f"MACD未确认 hist={ind.macd_hist:.3f}"


def sig_rsi(ind: IndicatorBundle, snapshot: StockSnapshot) -> Tuple[bool, str]:
    if pd.isna(ind.rsi14):
        return False, "RSI缺失"
    r = ind.rsi14
    if 40 <= r <= 70:
        return True, f"RSI健康区 {r:.1f}"
    if r < 30 and snapshot.pct_change > 0:
        return True, f"RSI超卖反弹 {r:.1f} 当日+{snapshot.pct_change:.2f}%"
    return False, f"RSI不利 {r:.1f}"


def sig_breakout(klines: pd.DataFrame, ind: IndicatorBundle) -> Tuple[bool, str]:
    if klines is None or klines.empty or pd.isna(ind.high20):
        return False, "突破无数据"
    cur = float(klines["close"].iloc[-1])
    if cur > ind.high20:
        return True, f"突破20日高点 {cur:.2f}>{ind.high20:.2f}"
    return False, f"未突破 {cur:.2f}<= {ind.high20:.2f}"


def sig_pattern(klines: pd.DataFrame) -> Tuple[bool, str]:
    if klines is None or klines.empty:
        return False, "无K线"
    low = klines.get("low")
    high = klines.get("high")
    close = klines.get("close")
    if low is None or high is None or close is None:
        return False, "K线列缺失"
    if detect_head_shoulders_top(high, lookback=30):
        return False, "出现头肩顶(看跌)"
    if detect_double_bottom(low, close, lookback=30):
        return True, "双底反转形态"
    return False, "无明显多头形态"


def sig_volume(snapshot: StockSnapshot, ind: IndicatorBundle, ratio_min: float) -> Tuple[bool, str]:
    if pd.isna(ind.volume_ratio):
        return False, "量比缺失"
    if ind.volume_ratio >= ratio_min:
        return True, f"放量 {ind.volume_ratio:.2f}x"
    return False, f"未放量 {ind.volume_ratio:.2f}x<{ratio_min}"


def sig_pct_range(snapshot: StockSnapshot, pct_min: float, pct_max: float) -> Tuple[bool, str]:
    p = snapshot.pct_change
    if pct_min <= p <= pct_max:
        return True, f"涨跌幅合理 {p:.2f}%"
    return False, f"涨跌幅越界 {p:.2f}%∉[{pct_min},{pct_max}]"


def sig_liquidity(snapshot: StockSnapshot, turnover_floor: float, amplitude_cap: float) -> Tuple[bool, str]:
    if snapshot.turnover < turnover_floor:
        return False, f"成交额不足 {snapshot.turnover/1e8:.2f}亿"
    if snapshot.amplitude > amplitude_cap:
        return False, f"振幅过大 {snapshot.amplitude:.2f}%"
    return True, f"流动性OK {snapshot.turnover/1e8:.2f}亿/振幅{snapshot.amplitude:.2f}%"


def evaluate(
    snapshot: StockSnapshot, klines: pd.DataFrame, cfg: StrategyConfig
) -> Tuple[StrategyResult, IndicatorBundle]:
    result = StrategyResult()
    ind = compute_indicators(snapshot, klines)

    # 硬过滤
    if cfg.risk_enabled:
        ok, msg = risk_check(snapshot)
        result.risk_passed = ok
        result.risk_reason = msg
        if not ok:
            result.misses.append(f"[RISK] {msg}")
            return result, ind

    # 信号策略
    signal_runners = [
        ("MA_CROSS", cfg.ma_enabled, lambda: sig_ma_cross(klines, ind)),
        ("MACD", cfg.macd_enabled, lambda: sig_macd(ind)),
        ("RSI", cfg.rsi_enabled, lambda: sig_rsi(ind, snapshot)),
        ("BREAKOUT", cfg.breakout_enabled, lambda: sig_breakout(klines, ind)),
        ("PATTERN", cfg.pattern_enabled, lambda: sig_pattern(klines)),
        ("VOLUME", cfg.volume_enabled, lambda: sig_volume(snapshot, ind, cfg.volume_ratio_min)),
        ("PCT_RANGE", cfg.pct_enabled, lambda: sig_pct_range(snapshot, cfg.pct_min, cfg.pct_max)),
        ("LIQUIDITY", cfg.liquidity_enabled, lambda: sig_liquidity(snapshot, cfg.turnover_floor, cfg.amplitude_cap)),
    ]
    for name, enabled, fn in signal_runners:
        if not enabled:
            continue
        try:
            hit, msg = fn()
        except Exception as e:  # noqa: BLE001
            hit, msg = False, f"异常:{e}"
        line = f"[{name}] {msg}"
        result.details.append(line)
        if hit:
            result.hits += 1
            result.signals.append(name)
        else:
            result.misses.append(line)
    return result, ind
