"""数值格式化共享逻辑。"""
from __future__ import annotations

import math
from typing import Any


def safe_fmt(v: Any, digits: int = 2, default: str = "N/A") -> str:
    """将数值格式化为定长小数字符串；None/NaN/Inf 返回 default。"""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f"{f:.{digits}f}"
