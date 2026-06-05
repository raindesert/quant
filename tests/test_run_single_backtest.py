"""P-? _run_single_backtest 单元测试。

- 17 元组 (老格式) 兼容
- 18 元组 params 字典真传给 strategy
- params 非法键 fallback
- params={} 走默认

不 import 整个 main.py (会拖入 matplotlib 等重依赖)；
改为 stub 重 sys.modules 让 main 的 import 跳过重链, 再 import 函数本身。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def _install_stub_modules():
    """用轻量 stub 替代 main.py 顶部 import 的重依赖, 让 main 模块本身能加载。"""
    # 先尝试直接 import; 若失败再 stub
    try:
        import main  # noqa: F401
        return
    except Exception:
        pass

    # matplotlib / numpy / baostock / yaml 等 — 用 SimpleNamespace 占位
    stubs = {
        "matplotlib": ModuleType("matplotlib"),
        "matplotlib.pyplot": ModuleType("matplotlib.pyplot"),
        "matplotlib.dates": ModuleType("matplotlib.dates"),
        "matplotlib.font_manager": ModuleType("matplotlib.font_manager"),
        "matplotlib.ticker": ModuleType("matplotlib.ticker"),
        "matplotlib.gridspec": ModuleType("matplotlib.gridspec"),
        "numpy": ModuleType("numpy"),
        "yaml": ModuleType("yaml"),
        "baostock": ModuleType("baostock"),
        "pandas": ModuleType("pandas"),
        "requests": ModuleType("requests"),
    }
    for name, mod in stubs.items():
        mod.__dict__.setdefault("__path__", [])
        sys.modules.setdefault(name, mod)
    # 强制让 main 用 stub
    for name, mod in stubs.items():
        sys.modules[name] = mod


_install_stub_modules()

# 现在可以 import main
import main  # noqa: E402


class _RecordingStrategy:
    """记录被调用时的 kwargs, 用来断言 params 真的传进来。

    只接受 ALLOWED_KEYS 中的参数 — 模拟真实 SMAStrategy 等固定签名策略。
    传非法键抛 TypeError (与真实策略类一致)。
    """
    ALLOWED_KEYS = {"fast_period", "slow_period", "signal_period", "period", "std_dev", "lookback"}
    instances = []

    def __init__(self, **kwargs):
        bad = set(kwargs) - self.ALLOWED_KEYS
        if bad:
            raise TypeError(f"_RecordingStrategy got unexpected keyword args: {bad}")
        type(self).instances.append(kwargs)
        self.kwargs = kwargs


class _StubEngine:
    """替代 BacktestEngine, 不联网, 返一个固定 summary。"""
    def __init__(self, **kw):
        self.kw = kw

    def run(self, strategy, symbol, days=250, start_date=None, end_date=None, frequency="day"):
        return {"profit_pct": 1.0, "trades_list": [], "symbol": symbol, "frequency": frequency}


def _args_17():
    """17 元组老格式。"""
    return ("sma", "000001.SZ", 60, 100000.0, 0.0003, 0.0, 0.0, 1.0,
            None, None, False, 0.0, "percent", False, True, None, "day")


def _args_18(params):
    """18 元组新格式。"""
    return _args_17() + (params,)


class TestRunSingleBacktest(unittest.TestCase):
    """_run_single_backtest args 解析 + params 派发。"""

    def setUp(self):
        _RecordingStrategy.instances = []
        # patch main 内部的 get_strategy_class / BacktestEngine
        self._orig_get_cls = main.get_strategy_class
        self._orig_engine = main.BacktestEngine
        main.get_strategy_class = lambda name: _RecordingStrategy if name == "sma" else None
        main.BacktestEngine = _StubEngine

    def tearDown(self):
        main.get_strategy_class = self._orig_get_cls
        main.BacktestEngine = self._orig_engine

    def test_17_tuple_compat_no_params(self):
        """老 17 元组 (无 params) 应当被接受, 用默认 strategy 构造。"""
        result = main._run_single_backtest(_args_17())
        self.assertIsNotNone(result)
        # 一次构造, kwargs 空
        self.assertEqual(len(_RecordingStrategy.instances), 1)
        self.assertEqual(_RecordingStrategy.instances[0], {})

    def test_18_tuple_empty_params_uses_default(self):
        """18 元组 params={} → 走默认构造 (空 kwargs)。"""
        result = main._run_single_backtest(_args_18({}))
        self.assertIsNotNone(result)
        self.assertEqual(_RecordingStrategy.instances[-1], {})

    def test_18_tuple_params_forwarded(self):
        """18 元组 params 真传给 strategy_cls(**params)。"""
        result = main._run_single_backtest(_args_18({"fast_period": 5, "slow_period": 20}))
        self.assertIsNotNone(result)
        self.assertEqual(_RecordingStrategy.instances[-1],
                         {"fast_period": 5, "slow_period": 20})

    def test_18_tuple_invalid_key_falls_back(self):
        """非法 params 键 (策略不接受) 触发 TypeError, 自动 fallback 默认。"""
        result = main._run_single_backtest(_args_18({"nonexistent_param": 99}))
        self.assertIsNotNone(result)
        # 第一次 __init__ 抛 TypeError 没 append, 第二次 (fallback) 默认成功
        # 所以 instances 只有 1 条, 应当 kwargs 空
        self.assertEqual(len(_RecordingStrategy.instances), 1)
        self.assertEqual(_RecordingStrategy.instances[-1], {})

    def test_result_strategy_field_set(self):
        """返回值 summary 应当有 strategy 字段。"""
        result = main._run_single_backtest(_args_17())
        assert result is not None
        self.assertEqual(result["strategy"], "sma")
        self.assertEqual(result["symbol"], "000001.SZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
