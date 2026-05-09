"""大盘环境过滤：上证指数与 MA20 的关系。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def _fetch_index_daily() -> pd.DataFrame:
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
    for fn, kwargs in [
        ("index_zh_a_hist", dict(symbol="000001", period="daily", start_date=start, end_date=end)),
        ("stock_zh_index_daily_em", dict(symbol="sh000001")),
    ]:
        try:
            df = getattr(ak, fn)(**kwargs)
            if df is not None and not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            logger.debug("大盘指数接口 %s 失败: %s", fn, e)
    return pd.DataFrame()


def assess_market() -> Tuple[Optional[bool], dict]:
    """评估上证指数当前状态。

    返回 (above_ma20, info)；above_ma20=None 表示数据不可用。
    info: {close, ma20, pct_change}
    """
    df = _fetch_index_daily()
    if df is None or df.empty:
        return None, {"reason": "指数数据不可用"}
    rename = {"日期": "date", "收盘": "close", "close": "close", "open": "open", "涨跌幅": "pct_change"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "close" not in df.columns:
        return None, {"reason": f"列缺失 {list(df.columns)}"}
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < 20:
        return None, {"reason": f"指数样本不足({len(closes)})"}
    ma20 = float(closes.tail(20).mean())
    cur = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else cur
    pct = (cur / prev - 1) * 100 if prev else 0.0
    return cur >= ma20, {"close": cur, "ma20": ma20, "pct_change": pct}
