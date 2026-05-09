"""行业归属：用同花顺概念/申万行业板块构造 code->industry 映射。

策略：本地 JSON 缓存 7 天，超期重建。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def _build_industry_map() -> Dict[str, str]:
    import akshare as ak

    out: Dict[str, str] = {}
    try:
        boards = ak.stock_board_industry_name_em()
    except Exception as e:  # noqa: BLE001
        logger.warning("拉取行业板块列表失败: %s", e)
        return out
    if boards is None or boards.empty:
        return out
    name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[0]
    names = boards[name_col].astype(str).tolist()
    for i, name in enumerate(names):
        try:
            cons = ak.stock_board_industry_cons_em(symbol=name)
        except Exception as e:  # noqa: BLE001
            logger.debug("行业 %s 拉取成分股失败: %s", name, e)
            continue
        if cons is None or cons.empty:
            continue
        code_col = None
        for c in ("代码", "股票代码", "code"):
            if c in cons.columns:
                code_col = c
                break
        if not code_col:
            continue
        for code in cons[code_col].astype(str).str.zfill(6).tolist():
            out.setdefault(code, name)
        if (i + 1) % 10 == 0:
            time.sleep(0.5)
    logger.info("行业映射构建完成: %d 个板块, %d 只股票", len(names), len(out))
    return out


def load_industry_map(cache_path: Path, ttl_days: int = 7) -> Dict[str, str]:
    cache_path = Path(cache_path)
    if cache_path.exists():
        try:
            stat = cache_path.stat()
            age = datetime.now() - datetime.fromtimestamp(stat.st_mtime)
            if age < timedelta(days=ttl_days):
                with cache_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取行业缓存失败: %s", e)
    data = _build_industry_map()
    if data:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return data
