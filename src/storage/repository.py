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
    """
    CREATE TABLE IF NOT EXISTS pick_returns (
        run_date TEXT NOT NULL,
        run_slot TEXT NOT NULL,
        code TEXT NOT NULL,
        period INTEGER NOT NULL,
        target_date TEXT,
        push_price REAL,
        target_price REAL,
        ret_pct REAL,
        updated_at TEXT,
        PRIMARY KEY (run_date, run_slot, code, period)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_cache (
        run_date TEXT NOT NULL,
        code TEXT NOT NULL,
        score INTEGER, allow INTEGER, reason TEXT, raw TEXT,
        created_at TEXT,
        PRIMARY KEY (run_date, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_usage (
        run_date TEXT PRIMARY KEY,
        calls INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT
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

    # ---------- AI 缓存与配额 ----------
    def get_ai_cached(self, run_date: str, code: str):
        with self._conn() as c:
            row = c.execute(
                "SELECT score, allow, reason, raw FROM ai_cache WHERE run_date=? AND code=?",
                (run_date, code),
            ).fetchone()
            if not row:
                return None
            return {"score": row[0], "allow": bool(row[1]), "reason": row[2], "raw": row[3]}

    def save_ai_cached(self, run_date: str, code: str, score: int, allow: bool, reason: str, raw: str):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO ai_cache
                   (run_date, code, score, allow, reason, raw, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (run_date, code, int(score), 1 if allow else 0, reason, raw, now),
            )

    def get_ai_usage(self, run_date: str) -> int:
        with self._conn() as c:
            row = c.execute("SELECT calls FROM ai_usage WHERE run_date=?", (run_date,)).fetchone()
            return int(row[0]) if row else 0

    def incr_ai_usage(self, run_date: str, n: int = 1) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT INTO ai_usage(run_date, calls, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(run_date) DO UPDATE SET
                     calls=calls+excluded.calls, updated_at=excluded.updated_at""",
                (run_date, n, now),
            )
            row = c.execute("SELECT calls FROM ai_usage WHERE run_date=?", (run_date,)).fetchone()
            return int(row[0]) if row else 0

    # ---------- 推送跟踪 ----------
    def fetch_pending_returns(self, max_periods: int = 20):
        """返回需要计算 returns 的 (run_date, run_slot, code, price) 行（已推送 + 距今 <=max_periods*2 天）。"""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT run_date, run_slot, code, price FROM picks
                WHERE is_pushed=1
                  AND run_date >= date('now', ?)
                ORDER BY run_date DESC
                """,
                (f'-{max_periods * 2 + 5} days',),
            ).fetchall()
        return [{"run_date": r[0], "run_slot": r[1], "code": r[2], "price": r[3]} for r in rows]

    def upsert_pick_return(self, run_date, run_slot, code, period, target_date,
                           push_price, target_price, ret_pct):
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO pick_returns
                   (run_date, run_slot, code, period, target_date, push_price, target_price, ret_pct, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (run_date, run_slot, code, int(period), target_date,
                 push_price, target_price, ret_pct, now),
            )

    def list_returns(self, days: int = 30, period: int = 5):
        with self._conn() as c:
            rows = c.execute(
                """SELECT run_date, run_slot, code, ret_pct FROM pick_returns
                   WHERE period=? AND run_date >= date('now', ?)""",
                (period, f'-{days} days'),
            ).fetchall()
        return [{"run_date": r[0], "run_slot": r[1], "code": r[2], "ret_pct": r[3]} for r in rows]

    def signal_performance(self, days: int = 60, period: int = 5):
        """对每条信号策略，统计推送票中命中该信号的 N 日平均收益与胜率。"""
        with self._conn() as c:
            rows = c.execute(
                """SELECT p.signals, r.ret_pct
                   FROM picks p JOIN pick_returns r
                   ON p.run_date=r.run_date AND p.run_slot=r.run_slot AND p.code=r.code
                   WHERE p.is_pushed=1 AND r.period=?
                     AND p.run_date >= date('now', ?)""",
                (period, f'-{days} days'),
            ).fetchall()
        from collections import defaultdict
        bucket = defaultdict(list)
        for sigs, ret in rows:
            for s in (sigs or "").split(","):
                s = s.strip()
                if s:
                    bucket[s].append(float(ret))
        out = []
        for sig, vals in bucket.items():
            n = len(vals)
            if n == 0:
                continue
            avg = sum(vals) / n
            win = sum(1 for v in vals if v > 0) / n * 100
            out.append({"signal": sig, "n": n, "avg": avg, "win": win})
        out.sort(key=lambda x: x["avg"], reverse=True)
        return out

    def ai_score_calibration(self, days: int = 60, period: int = 5):
        with self._conn() as c:
            rows = c.execute(
                """SELECT p.ai_score, r.ret_pct
                   FROM picks p JOIN pick_returns r
                   ON p.run_date=r.run_date AND p.run_slot=r.run_slot AND p.code=r.code
                   WHERE p.is_pushed=1 AND r.period=?
                     AND p.run_date >= date('now', ?)""",
                (period, f'-{days} days'),
            ).fetchall()
        from collections import defaultdict
        bucket = defaultdict(list)
        for score, ret in rows:
            score = int(score or 0)
            if score >= 90:
                key = "90+"
            elif score >= 80:
                key = "80-89"
            elif score >= 70:
                key = "70-79"
            elif score >= 60:
                key = "60-69"
            else:
                key = "<60"
            bucket[key].append(float(ret))
        order = ["90+", "80-89", "70-79", "60-69", "<60"]
        out = []
        for k in order:
            vals = bucket.get(k, [])
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            win = sum(1 for v in vals if v > 0) / len(vals) * 100
            out.append({"bucket": k, "n": len(vals), "avg": avg, "win": win})
        return out

    def list_reports(self, limit: int = 200):
        """供 index.html 使用：列出所有 (run_date, run_slot, total, focus_count)。"""
        with self._conn() as c:
            rows = c.execute(
                """SELECT run_date, run_slot, total, candidates, focus_count, vetoed
                   FROM runs ORDER BY run_date DESC,
                   CASE run_slot WHEN 'close' THEN 1 ELSE 0 END DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {"run_date": r[0], "run_slot": r[1], "total": r[2],
             "candidates": r[3], "focus_count": r[4], "vetoed": r[5]}
            for r in rows
        ]
