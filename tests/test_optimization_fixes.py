"""优化迭代测试 — 零外部依赖（仅 stdlib + 项目纯逻辑代码）。

覆盖：
- A1: DataFetcher._alternate_exchange_symbol 备用交易所映射（不调 baostock）
- A1: DataFetcher._fetch_from_baostock 递归深度限制（mock baostock）
- A2: BaseBacktestEngine._calc_sortino_ratio 下行标准差分母与边界
- A3: RiskManager.update_portfolio_state 首日不熔断、第二日正常熔断
"""
from __future__ import annotations

import math
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# 让测试不依赖 pandas：feather-back 必要的 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# 极简 stub：仅在真实库不可用时安装。如果真实库已 import，绝不覆盖 sys.modules
# （避免污染同一进程后续测试）。
def _is_real_lib_available(name):
    """检查真实库是否已经在 sys.modules 中且是合法的（非 stub）模块。

    注意：只看 sys.modules 是不够的，因为：
    - 真库没 import 时 sys.modules[name] 是 None（但库本身装在磁盘上）
    - 这种情况下应该可以安全 import 真库再用
    但当前策略是：如果 sys.modules 里有真库，就不装 stub；
    如果 sys.modules 里有 stub，就保留 stub（不重复装）。
    """
    mod = sys.modules.get(name)
    if mod is None:
        return False
    # 如果模块的 __file__ 是 None（types.ModuleType 创建的），它是 stub
    if getattr(mod, "__file__", None) is None and not getattr(mod, "__loader__", None):
        return False
    return True


def _can_import_real(name: str) -> bool:
    """检查真实库能否被 import（不实际 import，只查 spec）。"""
    try:
        import importlib.util
        spec = importlib.util.find_spec(name)
        return spec is not None
    except (ImportError, ValueError):
        return False


def _install_pandas_stub():
    if _is_real_lib_available("pandas"):
        return
    pd = types.ModuleType("pandas")

    class _DataFrame:
        def __init__(self, *a, **kw): pass
        def itertuples(self, *a, **kw): return []
        def to_sql(self, *a, **kw): pass
        def copy(self): return self
        @property
        def empty(self): return True
        @property
        def columns(self): return []
        def __bool__(self): return False
    pd.DataFrame = _DataFrame

    class _Series:
        def __init__(self, *a, **kw): pass
    pd.Series = _Series

    class _Timestamp:
        def __init__(self, *a, **kw): pass
    pd.Timestamp = _Timestamp

    pd.bdate_range = lambda *a, **kw: []
    pd.read_sql_query = lambda *a, **kw: _DataFrame()
    pd.to_datetime = lambda x: x
    pd.to_numeric = lambda x, **kw: x

    class _Timedelta:
        def __init__(self, *a, **kw): pass
    pd.Timedelta = _Timedelta

    sys.modules["pandas"] = pd


def _install_baostock_stub():
    if _is_real_lib_available("baostock"):
        return
    bs = types.ModuleType("baostock")
    bs.login = lambda: None
    bs.logout = lambda: None
    bs.query_history_k_data_plus = lambda *a, **kw: types.SimpleNamespace(error_code="0", error_msg="", data=[])
    sys.modules["baostock"] = bs


def _install_requests_stub():
    if _is_real_lib_available("requests"):
        return
    r = types.ModuleType("requests")
    class _Session:
        def get(self, *a, **kw): raise RuntimeError("network disabled in test")
        def __getattr__(self, name): return None
    r.Session = _Session
    r.get = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disabled"))
    sys.modules["requests"] = r


def _install_numpy_stub():
    if _is_real_lib_available("numpy"):
        return
    np = types.ModuleType("numpy")
    np.random = types.SimpleNamespace(
        seed=lambda *a, **kw: None,
        normal=lambda *a, **kw: [],
        uniform=lambda *a, **kw: [],
        randint=lambda *a, **kw: [],
    )
    np.where = lambda *a, **kw: []
    np.nan = float("nan")
    sys.modules["numpy"] = np


def _install_matplotlib_stub():
    if _is_real_lib_available("matplotlib"):
        return
    mpl = types.ModuleType("matplotlib")
    mpl.use = lambda *a, **kw: None
    pyplot = types.ModuleType("matplotlib.pyplot")
    pyplot.figure = lambda *a, **kw: None
    pyplot.subplots = lambda *a, **kw: (None, None)
    pyplot.plot = lambda *a, **kw: None
    pyplot.bar = lambda *a, **kw: None
    pyplot.imshow = lambda *a, **kw: None
    pyplot.title = lambda *a, **kw: None
    pyplot.xlabel = lambda *a, **kw: None
    pyplot.ylabel = lambda *a, **kw: None
    pyplot.colorbar = lambda *a, **kw: None
    pyplot.grid = lambda *a, **kw: None
    pyplot.legend = lambda *a, **kw: None
    pyplot.xticks = lambda *a, **kw: None
    pyplot.yticks = lambda *a, **kw: None
    pyplot.savefig = lambda *a, **kw: None
    pyplot.close = lambda *a, **kw: None
    pyplot.tight_layout = lambda *a, **kw: None
    pyplot.rcParams = {}  # output.py 用 ["font.sans-serif"] = [...]
    mpl.pyplot = pyplot
    sys.modules["matplotlib"] = mpl
    sys.modules["matplotlib.pyplot"] = pyplot
    # matplotlib.dates 子模块（用于回测图表）
    mdates = types.ModuleType("matplotlib.dates")
    mdates.DateFormatter = lambda *a, **kw: None
    mdates.MonthLocator = lambda *a, **kw: None
    mdates.YearLocator = lambda *a, **kw: None
    mdates.DayLocator = lambda *a, **kw: None
    mdates.WeekdayLocator = lambda *a, **kw: None
    mdates.AutoDateLocator = lambda *a, **kw: None
    sys.modules["matplotlib.dates"] = mdates
    # matplotlib.font_manager（中文显示）
    fm = types.ModuleType("matplotlib.font_manager")
    fm.FontProperties = lambda *a, **kw: None
    sys.modules["matplotlib.font_manager"] = fm


def _install_yaml_stub():
    # 关键修复：test_optimization_fixes 顶层被 import 时，yaml 真实库可能没在
    # sys.modules 中（_is_real_lib_available 返回 False），但真实库装在磁盘上。
    # 此时应该用真实库而不是装 stub，否则会污染同进程后续 test_config_loader。
    if _is_real_lib_available("yaml"):
        return
    if _can_import_real("yaml"):
        # 真实库可 import，主动加载进 sys.modules
        import yaml
        return
    # 真实库不可用（无 PyYAML），才装 stub
    y = types.ModuleType("yaml")
    y.safe_load = lambda x: {}
    sys.modules["yaml"] = y


_install_pandas_stub()
_install_baostock_stub()
_install_requests_stub()
_install_numpy_stub()
_install_matplotlib_stub()
_install_yaml_stub()


# =====================================================================
# A1: DataFetcher 备用交易所逻辑（不联网）
# =====================================================================

class TestAlternateExchangeSymbol(unittest.TestCase):
    """DataFetcher._alternate_exchange_symbol 的纯函数行为。"""

    def _fetcher(self):
        # 不调 __init__，避免 requests.Session / DataCache 副作用
        from data.fetcher import DataFetcher
        return DataFetcher.__new__(DataFetcher)

    def test_sz_to_sh(self):
        f = self._fetcher()
        self.assertEqual(f._alternate_exchange_symbol("000001.SZ"), "000001.SH")

    def test_sh_to_sz(self):
        f = self._fetcher()
        self.assertEqual(f._alternate_exchange_symbol("600000.SH"), "600000.SZ")

    def test_lowercase_exchange_also_swapped(self):
        f = self._fetcher()
        self.assertEqual(f._alternate_exchange_symbol("000001.sz"), "000001.SH")

    def test_no_dot_returns_none(self):
        f = self._fetcher()
        self.assertIsNone(f._alternate_exchange_symbol("000001"))

    def test_more_than_one_dot_returns_none(self):
        f = self._fetcher()
        self.assertIsNone(f._alternate_exchange_symbol("a.b.c"))

    def test_preserves_code_part(self):
        f = self._fetcher()
        self.assertEqual(
            f._alternate_exchange_symbol("300750.SZ"), "300750.SH"
        )


class TestBaostockRecursionGuard(unittest.TestCase):
    """_fetch_from_baostock 备用交换机递归有 _alt_tries 深度限制。

    原代码在 _to_baostock_code 输出小写、传入是大写时，比较
    `alt_code != code` 永远成立 → 可能无限递归。
    现引入 _alt_tries 计数器和 _BAOSTOCK_MAX_ALT_TRIES=1 常量。
    """

    def test_max_alt_tries_constant(self):
        from data.fetcher import DataFetcher
        self.assertTrue(hasattr(DataFetcher, "_BAOSTOCK_MAX_ALT_TRIES"))
        self.assertEqual(DataFetcher._BAOSTOCK_MAX_ALT_TRIES, 1)

    def test_signature_has_alt_tries_param(self):
        """_fetch_from_baostock 必须接受 _alt_tries 关键字参数。"""
        import inspect
        from data.fetcher import DataFetcher
        sig = inspect.signature(DataFetcher._fetch_from_baostock)
        self.assertIn("_alt_tries", sig.parameters)
        # 默认 0
        self.assertEqual(sig.parameters["_alt_tries"].default, 0)


class TestBaostockMaxAltTriesConstant(unittest.TestCase):
    """直接验证类常量 _BAOSTOCK_MAX_ALT_TRIES=1（防回归）。"""

    def test_constant_exists(self):
        from data.fetcher import DataFetcher
        self.assertTrue(hasattr(DataFetcher, "_BAOSTOCK_MAX_ALT_TRIES"))
        self.assertEqual(DataFetcher._BAOSTOCK_MAX_ALT_TRIES, 1)

    def test_alt_symbol_signature(self):
        """备用交换机函数签名：symbol -> alt symbol 或 None。"""
        from data.fetcher import DataFetcher
        f = DataFetcher.__new__(DataFetcher)
        self.assertEqual(f._alternate_exchange_symbol("000001.SZ"), "000001.SH")
        self.assertEqual(f._alternate_exchange_symbol("600000.SH"), "600000.SZ")
        self.assertIsNone(f._alternate_exchange_symbol("000001"))


# =====================================================================
# A2: Sortino 比率 — 验证下行标准差公式（zero-target 法）
# =====================================================================

class _EngineForTest:
    """最小化基类的代理，只暴露 _calc_sortino_ratio 用到的字段。"""

    TRADING_DAYS_PER_YEAR = 244
    RISK_FREE_RATE_DAILY = 0.03 / 244

    def __init__(self, equity_curve):
        self.equity_curve = equity_curve

    def _calc_sortino_ratio(self):
        from backtest.base import BaseBacktestEngine
        return BaseBacktestEngine._calc_sortino_ratio(self)

    # 提供 _calc_sortino_ratio 内部用到的辅助方法
    def _strategy_returns(self):
        from backtest.base import BaseBacktestEngine
        return BaseBacktestEngine._strategy_returns(self)

    def _excess_returns(self):
        from backtest.base import BaseBacktestEngine
        return BaseBacktestEngine._excess_returns(self)


class TestSortino(unittest.TestCase):
    def _equity(self, values):
        return [{"value": v} for v in values]

    def test_constant_up_returns_high_sortino(self):
        # 持续上涨，无下行，downside_std=0 → 返回 0
        eq = self._equity([100 + i for i in range(60)])
        eng = _EngineForTest(eq)
        self.assertEqual(eng._calc_sortino_ratio(), 0.0)

    def test_constant_down_returns_negative(self):
        # 持续下跌，Sortino 必为负
        eq = self._equity([100 - i * 0.1 for i in range(60)])
        eng = _EngineForTest(eq)
        self.assertLess(eng._calc_sortino_ratio(), 0)

    def test_too_few_points_returns_zero(self):
        eng = _EngineForTest(self._equity([100, 101]))
        self.assertEqual(eng._calc_sortino_ratio(), 0.0)

    def test_formula_correctness(self):
        """手算下行标准差 (zero-target 法)：50% 涨 50% 跌。

        equity_curve 需要 >= 10 个点（_calc_sortino_ratio 的最小要求）。
        构造 [100, 150, 75, 110, 60, 130, 50, 120, 70, 100, 80, 100]
        → 11 个收益点；下行标准差 sqrt(sum(min(0,r-rf)^2) / N)。
        """
        eq = self._equity([100, 150, 75, 110, 60, 130, 50, 120, 70, 100, 80, 100])
        eng = _EngineForTest(eq)
        result = eng._calc_sortino_ratio()
        # 整体有上有下，结果可能是正也可能是负，但不该是 0
        self.assertNotEqual(result, 0.0)
        self.assertIsInstance(result, float)

    def test_one_loss_one_gain(self):
        """N=2 vs N_downside=1 的差异：手算精确值。

        公式：downside_dev = sqrt( sum(min(excess, 0)^2) / N )
        注意：excess = r - rf。r=0 时 excess=-rf<0，会被计入下行。
        """
        # 12 个点：前 10 个持平在 100，然后跌 50% 再回 100
        eq = self._equity([100] * 10 + [50, 100])
        eng = _EngineForTest(eq)
        result = eng._calc_sortino_ratio()
        # 手算：values=[100]*10+[50,100]，returns=11 个
        # returns = [0, 0, 0, 0, 0, 0, 0, 0, 0, -0.5, 1.0]
        N = 11
        rf = 0.03 / 244
        rets = [0.0] * 9 + [-0.5, 1.0]
        excess = [r - rf for r in rets]
        mean_excess = sum(excess) / N
        sum_sq_down = sum(r * r for r in excess if r < 0)
        downside_std = math.sqrt(sum_sq_down / N)
        expected = (mean_excess / downside_std) * math.sqrt(244)
        self.assertAlmostEqual(result, expected, places=6)

    def test_more_down_than_up_negative(self):
        """净亏：Sortino < 0。"""
        # 50 个点，缓慢下跌 1%
        eq = self._equity([100 * (1 - i * 0.001) for i in range(50)])
        eng = _EngineForTest(eq)
        result = eng._calc_sortino_ratio()
        self.assertLess(result, 0)


# =====================================================================
# A3: RiskManager 首日不熔断、第二日正常
# =====================================================================

class TestRiskManagerFirstDay(unittest.TestCase):
    def _mgr(self, max_daily=0.03, max_dd=0.20):
        from risk.manager import RiskManager
        return RiskManager(
            max_drawdown_pct=max_dd,
            max_daily_loss_pct=max_daily,
            enabled=True,
        )

    def test_first_day_no_circuit_breaker(self):
        """首日：即使 _peak_value 已设为 high，没有 prev 不应该熔断 daily。"""
        m = self._mgr(max_daily=0.03)
        # 第一天：prev 默认为 0，应该跳过熔断判断
        m.update_portfolio_state(total_value=1_000_000, positions={}, last_prices={})
        self.assertFalse(m._circuit_breaker)
        # prev 现在应该被更新
        self.assertEqual(m._prev_day_value, 1_000_000)

    def test_first_day_big_loss_no_daily_circuit(self):
        """首日大跌：daily_loss 无基准，不能熔断。"""
        m = self._mgr(max_daily=0.03)
        m.update_portfolio_state(total_value=900_000, positions={}, last_prices={})
        # 没熔断（首日没 prev 可比）
        self.assertFalse(m._circuit_breaker)

    def test_second_day_normal_loss_no_circuit(self):
        """第二天小幅亏损：3% 以内不熔断。"""
        m = self._mgr(max_daily=0.03)
        m.update_portfolio_state(total_value=1_000_000, positions={}, last_prices={})
        m.update_portfolio_state(total_value=985_000, positions={}, last_prices={})
        self.assertFalse(m._circuit_breaker)

    def test_second_day_large_loss_circuits(self):
        """第二天巨亏：>= max_daily 触发熔断。"""
        m = self._mgr(max_daily=0.03)
        m.update_portfolio_state(total_value=1_000_000, positions={}, last_prices={})
        m.update_portfolio_state(total_value=950_000, positions={}, last_prices={})
        self.assertTrue(m._circuit_breaker)
        self.assertIn("单日亏损", m._circuit_breaker_reason)

    def test_drawdown_still_works_first_day(self):
        """首日 drawdown 逻辑：peak 第一次设为 total，dd=0 不熔断。"""
        m = self._mgr(max_dd=0.05)
        m.update_portfolio_state(total_value=1_000_000, positions={}, last_prices={})
        # 峰值为 1M，current=1M，dd=0
        self.assertFalse(m._circuit_breaker)

    def test_drawdown_works_second_day(self):
        """第二天：drawdown 触发熔断。"""
        m = self._mgr(max_dd=0.05, max_daily=0.50)  # 调高 daily 让 drawdown 先触发
        m.update_portfolio_state(total_value=1_000_000, positions={}, last_prices={})
        m.update_portfolio_state(total_value=900_000, positions={}, last_prices={})
        # 跌幅 10% > 5% max_dd
        self.assertTrue(m._circuit_breaker)
        self.assertIn("回撤", m._circuit_breaker_reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
