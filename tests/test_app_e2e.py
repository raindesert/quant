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
        self._run_page(self.app.PAGE_REALTIME)
        self.assertIn("实时行情", self.rec.headers[0])


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
        self.assertEqual(len(self.app._PAGE_ROUTER), 7)
        for page in [
            self.app.PAGE_BACKTEST, self.app.PAGE_COMPARISON, self.app.PAGE_OPTIMIZE,
            self.app.PAGE_MULTI_STRATEGY, self.app.PAGE_YAML,
            self.app.PAGE_WALK_FORWARD, self.app.PAGE_REALTIME,
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
        ]
        self.assertEqual(len(consts), len(set(consts)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
