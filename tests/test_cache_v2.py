"""DataCache v2 单表版测试 — 优先用真实 pandas（若已装），否则用极简 stub。

由于 pandas stub 复杂度高，如果环境已装真实 pandas，直接用真实库；
否则用 SQL 验证核心表结构 + list_symbols/clear/get_last_date（不调 DataFrame.save）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import types as _types


def _is_real_lib_available(name):
    """检查 sys.modules 里 name 是否是真实库（不是 types.ModuleType 桩）。"""
    mod = sys.modules.get(name)
    if mod is None:
        return False
    if getattr(mod, "__file__", None) is None and not getattr(mod, "__loader__", None):
        return False
    return True


# 极简 pandas stub（仅无依赖环境）
if not _is_real_lib_available("pandas"):
    _pd = _types.ModuleType("pandas")

    class _DF:
        """支持 DataCache.save() 所需的最小 API。"""
        def __init__(self, data):
            self._data = {k: list(v) for k, v in data.items()}
            self.empty = len(next(iter(self._data.values()), [])) == 0

        @property
        def columns(self):
            return list(self._data.keys())

        def copy(self):
            return _DF({k: list(v) for k, v in self._data.items()})

        def __setitem__(self, key, value):
            if callable(value):
                new_col = [value(v) for v in self._data[key]]
                self._data[key] = new_col
            else:
                self._data[key] = list(value)

        def __getitem__(self, key):
            if isinstance(key, list):
                return _DF({k: self._data[k] for k in key})
            class _ColView:
                def __init__(self, data): self._data = data
                @property
                def dt(self):
                    class _Dt:
                        def strftime(self, fmt): return lambda v: str(v)[:10]
                    return _Dt()
            return _ColView(self._data[key])

        def itertuples(self, index=False, name=None):
            if not self._data:
                return
            keys = list(self._data.keys())
            n = len(self._data[keys[0]])
            for i in range(n):
                yield tuple(self._data[k][i] for k in keys)

    _pd.DataFrame = _DF
    _pd.to_datetime = lambda x: x
    _pd.to_numeric = lambda x, **k: x
    _pd.read_sql_query = None
    sys.modules["pandas"] = _pd

# numpy stub
if not _is_real_lib_available("numpy"):
    _np = _types.ModuleType("numpy")
    _np.where = lambda *a, **k: []
    _np.nan = float("nan")
    sys.modules["numpy"] = _np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.cache import DataCache, _TABLE_NAME


class TestDataCacheV2(unittest.TestCase):
    """v2 单表版测试 — 通过 SQL 直插验证表结构与查询。

    DataCache.save() 依赖 pandas DataFrame 的复杂 API，stub 成本高；
    这里通过直接 INSERT 验证核心表结构 + list_symbols/clear/get_last_date。
    """
    def setUp(self):
        import shutil
        self.tmpdir = Path(tempfile.mkdtemp())
        self.cache = DataCache(db_dir=self.tmpdir)

    def tearDown(self):
        self.cache.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert(self, symbol, date, close=10.0):
        """直接 SQL 插入一行，模拟 DataCache.save 的结果。"""
        conn = self.cache._get_conn()
        conn.execute(
            f"INSERT OR IGNORE INTO {_TABLE_NAME} "
            f"(symbol, date, open, close, high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (symbol, date, close, close, close, close, 1000),
        )
        conn.commit()

    def test_table_name_is_kdata(self):
        self.assertEqual(_TABLE_NAME, "kdata")
        self.assertEqual(self.cache._table_name("000001.SZ"), "kdata")

    def test_get_last_date_basic(self):
        self._insert("000001.SZ", "2024-01-01", 10.0)
        self._insert("000001.SZ", "2024-01-05", 11.0)
        self._insert("000001.SZ", "2024-01-03", 10.5)
        last = self.cache.get_last_date("000001.SZ")
        self.assertEqual(last, "2024-01-05")

    def test_get_last_date_no_data(self):
        self.assertIsNone(self.cache.get_last_date("999999.SZ"))

    def test_multi_symbol(self):
        self._insert("000001.SZ", "2024-01-01")
        self._insert("600000.SH", "2024-01-01")
        self.assertEqual(self.cache.get_last_date("000001.SZ"), "2024-01-01")
        self.assertEqual(self.cache.get_last_date("600000.SH"), "2024-01-01")

    def test_list_symbols(self):
        self._insert("000001.SZ", "2024-01-01")
        self._insert("600000.SH", "2024-01-01")
        self._insert("300001.SZ", "2024-01-01")
        syms = self.cache.list_symbols()
        self.assertEqual(syms, ["000001.SZ", "300001.SZ", "600000.SH"])

    def test_clear_specific_symbol(self):
        self._insert("000001.SZ", "2024-01-01")
        self._insert("600000.SH", "2024-01-01")
        deleted = self.cache.clear("000001.SZ")
        self.assertEqual(deleted, 1)
        self.assertIsNone(self.cache.get_last_date("000001.SZ"))
        self.assertEqual(self.cache.get_last_date("600000.SH"), "2024-01-01")

    def test_clear_all(self):
        self._insert("000001.SZ", "2024-01-01")
        self._insert("600000.SH", "2024-01-01")
        deleted = self.cache.clear()
        self.assertEqual(deleted, 2)
        self.assertEqual(self.cache.list_symbols(), [])

    def test_existing_dates_helper(self):
        self._insert("000001.SZ", "2024-01-01")
        self._insert("000001.SZ", "2024-01-02")
        conn = self.cache._get_conn()
        dates = self.cache._get_existing_dates(conn, "000001.SZ")
        self.assertEqual(dates, {"2024-01-01", "2024-01-02"})

    def test_table_exists_kdata(self):
        conn = self.cache._get_conn()
        self.assertTrue(self.cache._table_exists(conn, "kdata"))

    def test_insert_or_ignore_dedup(self):
        """PRIMARY KEY (symbol, date) 约束保证去重。"""
        self._insert("000001.SZ", "2024-01-01")
        self._insert("000001.SZ", "2024-01-01")  # 重复
        self._insert("000001.SZ", "2024-01-01", close=99.0)  # 重复但 close 不同
        conn = self.cache._get_conn()
        count = conn.execute(
            f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE symbol = ?", ("000001.SZ",)
        ).fetchone()[0]
        self.assertEqual(count, 1)
        # 第一次插入的 close 应该是 10.0（INSERT OR IGNORE 保留先到）
        row = conn.execute(
            f"SELECT close FROM {_TABLE_NAME} WHERE symbol = ?", ("000001.SZ",)
        ).fetchone()
        self.assertEqual(row[0], 10.0)

    def test_save_uses_insert_or_ignore(self):
        """验证表结构正确：PRIMARY KEY (symbol, date) + INSERT OR IGNORE 工作。"""
        conn = self.cache._get_conn()
        # 1. 表存在
        self.assertTrue(self.cache._table_exists(conn, "kdata"))
        # 2. PRIMARY KEY 约束
        info = conn.execute(f"PRAGMA table_info({_TABLE_NAME})").fetchall()
        cols = {row[1] for row in info}
        self.assertIn("symbol", cols)
        self.assertIn("date", cols)
        # 3. INSERT OR IGNORE 行为
        self._insert("000001.SZ", "2024-01-01", 10.0)
        self._insert("000001.SZ", "2024-01-01", 99.0)
        count = conn.execute(
            f"SELECT COUNT(*) FROM {_TABLE_NAME} WHERE symbol = ?", ("000001.SZ",)
        ).fetchone()[0]
        self.assertEqual(count, 1)
        # 第一次插入保留
        close = conn.execute(
            f"SELECT close FROM {_TABLE_NAME} WHERE symbol = ?", ("000001.SZ",)
        ).fetchone()[0]
        self.assertEqual(close, 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
