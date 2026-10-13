"""Backtest history persistence (SQLite).

Stores the full record list (id, ts, mode, symbol, strategy, profit_pct, sharpe,
drawdown, win_rate, trades, summary, extra) so the Streamlit "回测历史" page
survives page reloads, app restarts, and browser tab closures.

DB lives at data/history.db (created on first import). All operations are
synchronous and thread-safe via sqlite3's default connection-per-call pattern.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo root: history_store.py lives at <repo>/history_store.py, so its parent IS the repo.
_ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("QUANT_HISTORY_DB", _ROOT / "data" / "history.db"))
_DEFAULT_LIMIT = 500  # hard cap on rows retained

_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT,
    profit_pct REAL,
    sharpe_ratio REAL,
    max_drawdown_pct REAL,
    win_rate REAL,
    trades INTEGER,
    summary_json TEXT,
    extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON backtest_runs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_runs_symbol_ts ON backtest_runs(symbol, ts DESC);
"""

_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Idempotent schema bootstrap. Safe to call from any thread on startup."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.executescript(_DDL)
            conn.commit()
            _initialized = True
        finally:
            conn.close()


def _ensure() -> None:
    if not _initialized:
        init_db()


def _row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    rec = {
        "id": row["id"],
        "timestamp": row["ts"],
        "mode": row["mode"],
        "symbol": row["symbol"],
        "strategy": row["strategy"] or "?",
        "profit_pct": row["profit_pct"] or 0.0,
        "sharpe_ratio": row["sharpe_ratio"] or 0.0,
        "max_drawdown_pct": row["max_drawdown_pct"] or 0.0,
        "win_rate": row["win_rate"] or 0.0,
        "trades": row["trades"] or 0,
        "summary": {},
        "extra": {},
    }
    if row["summary_json"]:
        try:
            rec["summary"] = json.loads(row["summary_json"])
        except (json.JSONDecodeError, TypeError):
            rec["summary"] = {}
    if row["extra_json"]:
        try:
            rec["extra"] = json.loads(row["extra_json"])
        except (json.JSONDecodeError, TypeError):
            rec["extra"] = {}
    return rec


def save(mode: str, summary: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> int:
    """Persist a backtest run. Returns the new row id.

    Args:
        mode: backtest / optimize / multi_strategy / walkforward
        summary: the engine summary dict (must contain symbol, strategy, metrics)
        extra: optional sub-strategy list, optimization params, etc.
    """
    _ensure()
    if not isinstance(summary, dict):
        raise TypeError(f"summary must be dict, got {type(summary).__name__}")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO backtest_runs
               (ts, mode, symbol, strategy, profit_pct, sharpe_ratio,
                max_drawdown_pct, win_rate, trades, summary_json, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts,
                str(mode),
                str(summary.get("symbol", "?")),
                str(summary.get("strategy", "?")),
                float(summary.get("profit_pct", 0.0) or 0.0),
                float(summary.get("sharpe_ratio", 0.0) or 0.0),
                float(summary.get("max_drawdown_pct", 0.0) or 0.0),
                float(summary.get("win_rate", 0.0) or 0.0),
                int(summary.get("trades", 0) or 0),
                json.dumps(summary, ensure_ascii=False, default=str),
                json.dumps(extra or {}, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        assert new_id is not None, "INSERT did not return rowid"
    finally:
        conn.close()
    _enforce_cap(_DEFAULT_LIMIT)
    return int(new_id)


def list_recent(limit: int = 200) -> List[Dict[str, Any]]:
    """Return most recent N runs (newest first) as plain dicts."""
    _ensure()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_record(r) for r in rows]


def list_for_symbol(symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Return runs for a single symbol, newest first."""
    _ensure()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_runs WHERE symbol = ? "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (str(symbol), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_record(r) for r in rows]


def get(record_id: int) -> Optional[Dict[str, Any]]:
    _ensure()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?", (int(record_id),)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_record(row) if row else None


def delete(record_id: int) -> bool:
    _ensure()
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM backtest_runs WHERE id = ?", (int(record_id),)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear() -> int:
    """Wipe all rows. Returns count deleted."""
    _ensure()
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM backtest_runs")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def stats() -> Dict[str, Any]:
    """Aggregate stats: total runs, distinct symbols/strategies, best/worst run."""
    _ensure()
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM backtest_runs").fetchone()["n"]
        if total == 0:
            return {"total": 0}
        sym_n = conn.execute(
            "SELECT COUNT(DISTINCT symbol) AS n FROM backtest_runs"
        ).fetchone()["n"]
        strat_n = conn.execute(
            "SELECT COUNT(DISTINCT strategy) AS n FROM backtest_runs"
        ).fetchone()["n"]
        profitable = conn.execute(
            "SELECT COUNT(*) AS n FROM backtest_runs WHERE profit_pct > 0"
        ).fetchone()["n"]
        best_row = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY profit_pct DESC LIMIT 1"
        ).fetchone()
        worst_row = conn.execute(
            "SELECT * FROM backtest_runs ORDER BY profit_pct ASC LIMIT 1"
        ).fetchone()
        avg_row = conn.execute(
            "SELECT AVG(profit_pct) AS a, AVG(sharpe_ratio) AS s, "
            "AVG(max_drawdown_pct) AS d FROM backtest_runs"
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": total,
        "distinct_symbols": sym_n,
        "distinct_strategies": strat_n,
        "profitable": profitable,
        "avg_profit_pct": avg_row["a"] or 0.0,
        "avg_sharpe": avg_row["s"] or 0.0,
        "avg_drawdown_pct": avg_row["d"] or 0.0,
        "best": _row_to_record(best_row) if best_row else None,
        "worst": _row_to_record(worst_row) if worst_row else None,
    }


def _enforce_cap(limit: int) -> None:
    """Keep only the most recent `limit` rows (FIFO)."""
    conn = _connect()
    try:
        # Cheap on indexed `ts`: delete by id NOT IN (top N by ts desc, id desc).
        conn.execute(
            "DELETE FROM backtest_runs WHERE id NOT IN ("
            "  SELECT id FROM backtest_runs ORDER BY ts DESC, id DESC LIMIT ?"
            ")",
            (int(limit),),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "DB_PATH",
    "init_db",
    "save",
    "list_recent",
    "list_for_symbol",
    "get",
    "delete",
    "clear",
    "stats",
]
