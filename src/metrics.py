"""结构化运行指标：每次 run 写入一行 JSON 到 logs/metrics.jsonl。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def append_metrics(log_dir: Path, payload: Dict[str, Any]) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {"ts": datetime.now().isoformat(timespec="seconds"), **payload}
    try:
        with (log_dir / "metrics.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("写指标失败: %s", e)
