"""信号制策略 + 一票否决策略。

硬过滤(risk)：名称含 ST/*ST/退、价格<=0 → 直接淘汰
一票否决(veto)：命中任意一条直接出局，不再计信号
信号策略：每命中算 1 票，hits>=MIN_SIGNALS 进候选池
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

    # 52 周高点（约 250 个交易日）
    if "high" in klines.columns and len(klines) >= 60:
        win = min(252, len(klines))
        ind.high52w = float(klines["high"].tail(win).max())
    elif len(close) >= 60:
        win = min(252, len(close))
        ind.high52w = float(close.tail(win).max())

    # 20 日累计涨幅
    if len(close) >= 21:
        c0 = float(close.iloc[-21])
        c1 = float(close.iloc[-1])
        if c0 > 0:
            ind.pct_20d = (c1 / c0 - 1) * 100

    # 量
    avg5 = avg_volume(klines, 5)
    ind.avg_vol5 = avg5
    cur_vol = snapshot.volume or (float(klines["volume"].iloc[-1]) if "volume" in klines.columns else 0)
    if avg5 and avg5 == avg5 and avg5 > 0 and cur_vol:
        ind.volume_ratio = cur_vol / avg5

    # BIAS
    cur_close = snapshot.price or float(close.iloc[-1])
    if ind.ma10 and ind.ma10 == ind.ma10 and ind.ma10 > 0:
        ind.bias10 = (cur_close - ind.ma10) / ind.ma10 * 100
    if ind.ma20 and ind.ma20 == ind.ma20 and ind.ma20 > 0:
        ind.bias20 = (cur_close - ind.ma20) / ind.ma20 * 100
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


# ----- 一票否决：高位滞涨 -----
def veto_high_stagnation(
    snapshot: StockSnapshot,
    klines: pd.DataFrame,
    ind: IndicatorBundle,
    cfg: StrategyConfig,
) -> Tuple[bool, str]:
    """同时满足：高位 + 放量 + 滞涨 → 否决。返回 (vetoed, reason)。"""
    if klines is None or klines.empty or "close" not in klines.columns:
        return False, ""

    # 高位：20日累计涨幅 > 阈值，或当前价距 52 周高点 < 10%
    high_20 = ind.pct_20d == ind.pct_20d and ind.pct_20d > cfg.veto_stagnation_pct_20d
    near_52w = (
        ind.high52w == ind.high52w
        and ind.high52w > 0
        and snapshot.price > 0
        and (snapshot.price >= ind.high52w * (1 - cfg.veto_stagnation_near_52w))
    )
    is_high = bool(high_20 or near_52w)
    if not is_high:
        return False, ""

    # 放量：当日量 > 5日均量 × 阈值
    is_huge_vol = (
        ind.volume_ratio == ind.volume_ratio
        and ind.volume_ratio >= cfg.veto_stagnation_vol_ratio
    )
    if not is_huge_vol:
        return False, ""

    # 滞涨：今日涨幅 < 阈值，或长上影 (high-close)/close > 阈值
    pct_low = snapshot.pct_change < cfg.veto_stagnation_pct_today
    upper_shadow_ratio = 0.0
    if snapshot.price > 0 and snapshot.high > 0 and snapshot.high >= snapshot.price:
        upper_shadow_ratio = (snapshot.high - snapshot.price) / snapshot.price * 100
    long_upper = upper_shadow_ratio > cfg.veto_stagnation_upper_shadow
    is_stagnant = bool(pct_low or long_upper)
    if not is_stagnant:
        return False, ""

    high_desc = (
        f"20日+{ind.pct_20d:.1f}%" if high_20 else
        f"距52周高点 {((ind.high52w - snapshot.price)/ind.high52w*100):.1f}%"
    )
    stagnation_desc = (
        f"今日{snapshot.pct_change:.2f}%" if pct_low else f"上影{upper_shadow_ratio:.1f}%"
    )
    return True, (
        f"高位滞涨：{high_desc} + 量比{ind.volume_ratio:.2f}x + {stagnation_desc}"
    )


# ----- 一票否决：乖离率过大 -----
def veto_bias(ind: IndicatorBundle, cfg: StrategyConfig) -> Tuple[bool, str]:
    if ind.bias10 == ind.bias10 and ind.bias10 > cfg.veto_bias10:
        return True, f"BIAS10={ind.bias10:.2f}% > {cfg.veto_bias10}%"
    if ind.bias20 == ind.bias20 and ind.bias20 > cfg.veto_bias20:
        return True, f"BIAS20={ind.bias20:.2f}% > {cfg.veto_bias20}%"
    return False, ""


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


def sig_macd_pullback_entry(
    klines: pd.DataFrame,
    snapshot: StockSnapshot,
    ind: IndicatorBundle,
    lookback: int = 60,
    impulse_min_bars: int = 2,
    pullback_min_bars: int = 2,
    pullback_max_bars: int = 20,
    shrink_ratio: float = 0.85,
    volume_ratio_min: float = 1.2,
) -> Tuple[bool, str]:
    """组合策略：底部启动 → 缩量回调不破颈 → 当日阳线放量金叉。

    分四段：
      1) 启动段：lookback 内出现过 MACD 金叉(hist 由负转正)，之后至少 impulse_min_bars
         根红柱递增（小红→大红），并记录启动段最高的 hist 与最高价(=颈线)。
      2) 回调段：启动段之后出现 hist 见顶回落，hist/红柱缩水或转绿；持续 pullback_min_bars
         ~ pullback_max_bars 根；其中 dif 未创启动段新高(顶背离)。
      3) 颈线防守：回调段最低收盘价 >= 启动段起点收盘 × 0.97（不有效跌破）。
      4) 当日触发：阳线(close>open) + 放量(volume >= 5日均量 × volume_ratio_min)
         + 当日 MACD 金叉(hist 由 <=0 转 >0，或 dif 上穿 dea)。
    """
    if klines is None or klines.empty or len(klines) < 35:
        return False, "K线不足"
    needed = {"close", "open", "volume"}
    if not needed.issubset(klines.columns):
        return False, "缺列(close/open/volume)"

    from .indicators import macd as _macd

    closes = klines["close"].astype(float)
    opens = klines["open"].astype(float)
    vols = klines["volume"].astype(float)
    dif, dea, hist = _macd(closes)

    n = len(closes)
    win = min(lookback, n - 1)
    if win < 20:
        return False, "lookback不足"
    # 在 [n-win, n-1] 范围内找最近一次金叉（hist 前<=0 当前>0）
    cross_idx = -1
    for i in range(n - win + 1, n):
        h_prev = hist.iloc[i - 1]
        h_now = hist.iloc[i]
        if pd.isna(h_prev) or pd.isna(h_now):
            continue
        if h_prev <= 0 < h_now:
            cross_idx = i
    if cross_idx < 0 or cross_idx >= n - (impulse_min_bars + pullback_min_bars):
        return False, "lookback内无可用底部金叉"

    # 1) 启动段：金叉后红柱递增的根数
    impulse_end = cross_idx
    for j in range(cross_idx + 1, n):
        h_j = hist.iloc[j]
        h_prev = hist.iloc[j - 1]
        if pd.isna(h_j) or pd.isna(h_prev):
            break
        if h_j > 0 and h_j >= h_prev:
            impulse_end = j
        else:
            break
    impulse_bars = impulse_end - cross_idx
    if impulse_bars < impulse_min_bars:
        return False, f"启动段红柱仅{impulse_bars}<{impulse_min_bars}"

    impulse_peak_hist = float(hist.iloc[cross_idx: impulse_end + 1].max())
    impulse_peak_dif = float(dif.iloc[cross_idx: impulse_end + 1].max())
    impulse_peak_close = float(closes.iloc[cross_idx: impulse_end + 1].max())
    impulse_start_close = float(closes.iloc[cross_idx])  # 颈线/启动平台

    # 2) 回调段：impulse_end+1 ~ n-2（昨天为止），hist 缩水或转绿
    pullback_start = impulse_end + 1
    pullback_end = n - 2  # 不含今天，今天用于触发判定
    pullback_bars = pullback_end - pullback_start + 1
    if pullback_bars < pullback_min_bars:
        return False, f"回调段仅{pullback_bars}根<{pullback_min_bars}"
    if pullback_bars > pullback_max_bars:
        return False, f"回调段已 {pullback_bars}>上限{pullback_max_bars}, 形态过期"

    pullback_hist = hist.iloc[pullback_start: pullback_end + 1]
    pullback_dif = dif.iloc[pullback_start: pullback_end + 1]
    pullback_closes = closes.iloc[pullback_start: pullback_end + 1]
    pullback_vols = vols.iloc[pullback_start: pullback_end + 1]
    impulse_vols = vols.iloc[cross_idx: impulse_end + 1]

    if pullback_hist.isna().any() or pullback_dif.isna().any():
        return False, "MACD缺失"

    # 红柱顶 → 衰减：回调段 hist 最小值 < 启动段 hist 峰值
    if float(pullback_hist.min()) >= impulse_peak_hist:
        return False, "无红柱衰减"

    # 3) 顶背离：回调段 dif 最高未超过启动段峰值；且回调段最高 close 接近或超启动峰但 dif 没跟上
    pullback_peak_dif = float(pullback_dif.max())
    pullback_peak_close = float(pullback_closes.max())
    divergence = (pullback_peak_close >= impulse_peak_close * 0.98) and (pullback_peak_dif < impulse_peak_dif)
    # 没有严格顶背离也允许，但要求至少 dif 跟随回落
    if not divergence and pullback_peak_dif >= impulse_peak_dif:
        return False, "未见顶背离/dif创新高"

    # 4) 不破颈线：回调段最低 close >= 启动起点 close × 0.97
    if float(pullback_closes.min()) < impulse_start_close * 0.97:
        return False, "跌破颈线启动平台"

    # 5) 回调缩量：回调期均量 < 启动期均量 × shrink_ratio
    if float(impulse_vols.mean()) > 0:
        vol_shrink = float(pullback_vols.mean()) / float(impulse_vols.mean())
        if vol_shrink > shrink_ratio:
            return False, f"回调未缩量 vol={vol_shrink:.2f}>{shrink_ratio}"
    else:
        vol_shrink = float("nan")

    # 6) 当日触发：阳线 + 放量 + 金叉
    today_close = float(closes.iloc[-1])
    today_open = float(opens.iloc[-1])
    is_red = today_close > today_open
    if not is_red:
        return False, "今日非阳线"

    vol_ratio = ind.volume_ratio if not pd.isna(ind.volume_ratio) else float("nan")
    if pd.isna(vol_ratio) or vol_ratio < volume_ratio_min:
        return False, f"今日未放量 量比={vol_ratio:.2f}<{volume_ratio_min}"

    h_today = float(hist.iloc[-1])
    h_yest = float(hist.iloc[-2])
    dif_today = float(dif.iloc[-1])
    dea_today = float(dea.iloc[-1])
    dif_yest = float(dif.iloc[-2])
    dea_yest = float(dea.iloc[-2])
    cross_today = (h_yest <= 0 < h_today) or (dif_yest <= dea_yest and dif_today > dea_today)
    if not cross_today:
        return False, "今日未金叉"

    vol_desc = "" if pd.isna(vol_shrink) else f" 缩量{vol_shrink:.2f}x"
    return True, (
        f"底部启动{impulse_bars}红柱→回调{pullback_bars}根{vol_desc}"
        f"→今日阳线放量{vol_ratio:.2f}x+MACD金叉"
    )


def evaluate(
    snapshot: StockSnapshot, klines: pd.DataFrame, cfg: StrategyConfig
) -> Tuple[StrategyResult, IndicatorBundle]:
    result = StrategyResult()
    ind = compute_indicators(snapshot, klines)

    # 1) 硬过滤
    if cfg.risk_enabled:
        ok, msg = risk_check(snapshot)
        result.risk_passed = ok
        result.risk_reason = msg
        if not ok:
            result.misses.append(f"[RISK] {msg}")
            return result, ind

    # 2) 一票否决
    if cfg.veto_stagnation_enabled:
        v, msg = veto_high_stagnation(snapshot, klines, ind, cfg)
        if v:
            result.vetoed = True
            result.veto_reasons.append(f"[VETO_STAGNATION] {msg}")
            result.details.append(f"[VETO_STAGNATION] {msg}")
    if cfg.veto_bias_enabled:
        v, msg = veto_bias(ind, cfg)
        if v:
            result.vetoed = True
            result.veto_reasons.append(f"[VETO_BIAS] {msg}")
            result.details.append(f"[VETO_BIAS] {msg}")
    if result.vetoed:
        return result, ind

    # 3) 信号策略
    signal_runners = [
        ("MA_CROSS", cfg.ma_enabled, lambda: sig_ma_cross(klines, ind)),
        ("MACD", cfg.macd_enabled, lambda: sig_macd(ind)),
        ("RSI", cfg.rsi_enabled, lambda: sig_rsi(ind, snapshot)),
        ("BREAKOUT", cfg.breakout_enabled, lambda: sig_breakout(klines, ind)),
        ("PATTERN", cfg.pattern_enabled, lambda: sig_pattern(klines)),
        ("VOLUME", cfg.volume_enabled, lambda: sig_volume(snapshot, ind, cfg.volume_ratio_min)),
        ("PCT_RANGE", cfg.pct_enabled, lambda: sig_pct_range(snapshot, cfg.pct_min, cfg.pct_max)),
        ("LIQUIDITY", cfg.liquidity_enabled, lambda: sig_liquidity(snapshot, cfg.turnover_floor, cfg.amplitude_cap)),
        ("MACD_PULLBACK", cfg.macd_pullback_enabled, lambda: sig_macd_pullback_entry(
            klines, snapshot, ind,
            lookback=cfg.macd_pullback_lookback,
            impulse_min_bars=cfg.macd_pullback_impulse_min,
            pullback_min_bars=cfg.macd_pullback_min,
            pullback_max_bars=cfg.macd_pullback_max,
            shrink_ratio=cfg.macd_pullback_shrink_ratio,
            volume_ratio_min=cfg.volume_ratio_min,
        )),
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
            if name == "MACD_PULLBACK":
                result.must_push = True
                result.must_push_reasons.append(msg)
        else:
            result.misses.append(line)
    return result, ind
