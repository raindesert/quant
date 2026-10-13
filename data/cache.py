"""行情数据本地持久化模块 — SQLite缓存历史数据，支持增量更新。

v2 重构：原实现按 symbol 分表，导致 1000+ 只股票时 sqlite_master 膨胀；
现统一单表 kdata(symbol, date, ...) + UNIQUE(symbol, date) 约束。
外部 API（save/load/get_last_date）保持不变。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

logger = logging.getLogger("quant")

# 兼容旧 API：旧实现按 symbol 分表时用 _SAFE_TABLE_RE 验证表名。
# v2 单表版虽然不再需要，但保留给潜在的旧调用方。
_SAFE_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 单表 schema（v2）
_TABLE_NAME = "kdata"
_DDL = """
CREATE TABLE IF NOT EXISTS kdata (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    close  REAL,
    high   REAL,
    low    REAL,
    volume REAL,
    amount REAL,
    turnover REAL,
    PRIMARY KEY (symbol, date)
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_kdata_symbol ON kdata(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_kdata_date ON kdata(date)",
]

# 复权基元数据表：前复权(qfq)序列每次分红送转都会重定基，
# 若把不同基的数据追加进同一条序列，除权日会出现虚假跳空。
# 这里记录每只股票当前数据的复权基日期，基变化时整表重建该 symbol。
_META_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""


class DataCache:
    """SQLite本地数据缓存（v2 单表版）。

    单表 kdata + UNIQUE(symbol, date) 约束保证：
    - 1000+ 股票不会撑爆 sqlite_master
    - INSERT OR IGNORE 替代旧的"先 SELECT date 再 to_sql append"两段操作
    - list/clear/expire 跨股票操作只读一张表
    """

    def __init__(self, db_dir: str | Path | None = None):
        if db_dir is None:
            # 可用环境变量 QUANT_CACHE_DIR 覆盖（便于测试/便携部署）
            db_dir = os.environ.get("QUANT_CACHE_DIR") or (
                Path.home() / ".quant" / "cache"
            )
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
        self._legacy_tables_renamed: bool = False

    def _get_conn(self) -> sqlite3.Connection | None:
        if self._available is False:
            return None
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(str(self.db_path))
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(_DDL)
                self._conn.executescript(_META_DDL)
                for idx_sql in _INDEXES:
                    self._conn.execute(idx_sql)
                self._conn.commit()
                self._available = True
            except (sqlite3.OperationalError, OSError) as exc:
                logger.warning("SQLite缓存不可用: %s", exc)
                self._conn = None
                self._available = False
                return None
        return self._conn

    def _table_name(self, symbol: str) -> str:
        """兼容旧 API 的 _table_name 入口（v2 返回单表名）。"""
        return _TABLE_NAME

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        """兼容旧 API。"""
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cursor.fetchone() is not None

    def get_adjust_base(self, symbol: str) -> str | None:
        """读取该 symbol 缓存数据的复权基日期（未记录返回 None）。"""
        conn = self._get_conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (f"adjust_base:{symbol}",)
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def _set_adjust_base(self, symbol: str, base: str | None) -> None:
        conn = self._get_conn()
        if conn is None or not base:
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"adjust_base:{symbol}", base),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.debug("写入复权基失败 %s: %s", symbol, exc)

    def _purge_symbol(self, symbol: str) -> None:
        """删除该 symbol 的全部缓存行（复权基变化时调用）。"""
        conn = self._get_conn()
        if conn is None:
            return
        try:
            conn.execute(f"DELETE FROM {_TABLE_NAME} WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM meta WHERE key = ?", (f"adjust_base:{symbol}",))
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("清理 %s 旧复权缓存失败: %s", symbol, exc)

    def save(self, symbol: str, df: pd.DataFrame, adjust_base: str | None = None):
        """写入行情。adjust_base 为前复权基准日期。

        与已缓存的基不一致时，先清空该 symbol 的旧行再写入，
        避免把不同复权基的数据永久拼接成一条含虚假跳空的序列。
        """
        conn = self._get_conn()
        if conn is None or df.empty:
            return
        df = df.copy()

        if adjust_base:
            prev_base = self.get_adjust_base(symbol)
            if prev_base and prev_base != adjust_base:
                logger.info(
                    "%s 复权基变化 %s -> %s，清空旧缓存后重写", symbol, prev_base, adjust_base
                )
                self._purge_symbol(symbol)

        try:
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        except AttributeError:
            # 上游可能传入已是字符串的日期列，不应让整次取数失败
            logger.debug("%s 日期列非 datetime 类型，跳过 strftime", symbol)
            df["date"] = df["date"].astype(str)

        df["symbol"] = symbol

        cols = [c for c in df.columns if c in (
            "symbol", "date", "open", "close", "high", "low",
            "volume", "amount", "turnover",
        )]
        rows = df[cols].itertuples(index=False, name=None)
        try:
            placeholders = ",".join(["?"] * len(cols))
            col_list = ",".join(cols)
            sql = (
                f"INSERT OR IGNORE INTO {_TABLE_NAME} ({col_list}) "
                f"VALUES ({placeholders})"
            )
            conn.executemany(sql, list(rows))
            conn.commit()
            self._set_adjust_base(symbol, adjust_base)
            logger.debug("缓存 %s: 尝试写入 %d 条", symbol, len(df))
        except Exception as exc:
            logger.warning("缓存写入失败 %s: %s", symbol, exc)

    def load(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        conn = self._get_conn()
        if conn is None:
            return pd.DataFrame()
        conditions = ["symbol = ?"]
        params: list[str] = [symbol]
        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)
        where = " AND ".join(conditions)
        query = f"SELECT date, open, close, high, low, volume, amount, turnover FROM {_TABLE_NAME} WHERE {where} ORDER BY date"

        try:
            df = pd.read_sql_query(query, conn, params=params)
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
        """返回该 symbol 的最大 date 字符串（YYYY-MM-DD）。"""
        conn = self._get_conn()
        if conn is None:
            return None
        try:
            cursor = conn.execute(
                f"SELECT MAX(date) FROM {_TABLE_NAME} WHERE symbol = ?",
                (symbol,),
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def _get_existing_dates(self, conn: sqlite3.Connection, table: str) -> set[str]:
        """兼容旧 API。返回指定 symbol 的所有已有 date。"""
        try:
            # table 参数现在忽略（v2 总是 _TABLE_NAME）
            symbol = table
            cursor = conn.execute(
                f"SELECT date FROM {_TABLE_NAME} WHERE symbol = ?", (symbol,)
            )
            return {row[0] for row in cursor.fetchall()}
        except Exception:
            return set()

    def list_symbols(self) -> list[str]:
        """v2 新增：列出所有已缓存的 symbol。"""
        conn = self._get_conn()
        if conn is None:
            return []
        try:
            cursor = conn.execute(
                f"SELECT DISTINCT symbol FROM {_TABLE_NAME} ORDER BY symbol"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception:
            return []

    def clear(self, symbol: str | None = None) -> int:
        """v2 新增：清空缓存。返回删除行数。"""
        conn = self._get_conn()
        if conn is None:
            return 0
        try:
            if symbol:
                cur = conn.execute(
                    f"DELETE FROM {_TABLE_NAME} WHERE symbol = ?", (symbol,)
                )
            else:
                cur = conn.execute(f"DELETE FROM {_TABLE_NAME}")
            conn.commit()
            return cur.rowcount
        except Exception:
            return 0

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
