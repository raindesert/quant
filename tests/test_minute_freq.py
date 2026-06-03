"""P1-1 分钟级回测 — 零外部依赖测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


def _is_real_lib_available(name):
    mod = sys.modules.get(name)
    if mod is None:
        return False
    if getattr(mod, "__file__", None) is None and not getattr(mod, "__loader__", None):
        return False
    return True


# stub（仅无依赖环境）
if not _is_real_lib_available("pandas"):
    import types
    _pd = types.ModuleType("pandas")

    class _DataFrame:
        empty = True
        columns = []
        def copy(self): return self
        def itertuples(self, *a, **k): return []
        def __bool__(self): return False
    _pd.DataFrame = _DataFrame
    class _Series:
        def __init__(self, *a, **k): pass
    _pd.Series = _Series
    class _Timestamp:
        def __init__(self, *a, **k): pass
    _pd.Timestamp = _Timestamp
    _pd.bdate_range = lambda *a, **k: []
    _pd.read_sql_query = lambda *a, **k: _DataFrame()
    _pd.to_datetime = lambda x: x
    _pd.to_numeric = lambda x, **k: x
    class _Timedelta:
        def __init__(self, *a, **k): pass
    _pd.Timedelta = _Timedelta
    sys.modules["pandas"] = _pd

if not _is_real_lib_available("baostock"):
    import types
    _bs = types.ModuleType("baostock")
    _bs.login = lambda: None
    _bs.logout = lambda: None
    _bs.query_history_k_data_plus = lambda *a, **k: None
    sys.modules["baostock"] = _bs

if not _is_real_lib_available("numpy"):
    import types
    _np = types.ModuleType("numpy")
    _np.where = lambda *a, **k: []
    _np.nan = float("nan")
    _np.random = types.SimpleNamespace(
        seed=lambda *a, **k: None,
        normal=lambda *a, **k: [],
        uniform=lambda *a, **k: [],
        randint=lambda *a, **k: [],
    )
    sys.modules["numpy"] = _np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestFrequencySupport(unittest.TestCase):
    """验证 DataFetcher 频率枚举和参数校验。"""

    def test_supported_frequencies_constant(self):
        from data.fetcher import DataFetcher
        self.assertEqual(
            DataFetcher.SUPPORTED_FREQUENCIES,
            ("day", "m1", "m5", "m15", "m30", "m60"),
        )

    def test_invalid_frequency_rejected(self):
        from data.fetcher import DataFetcher
        f = DataFetcher.__new__(DataFetcher)
        with self.assertRaises(ValueError) as ctx:
            f.get_history("000001.SZ", days=10, frequency="h1")
        self.assertIn("不支持的频率", str(ctx.exception))

    def test_day_frequency_accepted(self):
        from data.fetcher import DataFetcher
        f = DataFetcher.__new__(DataFetcher)
        try:
            # 不实际联网，只验证参数校验通过
            f.get_history("000001.SZ", days=10, frequency="day")
        except (RuntimeError, Exception) as e:
            # 网络/数据源失败 OK，但不应是参数错误
            self.assertNotIn("不支持的频率", str(e))

    def test_minute_frequency_accepted(self):
        from data.fetcher import DataFetcher
        f = DataFetcher.__new__(DataFetcher)
        try:
            f.get_history("000001.SZ", days=5, frequency="m5")
        except (RuntimeError, Exception) as e:
            self.assertNotIn("不支持的频率", str(e))


class TestBacktestEngineFrequency(unittest.TestCase):
    """验证 BacktestEngine.run 接受 frequency 参数 + 自动禁用 T+1/涨跌停。"""

    def test_run_signature_has_frequency(self):
        # 不直接 import backtest.engine（会触发 matplotlib 链），
        # 用 ast 解析源码验证签名。
        import ast
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "backtest" / "engine.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run":
                args = [a.arg for a in node.args.args]
                self.assertIn("frequency", args)
                # 检查默认值
                defaults = node.args.defaults
                n_args_no_default = len(args) - len(defaults)
                idx = args.index("frequency") - n_args_no_default
                self.assertIsNotNone(idx, "frequency 必须有默认值")
                self.assertEqual(
                    ast.literal_eval(defaults[idx]), "day"
                )
                return
        self.fail("没找到 BacktestEngine.run 方法")

    def test_run_with_data_signature_has_t1_override(self):
        """分钟级回测需要临时禁用 T+1/涨跌停。"""
        import ast
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "backtest" / "engine.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_run_with_data":
                args = [a.arg for a in node.args.args]
                self.assertIn("enforce_t1", args)
                self.assertIn("check_limit", args)
                return
        self.fail("没找到 BacktestEngine._run_with_data 方法")


if __name__ == "__main__":
    unittest.main(verbosity=2)
