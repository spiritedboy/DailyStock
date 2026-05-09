"""SQLite + CSV 持久化。"""
from __future__ import annotations

import csv
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from ..models import StockEvaluation

logger = logging.getLogger(__name__)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        run_date TEXT NOT NULL,
        run_slot TEXT NOT NULL,
        total INTEGER, vetoed INTEGER, candidates INTEGER,
        ai_called INTEGER, ai_allowed INTEGER, focus_count INTEGER,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        run_date TEXT NOT NULL,
        run_slot TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        sources TEXT,
        price REAL, pct_change REAL, turnover REAL, amplitude REAL,
        ma5 REAL, ma10 REAL, ma20 REAL,
        macd_hist REAL, rsi14 REAL, volume_ratio REAL,
        bias10 REAL, bias20 REAL, pct_20d REAL, high52w REAL,
        risk_passed INTEGER, vetoed INTEGER, veto_reasons TEXT,
        hits INTEGER, signals TEXT, misses TEXT,
        ai_ok INTEGER, ai_score INTEGER, ai_allow INTEGER, ai_reason TEXT,
        is_candidate INTEGER, is_focus INTEGER, is_pushed INTEGER,
        created_at TEXT NOT NULL,
        UNIQUE(run_date, run_slot, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        run_date TEXT NOT NULL,
        run_slot TEXT NOT NULL,
        kind TEXT NOT NULL,
        target TEXT,
        ok INTEGER NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(run_date, run_slot, kind, target)
    )
    """,
]


class Repository:
    def __init__(self, db_path: Path, csv_dir: Path):
        self.db_path = Path(db_path)
        self.csv_dir = Path(csv_dir)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            for s in SCHEMA:
                c.execute(s)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex

    def save_run(
        self, run_id: str, run_date: str, run_slot: str,
        total: int, vetoed: int, candidates: int,
        ai_called: int, ai_allowed: int, focus_count: int,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, run_date, run_slot, total, vetoed, candidates,
                 ai_called, ai_allowed, focus_count, now),
            )

    def save_picks(self, run_id: str, run_date: str, run_slot: str, evals: Iterable[StockEvaluation]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for e in evals:
            s, ind, st, ai = e.snapshot, e.indicators, e.strategy, e.ai
            rows.append((
                run_id, run_date, run_slot, s.code, s.name,
                ",".join(s.sources),
                s.price, s.pct_change, s.turnover, s.amplitude,
                ind.ma5, ind.ma10, ind.ma20,
                ind.macd_hist, ind.rsi14, ind.volume_ratio,
                ind.bias10, ind.bias20, ind.pct_20d, ind.high52w,
                1 if st.risk_passed else 0,
                1 if st.vetoed else 0,
                " | ".join(st.veto_reasons),
                st.hits,
                ",".join(st.signals),
                " | ".join(st.misses),
                1 if (ai and ai.ok) else 0,
                ai.score if ai else 0,
                1 if (ai and ai.allow) else 0,
                ai.reason if ai else "",
                1 if e.is_candidate else 0,
                1 if e.is_focus else 0,
                1 if e.is_pushed else 0,
                now,
            ))
        with self._conn() as c:
            c.executemany(
                """
                INSERT OR REPLACE INTO picks
                (run_id, run_date, run_slot, code, name, sources,
                 price, pct_change, turnover, amplitude,
                 ma5, ma10, ma20, macd_hist, rsi14, volume_ratio,
                 bias10, bias20, pct_20d, high52w,
                 risk_passed, vetoed, veto_reasons,
                 hits, signals, misses,
                 ai_ok, ai_score, ai_allow, ai_reason,
                 is_candidate, is_focus, is_pushed, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )

    def already_notified(self, run_date: str, run_slot: str, kind: str, target: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "SELECT 1 FROM notifications WHERE run_date=? AND run_slot=? AND kind=? AND target=? AND ok=1",
                (run_date, run_slot, kind, target),
            )
            return cur.fetchone() is not None

    def get_previous_pushed(
        self, current_run_date: str, current_run_slot: str,
    ):
        """返回上一次推送的 (run_date, run_slot, picks_list)。无则返回 ("", "", [])。

        slot 顺序：midday < close。
        """
        cur_ord = 1 if current_run_slot == "close" else 0
        with self._conn() as c:
            row = c.execute(
                """
                SELECT run_date, run_slot FROM picks
                WHERE is_pushed=1 AND (
                    run_date < ?
                    OR (run_date = ? AND CASE run_slot WHEN 'close' THEN 1 ELSE 0 END < ?)
                )
                ORDER BY run_date DESC,
                         CASE run_slot WHEN 'close' THEN 1 ELSE 0 END DESC
                LIMIT 1
                """,
                (current_run_date, current_run_date, cur_ord),
            ).fetchone()
            if not row:
                return "", "", []
            prev_date, prev_slot = row[0], row[1]
            rows = c.execute(
                """
                SELECT code, name, price, pct_change, ai_score, ai_reason, hits, signals,
                       sources, is_focus
                FROM picks
                WHERE run_date=? AND run_slot=? AND is_pushed=1
                ORDER BY ai_score DESC, hits DESC
                """,
                (prev_date, prev_slot),
            ).fetchall()
            picks = [
                {
                    "code": r[0], "name": r[1], "price": r[2], "pct_change": r[3],
                    "ai_score": r[4], "ai_reason": r[5], "hits": r[6],
                    "signals": r[7], "sources": r[8], "is_focus": bool(r[9]),
                }
                for r in rows
            ]
            return prev_date, prev_slot, picks

    def save_notification(
        self, run_id: str, run_date: str, run_slot: str,
        kind: str, target: str, ok: bool, detail: str = "",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """
                INSERT OR REPLACE INTO notifications
                (run_id, run_date, run_slot, kind, target, ok, detail, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (run_id, run_date, run_slot, kind, target, 1 if ok else 0, detail, now),
            )

    def export_csv(self, run_date: str, run_slot: str, evals: List[StockEvaluation]) -> Path:
        fname = f"picks_{run_date}_{run_slot}.csv"
        path = self.csv_dir / fname
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "code", "name", "sources",
                "price", "pct_change", "turnover", "amplitude",
                "ma5", "ma10", "ma20", "macd_hist", "rsi14", "volume_ratio",
                "bias10", "bias20", "pct_20d", "high52w",
                "risk_passed", "vetoed", "veto_reasons",
                "hits", "signals", "misses",
                "ai_ok", "ai_score", "ai_allow", "ai_reason",
                "is_candidate", "is_focus", "is_pushed",
            ])
            for e in evals:
                s, ind, st, ai = e.snapshot, e.indicators, e.strategy, e.ai
                w.writerow([
                    s.code, s.name, ",".join(s.sources),
                    s.price, s.pct_change, s.turnover, s.amplitude,
                    ind.ma5, ind.ma10, ind.ma20,
                    ind.macd_hist, ind.rsi14, ind.volume_ratio,
                    ind.bias10, ind.bias20, ind.pct_20d, ind.high52w,
                    int(st.risk_passed), int(st.vetoed),
                    " | ".join(st.veto_reasons),
                    st.hits, ",".join(st.signals), " | ".join(st.misses),
                    int(bool(ai and ai.ok)),
                    ai.score if ai else 0,
                    int(bool(ai and ai.allow)),
                    ai.reason if ai else "",
                    int(e.is_candidate), int(e.is_focus), int(e.is_pushed),
                ])
        return path