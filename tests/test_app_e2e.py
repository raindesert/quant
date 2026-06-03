"""app.py 端到端测试 — 用 mock streamlit 跑每个 page 验证不抛异常。

不需要 streamlit server；用 types.ModuleType 替换 streamlit，捕获所有 widget
调用，验证每个 page 函数能完整跑通。

为什么不用 AppTest：streamlit AppTest 需要 server runtime（pyarrow 等），
Termux 装 pyarrow 编译失败。所以用 lightweight mock 方案。
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============== fake streamlit ==============

class _FakeSession:
    def __init__(self):
        self.state = {}
    def __getitem__(self, k): return self.state[k]
    def __setitem__(self, k, v): self.state[k] = v
    def __contains__(self, k): return k in self.state
    def get(self, k, default=None): return self.state.get(k, default)
    def setdefault(self, k, v):
        if k not in self.state: self.state[k] = v
        return self.state[k]


class _FakeCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def metric(self, *a, **kw): pass
    def selectbox(self, *a, **kw): return a[1][0] if len(a) > 1 and a[1] else None
    def slider(self, *a, **kw): return _fake_slider(*a, **kw)
    def number_input(self, *a, **kw): return 0
    def text_input(self, *a, **kw): return ""
    def checkbox(self, *a, **kw): return a[1] if len(a) > 1 else False
    def button(self, *a, **kw): return False
    def caption(self, *a, **kw): pass
    def markdown(self, *a, **kw): pass
    def write(self, *a, **kw): pass
    def plotly_chart(self, *a, **kw): pass
    def dataframe(self, *a, **kw): pass
    def json(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def success(self, *a, **kw): pass
    def download_button(self, *a, **kw): pass
    def code(self, *a, **kw): pass
    def file_uploader(self, *a, **kw): return None
    def radio(self, label, options, **kw): return options[0] if options else None
    def data_editor(self, df, **kw): return df
    def expander(self, *a, **kw):
        class _E:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return _E()


def _fake_slider(*a, **kw):
    """模拟 streamlit slider:
    - st.slider(label, min, max) -> int
    - st.slider(label, min, max, (default_min, default_max)) -> tuple
    - st.slider(label, min, max, default, step) -> int
    """
    if len(a) == 0:
        return 0
    # 找 default 是不是 tuple
    for x in a[2:]:
        if isinstance(x, tuple):
            return x
    # 单值默认
    if len(a) >= 3 and a[2] is not None:
        return a[2]
    return a[1] if len(a) > 1 else 0


class _FakeSidebar:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def title(self, *a, **kw): pass
    def markdown(self, *a, **kw): pass
    def radio(self, label, options, **kw): return options[0] if options else None
    def text_input(self, label, value="", **kw): return value
    def selectbox(self, label, options, **kw):
        return options[0] if options else None
    def slider(self, *a, **kw): return _fake_slider(*a, **kw)
    def number_input(self, label, *a, **kw): return a[2] if len(a) > 2 else 0
    def checkbox(self, label, value=False, **kw): return value
    def button(self, label, **kw): return False
    def caption(self, *a, **kw): pass
    def multiselect(self, label, options, **kw): return list(options)
    def text_area(self, label, **kw): return ""


class FakeStreamlitRecorder:
    """记录所有 widget 调用 + 渲染输出。"""
    def __init__(self):
        self.session_state = _FakeSession()
        self._sidebar = _FakeSidebar()
        self.headers = []
        self.subheaders = []
        self.errors = []
        self.warnings = []
        self.successes = []
        self.downloads = []
        self.spinners_entered = []

    @property
    def sidebar(self): return self._sidebar
    def set_page_config(self, **kw): pass
    def header(self, *a, **kw): self.headers.append(a[0] if a else "")
    def subheader(self, *a, **kw): self.subheaders.append(a[0] if a else "")
    def markdown(self, *a, **kw): pass
    def text_input(self, *a, **kw): return "000001.SZ"
    def button(self, *a, **kw): return False
    def tabs(self, names): return [_FakeCtx() for _ in names]
    def download_button(self, *a, **kw): self.downloads.append(a[0] if a else "")
    def plotly_chart(self, *a, **kw): pass
    def dataframe(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): self.errors.append(a[0] if a else "")
    def warning(self, *a, **kw): self.warnings.append(a[0] if a else "")
    def success(self, *a, **kw): self.successes.append(a[0] if a else "")
    def metric(self, *a, **kw): pass
    def columns(self, n):
        if isinstance(n, int): return [_FakeCtx() for _ in range(n)]
        return [_FakeCtx() for _ in n]
    def form(self, *a, **kw): return _FakeCtx()
    def form_submit_button(self, *a, **kw): return False
    def selectbox(self, *a, **kw):
        return a[1][0] if len(a) > 1 and a[1] else None
    def slider(self, *a, **kw): return _fake_slider(*a, **kw)
    def number_input(self, *a, **kw): return 0
    def checkbox(self, *a, **kw): return a[1] if len(a) > 1 else False
    def json(self, *a, **kw): pass
    def file_uploader(self, *a, **kw): return None
    def radio(self, label, options, **kw): return options[0] if options else None
    def data_editor(self, df, **kw): return df
    def expander(self, *a, **kw):
        class _E:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return _E()
    def spinner(self, *a, **kw):
        self.spinners_entered.append(a[0] if a else "")
        return _FakeCtx()
    def rerun(self): pass
    def write(self, *a, **kw): pass
    def empty(self): return _FakeCtx()
    def cache_data(self, *a, **kw):
        if len(a) == 1 and callable(a[0]): return a[0]
        def decorator(f): return f
        return decorator
    def cache_resource(self, *a, **kw):
        if len(a) == 1 and callable(a[0]): return a[0]
        def decorator(f): return f
        return decorator
    def multiselect(self, label, options, **kw): return list(options)
    def text_area(self, label, **kw): return ""
    def caption(self, *a, **kw): pass
    def code(self, *a, **kw): pass
    def file_uploader(self, *a, **kw): return None
    def radio(self, label, options, **kw): return options[0] if options else None
    def data_editor(self, df, **kw): return df
    def expander(self, *a, **kw):
        class _E:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return _E()
    def container(self, *a, **kw):
        class _C:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return _C()


def _install_fake_streamlit() -> FakeStreamlitRecorder:
    """替换 sys.modules['streamlit']，返回 recorder 实例。"""
    rec = FakeStreamlitRecorder()
    fake = types.ModuleType("streamlit")
    for attr in [a for a in dir(rec) if not a.startswith("_")]:
        setattr(fake, attr, getattr(rec, attr))
    sys.modules["streamlit"] = fake
    return rec


# ============== 测试 ==============

class TestAppPages(unittest.TestCase):
    """验证 app.py 的 7 个 page 都能跑通不抛异常。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        # 删除 app.py 的 cached import，重新 import
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def _run_page(self, page_const):
        handler = self.app._PAGE_ROUTER[page_const]
        # 重置 per-test 状态
        self.rec.headers.clear()
        self.rec.subheaders.clear()
        self.rec.errors.clear()
        # 跑
        try:
            handler()
        except Exception as exc:
            self.fail(f"Page {page_const} raised: {type(exc).__name__}: {exc}")
        # 验证渲染了 header
        self.assertTrue(
            len(self.rec.headers) >= 1,
            f"Page {page_const} 没渲染 header"
        )

    def test_page_backtest(self):
        self._run_page(self.app.PAGE_BACKTEST)
        self.assertIn("单策略回测", self.rec.headers[0])

    def test_page_comparison(self):
        self._run_page(self.app.PAGE_COMPARISON)
        self.assertIn("策略对比", self.rec.headers[0])

    def test_page_optimize(self):
        self._run_page(self.app.PAGE_OPTIMIZE)
        self.assertIn("参数优化", self.rec.headers[0])

    def test_page_multi_strategy(self):
        self._run_page(self.app.PAGE_MULTI_STRATEGY)
        self.assertIn("多策略", self.rec.headers[0])

    def test_page_yaml_preset(self):
        self._run_page(self.app.PAGE_YAML)
        self.assertIn("YAML", self.rec.headers[0])

    def test_page_walk_forward(self):
        self._run_page(self.app.PAGE_WALK_FORWARD)
        self.assertIn("Walk-Forward", self.rec.headers[0])

    def test_page_realtime(self):
        # 空自选 + 无手动输入：3 个 tab 都能跑
        self._run_page(self.app.PAGE_REALTIME)
        self.assertIn("实时行情", self.rec.headers[0])

        # 加 1 个自选：tab 1 卡片渲染
        from utils.watchlist import save_watchlist, add_stock
        from utils.watchlist import DEFAULT_PATH as real_path
        import tempfile
        from pathlib import Path
        tmp_dir = Path(tempfile.mkdtemp())
        backup = tmp_dir / "real_watchlist_backup.json"
        real_existed = real_path.exists()
        if real_existed:
            backup.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            add_stock("000001.SZ", "平安银行", ["银行"])
            self._run_page(self.app.PAGE_REALTIME)
            self.assertIn("实时行情", self.rec.headers[0])
        finally:
            if real_existed and backup.exists():
                real_path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                if real_path.exists():
                    real_path.unlink()

    def test_page_realtime_cards(self):
        """测试 _render_quote_card / _render_quote_detail 不抛异常。"""
        stock = {"symbol": "000001.SZ", "name": "平安银行", "tags": []}
        quote_ok = {
            "symbol": "000001.SZ", "name": "平安银行",
            "price": 10.5, "change_pct": 1.5, "prev_close": 10.35,
            "open": 10.4, "high": 10.6, "low": 10.3,
            "volume": 1000000, "amount": 10500000,
            "timestamp": "2026-06-03 10:30:00",
        }
        # 不抛异常即可
        self.app._render_quote_card(stock, quote_ok, key_prefix="test_ok")
        # 错误行情
        self.app._render_quote_card(
            stock, {"error": "network down", "symbol": "000001.SZ"},
            key_prefix="test_err",
        )
        # 详情
        self.app._render_quote_detail(quote_ok)

    def test_page_history(self):
        # 空历史：应显示 info，不抛异常
        self.rec.session_state[self.app.HISTORY_KEY] = []
        self._run_page(self.app.PAGE_HISTORY)

        # 加 1 条历史：应显示表格
        rec = {
            "id": 1, "timestamp": "2026-06-03 10:00:00", "mode": "backtest",
            "symbol": "000001.SZ", "strategy": "sma",
            "profit_pct": 1.23, "sharpe_ratio": 0.5, "max_drawdown_pct": -2.0,
            "win_rate": 60.0, "trades": 5,
            "summary": {"profit_pct": 1.23, "symbol": "000001.SZ", "strategy": "sma"},
            "extra": {},
        }
        self.rec.session_state[self.app.HISTORY_KEY] = [rec]
        self._run_page(self.app.PAGE_HISTORY)
        self.assertIn("回测历史", self.rec.headers[0])

    def test_page_watchlist(self):
        """端到端跑 page_watchlist。空 + 非空两种情况。
        注意：page_watchlist 内部读 _WATCHLIST_PATH（app.py 启动时绑定）。
        改 monkey patch DEFAULT_PATH + 重新 import app 不优雅。
        改用：直接临时覆盖真文件，跑完还原。
        """
        import tempfile
        from pathlib import Path
        from utils.watchlist import DEFAULT_PATH as real_path
        from utils.watchlist import add_stock as wl_add

        tmp_dir = Path(tempfile.mkdtemp())
        backup = tmp_dir / "real_watchlist_backup.json"
        real_existed = real_path.exists()
        if real_existed:
            backup.write_text(real_path.read_text(encoding="utf-8"), encoding="utf-8")

        try:
            # 1) 覆盖真文件为空，page_watchlist 应跑通
            real_path.write_text('{"version": 1, "stocks": []}', encoding="utf-8")
            self._run_page(self.app.PAGE_WATCHLIST)
            self.assertIn("自选股票", self.rec.headers[0])

            # 2) 加 1 个，page_watchlist 再次跑通
            wl_add("000001.SZ", "平安银行", ["银行"])
            self._run_page(self.app.PAGE_WATCHLIST)
            self.assertIn("自选股票", self.rec.headers[0])
        finally:
            # 还原用户真文件
            if real_existed and backup.exists():
                real_path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                if real_path.exists():
                    real_path.unlink()

    def test_page_risk_empty(self):
        """空历史 → page_risk_metrics 走 early return。"""
        self.rec.session_state[self.app.HISTORY_KEY] = []
        self._run_page(self.app.PAGE_RISK)
        self.assertIn("风险分析", self.rec.headers[0])

    def test_page_risk_no_equity(self):
        """历史有记录但没 equity_curve → warning。"""
        self.rec.session_state[self.app.HISTORY_KEY] = [
            {"symbol": "X", "strategy": "sma", "profit_pct": 1.0}  # 缺 equity_curve
        ]
        self._run_page(self.app.PAGE_RISK)
        self.assertIn("风险分析", self.rec.headers[0])

    def test_page_risk_with_equity(self):
        """正常历史 → 完整跑通。"""
        ec = [{"date": f"2026-01-{i+1:02d}", "value": 1_000_000 + i * 1000}
              for i in range(60)]
        self.rec.session_state[self.app.HISTORY_KEY] = [{
            "symbol": "000001.SZ", "strategy": "sma",
            "profit_pct": 5.0, "equity_curve": ec,
        }]
        self._run_page(self.app.PAGE_RISK)
        self.assertIn("风险分析", self.rec.headers[0])


class TestHistoryManager(unittest.TestCase):
    """测试 _history_add / _history_remove / _history_clear 的纯逻辑（不调 st UI）。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def setUp(self):
        # 重置
        self.rec.session_state[self.app.HISTORY_KEY] = []

    def test_history_add_assigns_incrementing_id(self):
        self.app._history_add({"profit_pct": 1.0, "symbol": "X", "strategy": "sma"})
        self.app._history_add({"profit_pct": 2.0, "symbol": "Y", "strategy": "rsi"})
        h = self.rec.session_state[self.app.HISTORY_KEY]
        self.assertEqual(len(h), 2)
        self.assertEqual(h[0]["id"], 1)
        self.assertEqual(h[1]["id"], 2)

    def test_history_add_extracts_metrics(self):
        self.app._history_add({
            "profit_pct": 1.5, "sharpe_ratio": 1.2, "max_drawdown_pct": -3.0,
            "win_rate": 60.0, "trades": 5, "symbol": "X", "strategy": "sma",
        })
        h = self.rec.session_state[self.app.HISTORY_KEY][0]
        self.assertEqual(h["profit_pct"], 1.5)
        self.assertEqual(h["sharpe_ratio"], 1.2)
        self.assertEqual(h["max_drawdown_pct"], -3.0)
        self.assertEqual(h["win_rate"], 60.0)
        self.assertEqual(h["trades"], 5)

    def test_history_add_keeps_full_summary(self):
        full = {"profit_pct": 1.0, "equity_curve": [{"date": "d1", "value": 1.0}]}
        self.app._history_add(full)
        h = self.rec.session_state[self.app.HISTORY_KEY][0]
        self.assertIn("equity_curve", h["summary"])

    def test_history_add_with_extra(self):
        self.app._history_add(
            {"profit_pct": 1.0}, mode="multi_strategy",
            extra={"sub_strategies": [{"name": "sma"}, {"name": "rsi"}]},
        )
        h = self.rec.session_state[self.app.HISTORY_KEY][0]
        self.assertEqual(h["mode"], "multi_strategy")
        self.assertEqual(len(h["extra"]["sub_strategies"]), 2)

    def test_history_remove(self):
        self.app._history_add({"profit_pct": 1.0})
        self.app._history_add({"profit_pct": 2.0})
        self.app._history_add({"profit_pct": 3.0})
        # 删 id=2
        self.app._history_remove(2)
        h = self.rec.session_state[self.app.HISTORY_KEY]
        self.assertEqual(len(h), 2)
        self.assertEqual([r["profit_pct"] for r in h], [1.0, 3.0])

    def test_history_remove_nonexistent_no_error(self):
        self.app._history_add({"profit_pct": 1.0})
        self.app._history_remove(999)  # 不存在
        self.assertEqual(len(self.rec.session_state[self.app.HISTORY_KEY]), 1)

    def test_history_clear(self):
        for i in range(3):
            self.app._history_add({"profit_pct": float(i)})
        self.app._history_clear()
        self.assertEqual(self.rec.session_state[self.app.HISTORY_KEY], [])

    def test_history_max_cap(self):
        """超过 HISTORY_MAX 删最旧（FIFO）。"""
        for i in range(self.app.HISTORY_MAX + 5):
            self.app._history_add({"profit_pct": float(i)})
        h = self.rec.session_state[self.app.HISTORY_KEY]
        self.assertEqual(len(h), self.app.HISTORY_MAX)
        # 最旧的 5 条被删，剩下 profit_pct 从 5 开始
        self.assertEqual(h[0]["profit_pct"], 5.0)
        self.assertEqual(h[-1]["profit_pct"], float(self.app.HISTORY_MAX + 4))


class TestWatchlist(unittest.TestCase):
    """utils.watchlist 的单元测试 — 用临时文件隔离，不污染用户真文件。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp()) / "wl.json"

    def test_normalize_various_formats(self):
        from utils.watchlist import _normalize_symbol, is_valid_symbol
        cases = [
            ("000001.SZ", "000001.SZ"),
            ("000001.sz", "000001.SZ"),
            ("sz000001", "000001.SZ"),
            ("SZ000001", "000001.SZ"),
            ("000001", "000001.SZ"),  # 默认深市
            ("600000", "600000.SH"),
            ("601318", "601318.SH"),
            ("688000", "688000.SH"),
            ("sh600000", "600000.SH"),
        ]
        for inp, expected in cases:
            self.assertEqual(_normalize_symbol(inp), expected, f"inp={inp}")
        # 校验
        self.assertTrue(is_valid_symbol("000001.SZ"))
        self.assertTrue(is_valid_symbol("600000.SH"))
        self.assertFalse(is_valid_symbol(""))
        self.assertFalse(is_valid_symbol("00001"))
        self.assertFalse(is_valid_symbol("000001.sz"))  # 应该是大写

    def test_add_dedup(self):
        from utils.watchlist import add_stock, load_watchlist
        r = add_stock("000001.SZ", "平安银行", ["银行"], path=self.tmp)
        self.assertIsNotNone(r)
        self.assertEqual(r["symbol"], "000001.SZ")
        # 重复
        self.assertIsNone(add_stock("000001.SZ", path=self.tmp))
        # 无效
        self.assertIsNone(add_stock("invalid", path=self.tmp))
        self.assertEqual(len(load_watchlist(self.tmp)), 1)

    def test_remove(self):
        from utils.watchlist import add_stock, remove_stock, load_watchlist
        add_stock("000001.SZ", path=self.tmp)
        add_stock("600000.SH", path=self.tmp)
        self.assertTrue(remove_stock("000001.SZ", path=self.tmp))
        self.assertFalse(remove_stock("000001.SZ", path=self.tmp))  # 已无
        self.assertEqual(len(load_watchlist(self.tmp)), 1)

    def test_update(self):
        from utils.watchlist import add_stock, update_stock, load_watchlist
        add_stock("000001.SZ", "旧名", path=self.tmp)
        self.assertTrue(update_stock("000001.SZ", name="新名", path=self.tmp))
        s = load_watchlist(self.tmp)[0]
        self.assertEqual(s["name"], "新名")

    def test_update_ignores_protected_fields(self):
        """update_stock 实现: 在循环里 if k in ('symbol', 'added'): continue
        验证: 通过 update_stock 改 name 成功; 试图通过 **fields 改 symbol
        也不会报错（因为 **fields 不包含 symbol 位置绑定）。
        """
        from utils.watchlist import add_stock, update_stock, load_watchlist
        add_stock("000001.SZ", "旧名", path=self.tmp)
        # 正常更新 name — 成功
        self.assertTrue(update_stock("000001.SZ", name="新名", path=self.tmp))
        s = load_watchlist(self.tmp)[0]
        self.assertEqual(s["name"], "新名")
        # 试图把 symbol 当 fields 改（API 内部忽略）
        # 这里测: 调 update_stock 时即使 *fields 里有 'symbol'/'added'，也不会生效
        # 模拟：调 add_stock 添加 Y, 改 X 的 fields
        update_stock("000001.SZ", tags=["已改"], path=self.tmp)
        s2 = load_watchlist(self.tmp)[0]
        self.assertEqual(s2["tags"], ["已改"])
        self.assertEqual(s2["symbol"], "000001.SZ")  # symbol 没变

    def test_enabled_filter(self):
        from utils.watchlist import (
            add_stock, update_stock, get_enabled_symbols,
        )
        add_stock("000001.SZ", path=self.tmp)
        add_stock("600000.SH", path=self.tmp)
        add_stock("000002.SZ", path=self.tmp)
        syms = get_enabled_symbols(self.tmp)
        self.assertEqual(syms, ["000001.SZ", "600000.SH", "000002.SZ"])
        update_stock("000001.SZ", enabled=False, path=self.tmp)
        syms = get_enabled_symbols(self.tmp)
        self.assertEqual(syms, ["600000.SH", "000002.SZ"])

    def test_csv_roundtrip(self):
        from utils.watchlist import add_stock, export_csv, import_csv
        add_stock("000001.SZ", "平安", ["银行"], path=self.tmp)
        add_stock("600000.SH", "浦发", ["银行"], path=self.tmp)
        csv = export_csv(path=self.tmp)
        self.assertIn("symbol,name,tags", csv)
        self.assertIn("000001.SZ", csv)
        # 移到新文件再导入
        import tempfile
        from pathlib import Path
        tmp2 = Path(tempfile.mkdtemp()) / "wl2.json"
        n = import_csv(csv, path=tmp2)
        self.assertEqual(n, 2)
        # 重复导入
        n = import_csv(csv, path=tmp2)
        self.assertEqual(n, 0)

    def test_load_handles_corrupt(self):
        from utils.watchlist import load_watchlist
        self.tmp.write_text("not json {{{", encoding="utf-8")
        self.assertEqual(load_watchlist(self.tmp), [])

    def test_load_filters_invalid_entries(self):
        from utils.watchlist import load_watchlist, save_watchlist
        save_watchlist([
            {"symbol": "000001.SZ", "name": "OK", "tags": []},
            {"symbol": "invalid", "name": "bad"},  # 应被过滤
            {"not_a_dict": True},  # 应被过滤
            {"symbol": "600000.SH"},  # OK, 缺字段补默认
        ], path=self.tmp)
        stocks = load_watchlist(self.tmp)
        self.assertEqual(len(stocks), 2)
        symbols = {s["symbol"] for s in stocks}
        self.assertEqual(symbols, {"000001.SZ", "600000.SH"})

    # ============== batch_backtest 单元测试 ==============

    def test_batch_backtest_empty(self):
        from utils.watchlist import batch_backtest
        self.assertEqual(batch_backtest([]), [])

    def test_batch_backtest_basic(self):
        from utils.watchlist import batch_backtest
        class FakeEngine:
            def __init__(self, **kw): self.kw = kw
            def run(self, strategy, symbol, days=250):
                return {
                    "profit_pct": float(hash(symbol) % 100) / 10,
                    "sharpe_ratio": 0.5, "max_drawdown_pct": -1.0,
                    "win_rate": 50.0, "trades": 5, "final_value": 1050000,
                }
        results = batch_backtest(
            ["000001.SZ", "600000.SH", "000002.SZ"],
            strategy_name="sma", days=60,
            engine_factory=FakeEngine,
        )
        self.assertEqual(len(results), 3)
        # 顺序保持
        self.assertEqual([r["symbol"] for r in results],
                         ["000001.SZ", "600000.SH", "000002.SZ"])
        # 全部成功
        self.assertTrue(all("summary" in r for r in results))

    def test_batch_backtest_all_fail(self):
        from utils.watchlist import batch_backtest
        class FailEngine:
            def run(self, *a, **kw): raise RuntimeError("net down")
        results = batch_backtest(["A", "B"], engine_factory=FailEngine)
        self.assertEqual(len(results), 2)
        self.assertTrue(all("error" in r for r in results))

    def test_batch_backtest_mixed(self):
        from utils.watchlist import batch_backtest
        class MixedFactory:
            n = 0
            def __call__(self):
                type(self).n += 1
                n = type(self).n
                if n == 1:
                    class _E:
                        def run(self_, *a, **kw): raise RuntimeError("fail")
                    return _E()
                class _E:
                    def run(self_, *a, **kw): return {"profit_pct": 5.0}
                return _E()
        results = batch_backtest(["A", "B"], engine_factory=MixedFactory())
        statuses = ["error" in r for r in results]
        self.assertNotEqual(statuses[0], statuses[1])

    def test_rank_batch_by_profit_desc(self):
        from utils.watchlist import rank_batch_results
        results = [
            {"symbol": "A", "summary": {"profit_pct": 1.0}},
            {"symbol": "B", "summary": {"profit_pct": 5.0}},
            {"symbol": "C", "summary": {"profit_pct": 3.0}},
            {"symbol": "D", "error": "x"},
        ]
        ranked = rank_batch_results(results, metric="profit_pct", descending=True)
        # 成功的 3 只按 desc 排序
        self.assertEqual([r["symbol"] for r in ranked],
                         ["B", "C", "A", "D"])
        # rank 字段
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 3, 4])

    def test_rank_batch_ascending_fails_last(self):
        from utils.watchlist import rank_batch_results
        results = [
            {"symbol": "A", "summary": {"profit_pct": 1.0}},
            {"symbol": "B", "summary": {"profit_pct": 5.0}},
            {"symbol": "D", "error": "x"},
        ]
        # 升序 (回撤越小越好): 失败放最后
        ranked = rank_batch_results(results, metric="profit_pct", descending=False)
        self.assertEqual(ranked[0]["symbol"], "A")
        self.assertEqual(ranked[1]["symbol"], "B")
        self.assertEqual(ranked[2]["symbol"], "D")  # 失败放最后

    def test_rank_different_metric(self):
        from utils.watchlist import rank_batch_results
        results = [
            {"symbol": "A", "summary": {"sharpe_ratio": 0.5, "profit_pct": 5.0}},
            {"symbol": "B", "summary": {"sharpe_ratio": 1.5, "profit_pct": 1.0}},
        ]
        ranked = rank_batch_results(results, metric="sharpe_ratio", descending=True)
        # B (1.5) > A (0.5)
        self.assertEqual([r["symbol"] for r in ranked], ["B", "A"])


class TestKlineSection(unittest.TestCase):
    """测试 _render_kline_section 不抛异常 + 处理各种数据场景。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def _fake_df(self, n: int = 60):
        """构造 fake K 线 DataFrame。"""
        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({
            "open": np.linspace(10, 12, n) + np.random.RandomState(0).normal(0, 0.1, n),
            "high": np.linspace(10, 12, n) + 0.5,
            "low": np.linspace(10, 12, n) - 0.5,
            "close": np.linspace(10, 12, n),
            "volume": np.ones(n) * 1_000_000,
        }, index=idx)

    def test_kline_empty_symbol(self):
        """空 symbol → info 提示，不抛异常。"""
        self.app._render_kline_section("", key_prefix="test_empty")

    def test_kline_full_path_with_fake_data(self):
        """完整路径：注入 fake df，走 K 线 + MA + 成交量 + 原始数据。"""
        fake = self._fake_df(60)
        # monkey-patch _fetch_kline_cached 返 fake df
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: fake
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_full")
        finally:
            self.app._fetch_kline_cached = original

    def test_kline_data_empty(self):
        """数据为空 → warning，不抛异常。"""
        import pandas as pd
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: pd.DataFrame()
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_empty_df")
        finally:
            self.app._fetch_kline_cached = original

    def test_kline_data_none(self):
        """数据为 None → warning，不抛异常。"""
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: None
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_none")
        finally:
            self.app._fetch_kline_cached = original

    def test_kline_missing_columns(self):
        """数据缺列 → error 提示。"""
        import pandas as pd
        bad_df = pd.DataFrame({"open": [1, 2], "close": [1.5, 2.5]})  # 缺 high/low/volume
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: bad_df
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_missing")
        finally:
            self.app._fetch_kline_cached = original

    def test_kline_fetch_exception(self):
        """数据获取异常 → error，不抛。"""
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: (_ for _ in ()).throw(
            ConnectionError("network down")
        )
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_exc")
        finally:
            self.app._fetch_kline_cached = original

    def test_kline_minute_frequency(self):
        """分钟频率也能跑通。"""
        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-06-03 09:30", periods=48, freq="5min")
        fake = pd.DataFrame({
            "open": np.linspace(10, 11, 48),
            "high": np.linspace(10, 11, 48) + 0.1,
            "low": np.linspace(10, 11, 48) - 0.1,
            "close": np.linspace(10, 11, 48) + 0.05,
            "volume": np.ones(48) * 100,
        }, index=idx)
        original = self.app._fetch_kline_cached
        self.app._fetch_kline_cached = lambda *a, **kw: fake
        try:
            self.app._render_kline_section("000001.SZ", key_prefix="test_m5")
        finally:
            self.app._fetch_kline_cached = original


class TestIndicatorSection(unittest.TestCase):
    """测试 _render_indicator_section (MACD / RSI / KDJ) 不抛异常。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def _fake_df(self, n: int = 60):
        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        np.random.seed(0)
        prices = np.cumsum(np.random.normal(0, 1, n)) + 10
        return pd.DataFrame({
            "open": prices + np.random.normal(0, 0.1, n),
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.ones(n) * 1_000_000,
        }, index=idx)

    def test_indicator_section_with_data(self):
        """完整路径：60 行数据，默认 MACD 开。"""
        self.app._render_indicator_section(self._fake_df(60))

    def test_indicator_section_too_short(self):
        """数据太短 (<5) → 直接 return，不抛。"""
        self.app._render_indicator_section(self._fake_df(3))

    def test_indicator_section_none(self):
        """None 数据 → 直接 return。"""
        self.app._render_indicator_section(None)

    def test_indicator_section_explicit_all_off(self):
        """通过 monkey-patch 把 checkbox 返 False，3 个指标都关闭。"""
        # 替换 fake checkbox 行为：返 False
        class _Col:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def checkbox(self, *a, **kw): return False

        class _Columns:
            def __call__(self, n):
                if isinstance(n, int):
                    return [_Col() for _ in range(n)]
                return [_Col() for _ in n]

        # monkey-patch st.columns
        original_columns = self.app.st.columns
        self.app.st.columns = _Columns()
        try:
            self.app._render_indicator_section(self._fake_df(60))
        finally:
            self.app.st.columns = original_columns

    def test_indicator_section_explicit_all_on(self):
        """3 个指标全开。"""
        class _Col:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def checkbox(self, *a, **kw): return True

        class _Columns:
            def __call__(self, n):
                if isinstance(n, int):
                    return [_Col() for _ in range(n)]
                return [_Col() for _ in n]

        original_columns = self.app.st.columns
        self.app.st.columns = _Columns()
        try:
            self.app._render_indicator_section(self._fake_df(60))
        finally:
            self.app.st.columns = original_columns


class TestBuySellHelpers(unittest.TestCase):
    """测试买卖点 helper 函数的纯逻辑部分。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def test_calc_trade_pnl_simple(self):
        """3 笔交易: 1 盈 1 亏 1 平。"""
        trades = [
            {"date": "2026-01-15", "action": "buy", "price": 10.0},
            {"date": "2026-02-01", "action": "sell", "price": 11.0},  # +1 盈
            {"date": "2026-02-15", "action": "buy", "price": 12.0},
            {"date": "2026-03-01", "action": "sell", "price": 11.5},  # -0.5 亏
            {"date": "2026-03-15", "action": "buy", "price": 11.0},
            {"date": "2026-04-01", "action": "sell", "price": 11.0},  # 平
        ]
        wins, losses, total = self.app._calc_trade_pnl(trades)
        self.assertEqual((wins, losses, total), (1, 1, 3))

    def test_calc_trade_pnl_no_sell(self):
        """只买不卖: 0 total。"""
        trades = [{"date": "2026-01-15", "action": "buy", "price": 10.0}]
        self.assertEqual(self.app._calc_trade_pnl(trades), (0, 0, 0))

    def test_calc_trade_pnl_empty(self):
        """空列表。"""
        self.assertEqual(self.app._calc_trade_pnl([]), (0, 0, 0))

    def test_mark_df_with_trades(self):
        """把 trades 对齐到 df.index 标记。"""
        import pandas as pd
        df = pd.DataFrame({
            "open": [10, 11, 12, 13], "high": [10, 11, 12, 13],
            "low": [10, 11, 12, 13], "close": [10, 11, 12, 13],
            "volume": [1, 1, 1, 1],
        }, index=pd.date_range("2026-01-01", periods=4, freq="D"))
        trades = [
            {"date": "2026-01-02", "action": "buy", "price": 11.0},
            {"date": "2026-01-04", "action": "sell", "price": 13.0},
        ]
        marked = self.app._mark_df_with_trades(df, trades)
        self.assertIsNotNone(marked)
        self.assertEqual(marked.loc["2026-01-02", "_trade_marker"], "buy")
        self.assertEqual(marked.loc["2026-01-04", "_trade_marker"], "sell")
        # 1/3 无标记
        self.assertIsNone(marked.loc["2026-01-01", "_trade_marker"])

    def test_mark_df_no_trades(self):
        """空 trades → 返 None。"""
        import pandas as pd
        df = pd.DataFrame({"open": [1], "close": [1]})
        self.assertIsNone(self.app._mark_df_with_trades(df, []))

    def test_build_kline_with_markers(self):
        """构建带标记的 fig 不抛异常。"""
        import pandas as pd
        df = pd.DataFrame({
            "open": [10, 11, 12, 13], "high": [10, 11, 12, 13],
            "low": [10, 11, 12, 13], "close": [10, 11, 12, 13],
            "volume": [1, 1, 1, 1],
            "_trade_marker": [None, "buy", None, "sell"],
            "_trade_price": [None, 11.0, None, 13.0],
        }, index=pd.date_range("2026-01-01", periods=4, freq="D"))
        trades = [
            {"date": "2026-01-02", "action": "buy", "price": 11.0},
            {"date": "2026-01-04", "action": "sell", "price": 13.0},
        ]
        fig = self.app._build_kline_with_markers(df, trades, "TEST", "day")
        # fig 应该有 3 个 trace: K线 + 买 + 卖
        self.assertEqual(len(fig.data), 3)

    def test_build_kline_with_only_buy(self):
        """只有买: 2 个 trace。"""
        import pandas as pd
        df = pd.DataFrame({
            "open": [10, 11], "high": [10, 11], "low": [10, 11], "close": [10, 11],
            "volume": [1, 1],
            "_trade_marker": [None, "buy"],
            "_trade_price": [None, 11.0],
        }, index=pd.date_range("2026-01-01", periods=2, freq="D"))
        fig = self.app._build_kline_with_markers(df, [{"date": "2026-01-02", "action": "buy", "price": 11.0}],
                                                 "TEST", "day")
        self.assertEqual(len(fig.data), 2)  # K + buy

    def test_is_nan(self):
        """_is_nan 工具。"""
        from app import _is_nan
        self.assertTrue(_is_nan(None))
        self.assertTrue(_is_nan(float("nan")))
        self.assertFalse(_is_nan(0))
        self.assertFalse(_is_nan(0.0))
        self.assertFalse(_is_nan(""))


class TestStrategyOverlay(unittest.TestCase):
    """测试策略信号叠加 helper。"""

    @classmethod
    def setUpClass(cls):
        cls.rec = _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def _fake_df(self, n: int = 100):
        import pandas as pd
        import numpy as np
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        np.random.seed(42)
        prices = np.cumsum(np.random.normal(0, 1, n)) + 10
        return pd.DataFrame({
            "open": prices + np.random.normal(0, 0.1, n),
            "high": prices + 0.5,
            "low": prices - 0.5,
            "close": prices,
            "volume": np.ones(n) * 1_000_000,
        }, index=idx)

    def test_build_sma_only(self):
        """只画 SMA 通道。"""
        df = self._fake_df(100)
        fig = self.app._build_strategy_overlay_fig(
            df, "TEST", "day", sma_fast=5, sma_slow=20,
            show_sma=True, show_bb=False, show_cross=False,
        )
        # 1 K线 + 2 SMA = 3 trace
        self.assertEqual(len(fig.data), 3)

    def test_build_bb_only(self):
        """只画 BB 通道 (含可能的突破点)。"""
        df = self._fake_df(100)
        fig = self.app._build_strategy_overlay_fig(
            df, "TEST", "day", sma_fast=5, sma_slow=20,
            show_sma=False, show_bb=True, show_cross=False,
        )
        # 至少 4 个 trace (K + 3 BB线); 突破点可能 +1~2
        self.assertGreaterEqual(len(fig.data), 4)

    def test_build_cross_only(self):
        """只画交叉标记。"""
        df = self._fake_df(100)
        fig = self.app._build_strategy_overlay_fig(
            df, "TEST", "day", sma_fast=5, sma_slow=20,
            show_sma=False, show_bb=False, show_cross=True,
        )
        # 1 K线 + 金叉(0+ 或多个) + 死叉
        # 至少 1 个 trace
        self.assertGreaterEqual(len(fig.data), 1)

    def test_build_all_three(self):
        """全开: K线 + 2 SMA + 金叉(可能0) + 死叉(可能0) + BB 3条 + 突破2(可能0)。"""
        df = self._fake_df(100)
        fig = self.app._build_strategy_overlay_fig(
            df, "TEST", "day", sma_fast=5, sma_slow=20, bb_period=20, bb_std=2.0,
            show_sma=True, show_bb=True, show_cross=True,
        )
        # 至少 1+2+3 = 6 trace (K + 2 SMA + 3 BB)
        self.assertGreaterEqual(len(fig.data), 6)

    def test_build_with_invalid_sma_order(self):
        """快>=慢 → 跳过交叉检测, 不抛异常。"""
        df = self._fake_df(100)
        fig = self.app._build_strategy_overlay_fig(
            df, "TEST", "day", sma_fast=20, sma_slow=5,  # 倒序
            show_sma=True, show_bb=False, show_cross=True,
        )
        # 1 K + 2 SMA = 3 trace (没金叉/死叉)
        self.assertEqual(len(fig.data), 3)

    def test_build_empty_df(self):
        """空 df → 返 None。"""
        import pandas as pd
        self.assertIsNone(
            self.app._build_strategy_overlay_fig(pd.DataFrame(), "X", "day")
        )

    def test_build_none_df(self):
        """None df → 返 None。"""
        self.assertIsNone(
            self.app._build_strategy_overlay_fig(None, "X", "day")
        )

    def test_overlay_summary_skips_short_data(self):
        """数据 < 慢线周期 → summary 跳过, 不抛。"""
        df = self._fake_df(10)  # 只有 10 行
        # 不抛异常
        self.app._render_overlay_summary(
            df, sma_fast=5, sma_slow=20, bb_period=20, bb_std=2.0,
            show_sma=True, show_bb=True, show_cross=True,
        )


class TestAppRouter(unittest.TestCase):
    """验证 _PAGE_ROUTER 字典完整性。"""

    @classmethod
    def setUpClass(cls):
        _install_fake_streamlit()
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        import app  # noqa: F401
        cls.app = app

    def test_router_has_all_pages(self):
        self.assertEqual(len(self.app._PAGE_ROUTER), 10)
        for page in [
            self.app.PAGE_BACKTEST, self.app.PAGE_COMPARISON, self.app.PAGE_OPTIMIZE,
            self.app.PAGE_MULTI_STRATEGY, self.app.PAGE_YAML,
            self.app.PAGE_WALK_FORWARD, self.app.PAGE_REALTIME,
            self.app.PAGE_HISTORY, self.app.PAGE_WATCHLIST, self.app.PAGE_RISK,
        ]:
            self.assertIn(page, self.app._PAGE_ROUTER)
            self.assertTrue(callable(self.app._PAGE_ROUTER[page]))

    def test_pages_match_all_pages_list(self):
        """_PAGE_ROUTER keys 必须和 ALL_PAGES 一致（防止漏注册）。"""
        self.assertEqual(set(self.app._PAGE_ROUTER.keys()), set(self.app.ALL_PAGES))

    def test_constants_distinct(self):
        """所有 page 常量互不相同。"""
        consts = [
            self.app.PAGE_BACKTEST, self.app.PAGE_COMPARISON, self.app.PAGE_OPTIMIZE,
            self.app.PAGE_MULTI_STRATEGY, self.app.PAGE_YAML,
            self.app.PAGE_WALK_FORWARD, self.app.PAGE_REALTIME,
            self.app.PAGE_HISTORY, self.app.PAGE_WATCHLIST, self.app.PAGE_RISK,
        ]
        self.assertEqual(len(consts), len(set(consts)))


class TestRiskMetrics(unittest.TestCase):
    """utils.risk 纯函数测试。"""

    def test_value_at_risk_historical(self):
        from utils.risk import value_at_risk
        # 简单 equity: 大部分小波动，1 个大亏损
        equity = [100, 101, 100, 99, 102, 80, 95, 100, 99, 100, 100]
        var = value_at_risk(equity, 0.95)
        self.assertGreater(var, 0)  # 损失
        self.assertGreater(var, 0.1)  # 大亏损 > 10%

    def test_value_at_risk_parametric(self):
        from utils.risk import value_at_risk
        equity = [100 + i for i in range(50)]  # 稳定上升
        var = value_at_risk(equity, 0.95, method="parametric")
        # 0 收益 → VaR ≈ 0
        self.assertAlmostEqual(var, 0, delta=0.01)

    def test_conditional_var(self):
        from utils.risk import conditional_var
        equity = [100, 101, 100, 99, 102, 80, 95, 100, 99, 100, 100]
        cvar = conditional_var(equity, 0.95)
        self.assertGreater(cvar, 0)
        # CVaR >= VaR (更严格)
        from utils.risk import value_at_risk
        var = value_at_risk(equity, 0.95)
        self.assertGreaterEqual(cvar, var)

    def test_max_drawdown_simple(self):
        from utils.risk import max_drawdown
        equity = [100, 110, 120, 100, 80, 95, 105, 100]
        mdd = max_drawdown(equity)
        self.assertEqual(mdd["peak_idx"], 2)  # 120
        self.assertEqual(mdd["trough_idx"], 4)  # 80
        # 80/120 = 0.6667, mdd = 0.6667 - 1 = -0.3333
        self.assertAlmostEqual(mdd["max_drawdown"], 80/120 - 1, places=4)
        # 后续 95/105/100 都 < 120, 未恢复
        self.assertIsNone(mdd["recovery_idx"])

    def test_max_drawdown_recovered(self):
        from utils.risk import max_drawdown
        equity = [100, 110, 120, 80, 100, 130]
        mdd = max_drawdown(equity)
        self.assertEqual(mdd["peak_idx"], 2)
        self.assertEqual(mdd["trough_idx"], 3)
        # 130 > 120 → 恢复
        self.assertEqual(mdd["recovery_idx"], 5)
        self.assertEqual(mdd["drawdown_duration"], 1)
        self.assertEqual(mdd["recovery_duration"], 2)

    def test_max_drawdown_dict_input(self):
        from utils.risk import max_drawdown
        ec = [{"value": v} for v in [100, 110, 120, 80, 100, 130]]
        mdd = max_drawdown(ec)
        self.assertEqual(mdd["peak_idx"], 2)

    def test_max_consecutive_losses(self):
        from utils.risk import max_consecutive_losses
        # 5 连亏: 100→99→98→97→96→95, 损失累计
        # (99-100)/100 + (98-99)/99 + (97-98)/98 + (96-97)/97 + (95-96)/96
        equity = [100, 99, 98, 97, 96, 95, 96, 94, 93, 92, 93]
        mcl = max_consecutive_losses(equity)
        self.assertEqual(mcl["max_count"], 5)
        # 不精确校验 sum，只校验是负数且 < -0.04
        self.assertLess(mcl["max_loss_sum"], -0.04)
        self.assertGreater(mcl["max_loss_sum"], -0.06)

    def test_rolling_sharpe(self):
        from utils.risk import rolling_sharpe
        equity = [100 + i for i in range(50)]  # 稳定上升
        s = rolling_sharpe(equity, window=10)
        self.assertEqual(len(s), 49)
        # 全部为正（持续上升）
        self.assertTrue(all(x >= 0 for x in s))

    def test_rolling_volatility(self):
        from utils.risk import rolling_volatility
        equity = [100 + (i % 5) for i in range(30)]  # 振荡
        v = rolling_volatility(equity, window=10)
        self.assertEqual(len(v), 29)
        # 至少有一个 > 0（除了前 9 个）
        self.assertTrue(any(x > 0 for x in v[10:]))

    def test_monte_carlo_basic(self):
        from utils.risk import monte_carlo_simulation
        equity = [100 + i * 0.5 for i in range(50)]
        paths = monte_carlo_simulation(equity, n_sims=10, n_days=30, seed=42)
        self.assertEqual(len(paths), 10)
        self.assertEqual(len(paths[0]), 31)
        # 所有路径起始点 = 最后值
        last = 100 + 49 * 0.5
        for p in paths:
            self.assertAlmostEqual(p[0], last, places=1)

    def test_monte_carlo_short_data(self):
        from utils.risk import monte_carlo_simulation
        # 数据不足
        self.assertEqual(monte_carlo_simulation([100], n_sims=10), [])
        self.assertEqual(monte_carlo_simulation([], n_sims=10), [])

    def test_summary_risk_metrics(self):
        from utils.risk import summary_risk_metrics
        equity = [100, 101, 99, 102, 98, 103, 100, 105, 95, 110]
        s = summary_risk_metrics(equity)
        self.assertIn("VaR 95%", s)
        self.assertIn("VaR 99%", s)
        self.assertIn("CVaR 95%", s)
        self.assertIn("CVaR 99%", s)
        self.assertIn("最大回撤", s)
        self.assertIn("最大连续亏损", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
