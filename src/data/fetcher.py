"""A股数据抓取：成交额前 N + 同花顺热榜前 N，合并去重。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple

import pandas as pd

from ..models import StockSnapshot

logger = logging.getLogger(__name__)


_SPOT_RENAME = {
    "代码": "code",
    "名称": "name",
    "最新价": "price",
    "涨跌幅": "pct_change",
    "成交量": "volume",
    "成交额": "turnover",
    "振幅": "amplitude",
    "最高": "high",
    "最低": "low",
    "今开": "open",
    "昨收": "pre_close",
}


def _is_excluded(code: str, name: str, exclude_prefixes: List[str], exclude_name_keywords: List[str]) -> bool:
    code = str(code).strip()
    name = str(name).strip()
    for p in exclude_prefixes:
        if p and code.startswith(p):
            return True
    for kw in exclude_name_keywords:
        if kw and kw in name:
            return True
    return False


def _load_spot() -> pd.DataFrame:
    """拉一次全市场实时行情并标准化列。"""
    import akshare as ak

    df: pd.DataFrame = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        return pd.DataFrame()
    cols = {k: v for k, v in _SPOT_RENAME.items() if k in df.columns}
    df = df.rename(columns=cols)
    for c in ("price", "pct_change", "volume", "turnover", "amplitude", "high", "low", "open", "pre_close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = df["code"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    return df


def _fetch_ths_hot_codes(top_n: int) -> List[str]:
    """同花顺/问财热榜前 N 的股票代码。"""
    import akshare as ak

    try:
        df = ak.stock_hot_rank_wc()
    except Exception as e:  # noqa: BLE001
        logger.warning("拉取同花顺热榜失败: %s", e)
        return []
    if df is None or df.empty:
        return []
    code_col = None
    for c in ("股票代码", "代码", "code"):
        if c in df.columns:
            code_col = c
            break
    if not code_col:
        logger.warning("热榜数据无代码列，columns=%s", list(df.columns))
        return []
    codes = df[code_col].astype(str).str.strip().str.zfill(6).tolist()
    return codes[:top_n]


def fetch_universe(
    top_n_turnover: int,
    top_n_hot: int,
    exclude_prefixes: List[str],
    exclude_name_keywords: List[str],
) -> List[StockSnapshot]:
    """合并“成交额前N”和“同花顺热榜前N”，去重并应用排除规则。"""
    logger.info("拉取全市场实时行情 ...")
    spot = _load_spot()
    if spot.empty:
        logger.warning("行情数据为空")
        return []

    # 排除
    mask = spot.apply(
        lambda r: not _is_excluded(r["code"], r["name"], exclude_prefixes, exclude_name_keywords),
        axis=1,
    )
    spot = spot[mask].copy()

    # Top N by turnover
    if "turnover" not in spot.columns:
        raise RuntimeError("行情数据缺少 turnover 列")
    spot_sorted = spot.dropna(subset=["turnover"]).sort_values("turnover", ascending=False)
    top_turnover_codes: List[str] = spot_sorted.head(top_n_turnover)["code"].tolist()

    # 同花顺热榜
    hot_codes = _fetch_ths_hot_codes(top_n_hot)
    # 过滤排除
    spot_idx: Dict[str, pd.Series] = {row["code"]: row for _, row in spot.iterrows()}
    hot_codes_filtered: List[str] = []
    for c in hot_codes:
        row = spot_idx.get(c)
        if row is None:
            continue  # 行情中无此票（可能停牌或非A股）
        if _is_excluded(row["code"], row["name"], exclude_prefixes, exclude_name_keywords):
            continue
        hot_codes_filtered.append(c)

    logger.info(
        "成交额TopN=%d 命中=%d；热榜TopN=%d 过滤后=%d",
        top_n_turnover, len(top_turnover_codes), top_n_hot, len(hot_codes_filtered),
    )

    # 合并去重并记录来源（保持顺序：先 turnover，再 hot 补）
    sources: Dict[str, Set[str]] = {}
    order: List[str] = []
    for c in top_turnover_codes:
        sources.setdefault(c, set()).add("turnover")
        if c not in order:
            order.append(c)
    for c in hot_codes_filtered:
        sources.setdefault(c, set()).add("ths_hot")
        if c not in order:
            order.append(c)

    snapshots: List[StockSnapshot] = []
    for c in order:
        row = spot_idx.get(c)
        if row is None:
            continue
        snapshots.append(
            StockSnapshot(
                code=c,
                name=str(row.get("name", "")).strip(),
                price=float(row.get("price", 0) or 0),
                pct_change=float(row.get("pct_change", 0) or 0),
                volume=float(row.get("volume", 0) or 0),
                turnover=float(row.get("turnover", 0) or 0),
                amplitude=float(row.get("amplitude", 0) or 0),
                high=float(row.get("high", 0) or 0),
                low=float(row.get("low", 0) or 0),
                open=float(row.get("open", 0) or 0),
                pre_close=float(row.get("pre_close", 0) or 0),
                sources=sorted(sources[c]),
            )
        )
    logger.info("合并去重后样本数: %d", len(snapshots))
    return snapshots


def fetch_recent_klines(code: str, days: int = 250) -> pd.DataFrame:
    """获取单只股票最近 N 个交易日的日线，用于策略指标计算。"""
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    # 留足缓冲（节假日 + 停牌）
    start = (datetime.now() - timedelta(days=int(days * 1.6) + 30)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("获取K线失败 %s: %s", code, e)
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df = df.tail(days).reset_index(drop=True)
    return df


def fetch_spot_prices(codes: List[str]) -> Dict[str, Tuple[float, float, str]]:
    """查询若干股票当前价/涨跌幅/名称（用于上次推送票的回看对比）。

    返回 {code: (price, pct_change, name)}；查不到的代码不在返回 dict 中。
    """
    if not codes:
        return {}
    try:
        spot = _load_spot()
    except Exception as e:  # noqa: BLE001
        logger.warning("拉取实时行情失败: %s", e)
        return {}
    if spot.empty:
        return {}
    wanted = set(str(c).strip() for c in codes)
    sub = spot[spot["code"].isin(wanted)]
    out: Dict[str, Tuple[float, float, str]] = {}
    for _, r in sub.iterrows():
        out[str(r["code"])] = (
            float(r.get("price", 0) or 0),
            float(r.get("pct_change", 0) or 0),
            str(r.get("name", "")).strip(),
        )
    return out
