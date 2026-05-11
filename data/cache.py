"""行情数据本地持久化模块 — SQLite缓存历史数据，支持增量更新。"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

logger = logging.getLogger("quant")


class DataCache:
    """SQLite本地数据缓存。

    按股票代码分表存储，支持增量更新（只下载缺失日期的数据）。
    当SQLite不可用时自动降级为无缓存模式。
    """

    def __init__(self, db_dir: str | Path | None = None):
        if db_dir is None:
            db_dir = Path.home() / ".quant" / "cache"
        self.db_path = Path(db_dir) / "market.db"
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            fallback = Path(".") / ".quant_cache"
            self.db_path = fallback / "market.db"
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.db_path = Path("market.db")
        self._conn: sqlite3.Connection | None = None
        self._available: bool | None = None

    def _get_conn(self) -> sqlite3.Connection | None:
        if self._available is False:
            return None
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(str(self.db_path))
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._available = True
            except (sqlite3.OperationalError, OSError) as exc:
                logger.warning("SQLite缓存不可用: %s", exc)
                self._conn = None
                self._available = False
                return None
        return self._conn

    def _table_name(self, symbol: str) -> str:
        return symbol.replace(".", "_").replace("-", "_")

    def save(self, symbol: str, df: pd.DataFrame):
        conn = self._get_conn()
        if conn is None or df.empty:
            return
        table = self._table_name(symbol)
        df = df.copy()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        existing = self._get_existing_dates(conn, table)
        new_rows = df[~df["date"].isin(existing)]
        if new_rows.empty:
            return

        cols = [c for c in new_rows.columns if c in (
            "date", "open", "close", "high", "low", "volume", "amount", "turnover"
        )]
        try:
            new_rows[cols].to_sql(table, conn, if_exists="append", index=False)
            logger.debug("缓存 %s: 新增 %d 条记录", symbol, len(new_rows))
        except Exception as exc:
            logger.warning("缓存写入失败 %s: %s", symbol, exc)

    def load(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        table = self._table_name(symbol)

        try:
            cursor = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone() is None:
                return pd.DataFrame()
        except Exception:
            return pd.DataFrame()

        query = f"SELECT * FROM {table} WHERE 1=1"
        if start_date:
            query += f" AND date >= '{start_date}'"
        if end_date:
            query += f" AND date <= '{end_date}'"
        query += " ORDER BY date"

        try:
            df = pd.read_sql_query(query, conn)
        except Exception:
            return pd.DataFrame()

        if df.empty:
            return df

        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "close", "high", "low", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_last_date(self, symbol: str) -> str | None:
        conn = self._get_conn()
        if conn is None:
            return None
        table = self._table_name(symbol)
        try:
            cursor = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone() is None:
                return None
            cursor = conn.execute(f"SELECT MAX(date) FROM {table}")
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def _get_existing_dates(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            cursor = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone() is None:
                return set()
            cursor = conn.execute(f"SELECT date FROM {table}")
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
