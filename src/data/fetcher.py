"""A股数据抓取：成交额前 N + 同花顺热榜前 N，合并去重。"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set, Tuple

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
    # 新浪/腾讯字段别名
    "trade": "price",
    "changepercent": "pct_change",
    "volume": "volume",
    "amount": "turnover",
    "high": "high",
    "low": "low",
    "open": "open",
    "settlement": "pre_close",
    "symbol": "code",
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


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    cols = {k: v for k, v in _SPOT_RENAME.items() if k in df.columns}
    df = df.rename(columns=cols)
    if "code" not in df.columns:
        return pd.DataFrame()
    for c in ("price", "pct_change", "volume", "turnover", "amplitude", "high", "low", "open", "pre_close"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["code"] = df["code"].astype(str).str.strip()
    # 去掉 sh/sz/bj 前缀
    df["code"] = df["code"].str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6)
    if "name" in df.columns:
        df["name"] = df["name"].astype(str).str.strip()
    else:
        df["name"] = df["code"]
    if "amplitude" not in df.columns and {"high", "low", "pre_close"}.issubset(df.columns):
        df["amplitude"] = (df["high"] - df["low"]) / df["pre_close"] * 100
    return df


def _spot_em() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    return _normalize_spot(df)


def _spot_sina() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_spot()  # 新浪
    return _normalize_spot(df)


_SPOT_SOURCES = [
    ("em", _spot_em),
    ("sina", _spot_sina),
]


def _load_spot() -> pd.DataFrame:
    """拉一次全市场实时行情：东财→新浪 双源回退 + 重试。"""
    import os

    pref = os.getenv("SPOT_SOURCE", "auto").strip().lower()
    sources = _SPOT_SOURCES
    if pref == "em":
        sources = [_SPOT_SOURCES[0]]
    elif pref == "sina":
        sources = [_SPOT_SOURCES[1], _SPOT_SOURCES[0]]

    last_err: Optional[Exception] = None
    for name, fn in sources:
        for i in range(3):
            try:
                df = fn()
                if df is not None and not df.empty:
                    logger.info("行情源 %s 成功 (尝试 %d 次)", name, i + 1)
                    return df
                logger.warning("行情源 %s 返回空，第 %d 次", name, i + 1)
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = min(2 ** i, 8)
                logger.warning("行情源 %s 失败(第%d次): %s，%ss 后重试", name, i + 1, e, wait)
                time.sleep(wait)
        logger.warning("行情源 %s 三次均失败，尝试下一个源", name)

    if last_err is not None:
        raise last_err
    return pd.DataFrame()


def _fetch_ths_hot_codes(top_n: int) -> List[str]:
    """热榜前 N 的股票代码：依次尝试多个 AKShare 源，全部失败则返回空列表（非致命）。"""
    import akshare as ak

    # 尝试顺序：东财热榜（标准 100 行）→ 同花顺 → 问财 → 人气飙升 → 百度热搜
    # 注：stock_hot_rank_latest_em 返回的是 key-value 摘要（item/value），不是榜单，放最后兜底
    candidates = (
        "stock_hot_rank_em",
        "stock_hot_rank_ths",
        "stock_hot_rank_wc",
        "stock_hot_up_em",
        "stock_hot_search_baidu",
        "stock_hot_rank_latest_em",
    )
    code_col_candidates = (
        "股票代码", "代码", "code", "symbol", "Symbol", "证券代码",
        "名称/代码", "名称代码", "股票名称代码",
    )
    df = None
    code_col = None
    tried = []
    for fname in candidates:
        fn = getattr(ak, fname, None)
        if fn is None:
            continue
        tried.append(fname)
        try:
            d = fn()
            if d is None or d.empty:
                logger.info("热榜源 %s 返回空，继续尝试下一源", fname)
                continue
            cc = next((c for c in code_col_candidates if c in d.columns), None)
            if not cc:
                # 兜底：在任意字符串列中尝试抽 6 位数字（例如百度热搜可能返回合并列）
                for c in d.columns:
                    sample = d[c].astype(str).head(20).str.extract(r"(\d{6})", expand=False).dropna()
                    if len(sample) >= max(3, len(d) // 3):
                        cc = c
                        logger.info("热榜源 %s 使用兜底列 %s 提取代码", fname, c)
                        break
            if not cc:
                logger.info("热榜源 %s 命中但无代码列 columns=%s，继续尝试下一源",
                            fname, list(d.columns))
                continue
            df, code_col = d, cc
            logger.info("热榜源 %s 命中 (%d 行，代码列=%s)", fname, len(df), code_col)
            break
        except Exception as e:  # noqa: BLE001
            logger.info("热榜源 %s 调用失败: %s", fname, e)
    if df is None or df.empty or not code_col:
        if tried:
            logger.info("热榜源全部不可用 (尝试过 %s)，跳过热榜补充", ",".join(tried))
        return []
    raw = df[code_col].astype(str).str.strip()
    # 大小写都处理：SH600000 / sh600000 / 600000.SH 等格式统一为 6 位纯数字
    # 用 \d{6} 精确匹配 6 位数字，避免合并列里抽到非代码的短数字
    codes = (
        raw.str.replace(r"^(sh|sz|bj)", "", regex=True, case=False)
           .str.replace(r"\.(sh|sz|bj)$", "", regex=True, case=False)
           .str.extract(r"(\d{6})", expand=False)
           .fillna("")
           .tolist()
    )
    codes = [c for c in codes if c and c != "000000"]
    if codes:
        logger.debug("热榜样本代码: %s", codes[:5])
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

    # 热榜（外部 API，不稳定）
    hot_codes = _fetch_ths_hot_codes(top_n_hot)
    hot_source_label = "ths_hot"
    # 过滤排除
    spot_idx: Dict[str, pd.Series] = {row["code"]: row for _, row in spot.iterrows()}
    hot_codes_filtered: List[str] = []
    miss_not_in_spot: List[str] = []
    miss_excluded: List[str] = []
    for c in hot_codes:
        row = spot_idx.get(c)
        if row is None:
            miss_not_in_spot.append(c)
            continue  # 行情中无此票（可能停牌或非A股）
        if _is_excluded(row["code"], row["name"], exclude_prefixes, exclude_name_keywords):
            miss_excluded.append(c)
            continue
        hot_codes_filtered.append(c)
    if hot_codes and not hot_codes_filtered:
        logger.warning(
            "热榜 %d 个代码全部被过滤掉：行情中找不到=%d (%s)，被排除规则剔除=%d (%s)",
            len(hot_codes), len(miss_not_in_spot), miss_not_in_spot[:5],
            len(miss_excluded), miss_excluded[:5],
        )

    # 兜底：热榜不可用时，用 spot 中"涨幅榜前 N"补充（剔除一字板会在下游 filters 里做）
    if not hot_codes_filtered and "pct_change" in spot.columns:
        gainers = (
            spot.dropna(subset=["pct_change"])
                .sort_values("pct_change", ascending=False)
                .head(top_n_hot * 2)  # 多取一些，下面再过滤
        )
        for _, row in gainers.iterrows():
            c = row["code"]
            if _is_excluded(c, row["name"], exclude_prefixes, exclude_name_keywords):
                continue
            hot_codes_filtered.append(c)
            if len(hot_codes_filtered) >= top_n_hot:
                break
        hot_source_label = "gainers"
        logger.info(
            "热榜无可用代码(拉取=%d, 行情命中=%d, 排除=%d)，已用涨幅榜前 %d 兜底",
            len(hot_codes), len(miss_not_in_spot), len(miss_excluded), len(hot_codes_filtered),
        )

    logger.info(
        "成交额TopN=%d 命中=%d；热榜TopN=%d 过滤后=%d (来源=%s)",
        top_n_turnover, len(top_turnover_codes), top_n_hot,
        len(hot_codes_filtered), hot_source_label,
    )

    # 合并去重并记录来源（保持顺序：先 turnover，再 hot 补）
    sources: Dict[str, Set[str]] = {}
    order: List[str] = []
    for c in top_turnover_codes:
        sources.setdefault(c, set()).add("turnover")
        if c not in order:
            order.append(c)
    for c in hot_codes_filtered:
        sources.setdefault(c, set()).add(hot_source_label)
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
    return _fetch_klines_by_range(code, start, end, days)


def _kline_em(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_hist(
        symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq"
    )
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
    return df.rename(columns={k: v for k, v in rename.items() if k in df.columns})


def _kline_sina(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """新浪日线作为东财失败时的回退。symbol 需要带 sh/sz/bj 前缀。"""
    import akshare as ak

    c = str(code).zfill(6)
    if c.startswith(("60", "68", "9")):
        sym = "sh" + c
    elif c.startswith(("4", "8")):
        sym = "bj" + c
    else:
        sym = "sz" + c
    df = ak.stock_zh_a_daily(symbol=sym, start_date=start_date, end_date=end_date, adjust="qfq")
    if df is None or df.empty:
        return pd.DataFrame()
    if "date" in df.columns:
        df = df.copy()
        df["date"] = df["date"].astype(str)
    # 新浪没有 amplitude / pct_change，补一下
    if "close" in df.columns and "pct_change" not in df.columns:
        df["pct_change"] = df["close"].pct_change() * 100
    if {"high", "low", "close"}.issubset(df.columns) and "amplitude" not in df.columns:
        prev_close = df["close"].shift(1)
        df["amplitude"] = (df["high"] - df["low"]) / prev_close * 100
    return df


def _fetch_klines_by_range(code: str, start_date: str, end_date: Optional[str], days: int) -> pd.DataFrame:
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")

    last_err: Optional[Exception] = None
    for source_name, fn in (("em", _kline_em), ("sina", _kline_sina)):
        for i in range(3):
            try:
                df = fn(code, start_date, end_date)
                if df is not None and not df.empty:
                    if "date" in df.columns:
                        df["date"] = df["date"].astype(str)
                    return df.tail(days).reset_index(drop=True)
                break  # 空数据不重试，直接换源
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = min(2 ** i, 4)
                logger.debug("K线 %s 源 %s 第%d次失败: %s", code, source_name, i + 1, e)
                time.sleep(wait)
    # 北交所 920xxx / 83/87/88xxxx 等多数源不支持，给一行更清晰的提示
    if str(code).startswith(("920", "83", "87", "88")):
        logger.info("跳过北交所代码 %s（akshare 多数源不支持，建议在 EXCLUDE_PREFIXES 排除）", code)
    else:
        logger.warning("获取K线失败 %s: %s", code, last_err)
    return pd.DataFrame()


# ---------- 并发批量拉取 ----------

_RATE_LOCK = threading.Lock()
_LAST_CALL_TS: Dict[int, float] = {}


def _rate_limited_call(min_interval: float, fn, *args, **kwargs):
    """简单 per-thread 节流：避免 AKShare 限流。"""
    if min_interval > 0:
        tid = threading.get_ident()
        with _RATE_LOCK:
            last = _LAST_CALL_TS.get(tid, 0.0)
            now = time.time()
            wait = (last + min_interval) - now
            if wait > 0:
                time.sleep(wait)
            _LAST_CALL_TS[tid] = time.time()
    return fn(*args, **kwargs)


def fetch_klines_concurrent(
    codes: List[str],
    days: int,
    cache=None,
    max_workers: int = 6,
    rate_interval: float = 0.1,
    progress_every: int = 20,
) -> Dict[str, pd.DataFrame]:
    """并发拉取多只股票的 K 线，可选 KlineCache 加速。"""
    out: Dict[str, pd.DataFrame] = {}
    if not codes:
        return out

    def _one(code: str) -> Tuple[str, pd.DataFrame]:
        try:
            if cache is not None:
                def _remote(c: str, start_yyyymmdd: str) -> pd.DataFrame:
                    return _rate_limited_call(
                        rate_interval, _fetch_klines_by_range, c, start_yyyymmdd, None, days,
                    )
                df = cache.get(code, days, _remote)
            else:
                df = _rate_limited_call(rate_interval, fetch_recent_klines, code, days)
            return code, df
        except Exception as e:  # noqa: BLE001
            logger.warning("并发拉K线失败 %s: %s", code, e)
            return code, pd.DataFrame()

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        futures = [ex.submit(_one, c) for c in codes]
        for fut in as_completed(futures):
            code, df = fut.result()
            out[code] = df
            done += 1
            if progress_every and done % progress_every == 0:
                logger.info("K线并发进度 %d/%d", done, len(codes))
    return out


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
