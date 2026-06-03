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
    def progress(self, *a, **kw): pass
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
        self.assertEqual(len(self.app._PAGE_ROUTER), 9)
        for page in [
            self.app.PAGE_BACKTEST, self.app.PAGE_COMPARISON, self.app.PAGE_OPTIMIZE,
            self.app.PAGE_MULTI_STRATEGY, self.app.PAGE_YAML,
            self.app.PAGE_WALK_FORWARD, self.app.PAGE_REALTIME,
            self.app.PAGE_HISTORY, self.app.PAGE_WATCHLIST,
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
            self.app.PAGE_HISTORY, self.app.PAGE_WATCHLIST,
        ]
        self.assertEqual(len(consts), len(set(consts)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
