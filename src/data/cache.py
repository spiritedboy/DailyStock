"""K 线本地缓存：每只股票一个 CSV，增量更新。

策略：
- 文件路径 {cache_dir}/{code}.csv
- 命中：本地 last_date >= today（或最近交易日），直接返回 tail(days)
- 未命中：从 (last_date - 5) 起补拉，与本地拼接、去重、保存

并发安全：单进程使用，CSV 通过原子 rename 写入。
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


class KlineCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path(self, code: str) -> Path:
        return self.cache_dir / f"{code}.csv"

    def _read_local(self, code: str) -> pd.DataFrame:
        p = self.path(code)
        if not p.exists():
            return pd.DataFrame()
        try:
            df = pd.read_csv(p)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取K线缓存失败 %s: %s", code, e)
            return pd.DataFrame()
        if "date" in df.columns:
            df["date"] = df["date"].astype(str)
        return df

    def _write_local(self, code: str, df: pd.DataFrame) -> None:
        p = self.path(code)
        tmp = p.with_suffix(".csv.tmp")
        with _LOCK:
            df.to_csv(tmp, index=False)
            os.replace(tmp, p)

    def get(self, code: str, days: int, fetch_remote) -> pd.DataFrame:
        """返回最近 days 个交易日的 K 线 DataFrame。

        fetch_remote: callable(code, start_yyyymmdd) -> pd.DataFrame  日线"""
        local = self._read_local(code)
        today = datetime.now().date()
        last_date: Optional[str] = None
        if not local.empty and "date" in local.columns:
            last_date = str(local["date"].iloc[-1])

        need_fetch = True
        if last_date:
            try:
                ld = datetime.strptime(last_date, "%Y-%m-%d").date()
                # 周五缓存 + 周末视为有效，避免重复拉
                if ld >= today or (today.weekday() >= 5 and ld >= today - timedelta(days=today.weekday() - 4)):
                    need_fetch = False
            except ValueError:
                pass

        if need_fetch:
            start = (datetime.now() - timedelta(days=int(days * 1.6) + 30)).date()
            if last_date:
                try:
                    ld = datetime.strptime(last_date, "%Y-%m-%d").date()
                    start = max(start, ld - timedelta(days=5))
                except ValueError:
                    pass
            try:
                remote = fetch_remote(code, start.strftime("%Y%m%d"))
            except Exception as e:  # noqa: BLE001
                logger.warning("拉取K线失败 %s: %s", code, e)
                remote = pd.DataFrame()
            if not remote.empty:
                if not local.empty:
                    merged = pd.concat([local, remote], ignore_index=True)
                    merged = merged.drop_duplicates(subset=["date"], keep="last")
                    merged = merged.sort_values("date").reset_index(drop=True)
                else:
                    merged = remote.sort_values("date").reset_index(drop=True)
                self._write_local(code, merged)
                local = merged

        if local.empty:
            return local
        return local.tail(days).reset_index(drop=True)
