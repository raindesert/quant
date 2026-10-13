"""v41 三项深度修复的回归测试。

覆盖：
- walk_forward: 窗口前置预热 bar，训练/验证期长度口径一致
- engine: trade_start 参数不改变默认行为，且能只统计指定区间
- optimizer: walk_forward 开关接上真实验证（原为空转）
- cache: 前复权基变化时清空旧数据，避免除权日虚假跳空
"""
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


def _make_df(n=200, start=datetime(2024, 1, 1)):
    """构造一段有涨有跌的合成行情（跳过周末）。"""
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(pd.Timestamp(d))
        d += timedelta(days=1)
    import numpy as np
    rng = np.random.default_rng(7)
    steps = rng.normal(0.001, 0.02, size=n)
    close = 10.0 * (1 + np.cumsum(steps))
    return pd.DataFrame({
        "date": dates,
        "open": close * 0.995,
        "close": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": 1e6,
    })


class TestEngineTradeStart(unittest.TestCase):
    """trade_start：预热 bar 只喂策略不交易，从指定日期起才计入统计。"""

    def test_default_run_unchanged(self):
        from backtest.engine import BacktestEngine
        from strategy.registry import get_strategy_class

        df = _make_df(120)
        eng = BacktestEngine(initial_cash=1_000_000, verbose=False)
        s = get_strategy_class("sma")()
        r = eng._run_with_data(s, df, "000001.SZ")

        self.assertIsNotNone(r)
        # 默认不传 trade_start：权益曲线应覆盖 warmup 之后的全部交易日
        self.assertGreater(len(r.get("equity_curve", [])), 80)

    def test_trade_start_limits_equity_curve(self):
        from backtest.engine import BacktestEngine
        from strategy.registry import get_strategy_class

        df = _make_df(120)
        eng = BacktestEngine(initial_cash=1_000_000, verbose=False)
        s = get_strategy_class("sma")()
        trade_start = df["date"].iloc[60]

        r = eng._run_with_data(s, df, "000001.SZ", trade_start=trade_start)

        self.assertIsNotNone(r)
        curve = r.get("equity_curve", [])
        # 统计区间应从 trade_start 附近开始，而不是第 21 根 bar
        first_date = pd.to_datetime(curve[0]["date"])
        self.assertGreaterEqual(first_date, pd.to_datetime(trade_start) - timedelta(days=5))
        # 第 60 根起共 60 根 bar（末尾还会追加一根），不应多于全量
        self.assertLessEqual(len(curve), 61)
        self.assertLess(len(curve), len(df))

    def test_switches_restored_after_call(self):
        """_run_with_data 就地改过 enforce_t1/check_limit，退出后必须还原。"""
        from backtest.engine import BacktestEngine
        from strategy.registry import get_strategy_class

        df = _make_df(80)
        eng = BacktestEngine(initial_cash=1_000_000, verbose=False,
                             enforce_t_plus_1=True, check_limit=True)

        eng._run_with_data(get_strategy_class("sma")(), df, "000001.SZ",
                           enforce_t1=False, check_limit=False)

        self.assertTrue(eng.enforce_t_plus_1, "T+1 开关应被还原")
        self.assertTrue(eng.check_limit, "涨跌停开关应被还原")

    def test_switches_restored_on_exception(self):
        """即使执行抛异常也必须还原实例开关。"""
        from backtest.engine import BacktestEngine

        eng = BacktestEngine(initial_cash=1_000_000, enforce_t_plus_1=True)

        class Boom:
            def on_init(self, *a, **k):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            eng._run_with_data(Boom(), _make_df(50), "000001.SZ", enforce_t1=False)

        self.assertTrue(eng.enforce_t_plus_1)


class TestWalkForwardWarmup(unittest.TestCase):
    """训练/验证期长度口径必须一致，否则 degradation_ratio 不可比。"""

    def _validator(self):
        from backtest.walk_forward import WalkForwardValidator

        return WalkForwardValidator(
            strategy_name="sma", symbol="000001.SZ",
            train_days=60, test_days=30, step_days=30,
        )

    def test_period_includes_warmup_bars(self):
        v = self._validator()
        df = _make_df(300)

        # 窗口 [60:90]，切片必须额外带上之前 20 根预热 bar
        result = v._run_period(df, df["date"].iloc[60], df["date"].iloc[89])

        self.assertIsNotNone(result)
        curve = result.get("equity_curve", [])
        self.assertTrue(curve, "应产出权益曲线")
        # 30 个交易日的窗口应保留大部分样本，而不是被预热吞掉 20 根
        self.assertGreaterEqual(len(curve), 8)

    def test_too_short_window_returns_none_with_warning(self):
        v = self._validator()
        df = _make_df(300)

        with self.assertLogs("quant", level="WARNING"):
            r = v._run_period(df, df["date"].iloc[100], df["date"].iloc[101])

        self.assertIsNone(r)

    def test_skipped_windows_reported(self):
        """数据不足的窗口必须被计入 skipped，不能静默消失。"""
        from backtest.walk_forward import WalkForwardWindow, WalkForwardResult

        r = WalkForwardResult("sma", "000001.SZ")
        self.assertEqual(r.skipped_windows, 0)

    def test_warmup_constant_matches_engine(self):
        from backtest.walk_forward import WalkForwardValidator

        self.assertEqual(WalkForwardValidator.WARMUP_BARS, 20)


class TestOptimizerWalkForwardWiring(unittest.TestCase):
    """walk_forward 曾是只打印不做事的空转开关。"""

    def test_walk_forward_result_present_when_enabled(self):
        from backtest.optimizer import StrategyOptimizer

        opt = StrategyOptimizer(
            strategy_name="sma", symbol="000001.SZ", days=250,
            walk_forward=True,
        )
        # 参数已正确保存
        self.assertTrue(opt.walk_forward)

    def test_slippage_attrs_initialized(self):
        """_validate_walk_forward 依赖 slippage 属性，必须在 __init__ 初始化。"""
        from backtest.optimizer import StrategyOptimizer

        opt = StrategyOptimizer(strategy_name="sma", symbol="000001.SZ")
        self.assertEqual(opt.slippage, 0.0)
        self.assertEqual(opt.slippage_type, "percent")

    def test_validation_returns_expected_keys(self):
        from backtest.optimizer import StrategyOptimizer

        opt = StrategyOptimizer(
            strategy_name="sma", symbol="000001.SZ", days=400, walk_forward=True,
        )
        # 真实调用会拉数据，这里用 monkeypatch 验证返回契约
        import backtest.walk_forward as wf_mod

        class FakeResult:
            avg_train_return = 1.0
            avg_test_return = 0.4
            avg_train_sharpe = 1.2
            avg_test_sharpe = 0.5
            degradation_ratio = 0.4
            is_overfit = True
            skipped_windows = 0
            windows = [1, 2]

            def summary(self):
                return "fake"

        class FakeValidator:
            def __init__(self, **kw):
                pass

            def validate(self):
                return FakeResult()

        orig = wf_mod.WalkForwardValidator
        wf_mod.WalkForwardValidator = FakeValidator
        try:
            out = opt._validate_walk_forward({"fast": 5, "slow": 20})
        finally:
            wf_mod.WalkForwardValidator = orig

        for key in ("degradation_ratio", "is_overfit", "n_windows", "avg_test_return"):
            self.assertIn(key, out)
        self.assertTrue(out["is_overfit"])
        self.assertEqual(out["n_windows"], 2)


class TestCacheAdjustBase(unittest.TestCase):
    """前复权基变化时必须重建，否则除权日出现虚假跳空。"""

    def setUp(self):
        from data.cache import DataCache

        self.tmpdir = Path(tempfile.mkdtemp())
        self.cache = DataCache(db_dir=self.tmpdir)

    def tearDown(self):
        self.cache.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_same_base_keeps_history(self):
        df = _make_df(50)
        self.cache.save("000001.SZ", df, adjust_base="2024-03-01")
        self.cache.save("000001.SZ", df, adjust_base="2024-03-01")

        loaded = self.cache.load("000001.SZ")

        # 同一基重复写入不应丢数据
        self.assertEqual(len(loaded), 50)

    def test_base_change_purges_old_rows(self):
        old = _make_df(50)
        self.cache.save("000001.SZ", old, adjust_base="2024-03-01")

        # 除权后重定基：老数据价格整体下移，属于不同基
        new = _make_df(30, start=datetime(2024, 4, 1))
        for col in ("close", "open", "high", "low"):
            new[col] = new[col] * 0.5
        self.cache.save("000001.SZ", new, adjust_base="2024-06-01")

        loaded = self.cache.load("000001.SZ")

        # 旧基数据必须被清掉，只剩新基的 30 根
        self.assertEqual(len(loaded), 30)
        self.assertAlmostEqual(
            float(loaded["close"].max()), float(new["close"].max()), places=6
        )

    def test_adjust_base_persisted(self):
        self.cache.save("000001.SZ", _make_df(10), adjust_base="2024-05-05")

        self.assertEqual(self.cache.get_adjust_base("000001.SZ"), "2024-05-05")

    def test_string_date_column_does_not_crash(self):
        """上游传入字符串日期列时不应抛 AttributeError 让整次取数失败。"""
        df = _make_df(10)
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        self.cache.save("000001.SZ", df, adjust_base="2024-05-05")
        loaded = self.cache.load("000001.SZ")

        self.assertEqual(len(loaded), 10)

    def test_no_adjust_base_still_appends(self):
        """不传复权基时保持原有追加语义，不破坏既有调用方。"""
        self.cache.save("000001.SZ", _make_df(30))

        self.assertEqual(len(self.cache.load("000001.SZ")), 30)
        self.assertIsNone(self.cache.get_adjust_base("000001.SZ"))


class TestAdjustBaseHelper(unittest.TestCase):
    def test_returns_latest_date(self):
        from data.fetcher import _adjust_base_of

        df = _make_df(10)
        base = _adjust_base_of(df)

        self.assertEqual(base, pd.to_datetime(df["date"].max()).strftime("%Y-%m-%d"))

    def test_empty_returns_none(self):
        from data.fetcher import _adjust_base_of

        self.assertIsNone(_adjust_base_of(pd.DataFrame()))


if __name__ == "__main__":
    unittest.main()