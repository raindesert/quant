"""P1-3 优化器贝叶斯/随机模式测试 — 不依赖 optuna 运行。

测试方法：用 ast 解析源码验签名、import 验 enum、subprocess 验工厂。
避免测试本身触发 optuna 真实 import（污染风险）。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _module_path() -> Path:
    return Path(__file__).resolve().parent.parent / "backtest" / "optimizer.py"


def _find_function(tree, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class TestOptimizeMethodEnum(unittest.TestCase):
    """OptimizeMethod enum 行为 — 这个测试 import 模块但不触发 optuna 路径。"""

    def setUp(self):
        # 在新子进程里跑，避免污染主进程 sys.modules
        r = subprocess.run(
            [sys.executable, "-c", """
import sys
sys.path.insert(0, "%s")
from backtest.optimizer import OptimizeMethod, _is_higher_better
assert OptimizeMethod.GRID.value == "grid"
assert OptimizeMethod.RANDOM.value == "random"
assert OptimizeMethod.BAYESIAN.value == "bayesian"
assert OptimizeMethod.from_str("grid") == OptimizeMethod.GRID
assert OptimizeMethod.from_str("BAYESIAN") == OptimizeMethod.BAYESIAN
try:
    OptimizeMethod.from_str("gradient")
    raise AssertionError("应抛 ValueError")
except ValueError:
    pass
assert _is_higher_better("sharpe_ratio") is True
assert _is_higher_better("profit_pct") is True
assert _is_higher_better("max_drawdown_pct") is False
print("ENUM_OK")
""" % str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True,
        )
        self.stdout = r.stdout
        self.stderr = r.stderr
        self.returncode = r.returncode

    def test_enum_basic(self):
        self.assertEqual(self.returncode, 0, f"stdout={self.stdout}\nstderr={self.stderr}")
        self.assertIn("ENUM_OK", self.stdout)


class TestSuggesterFactory(unittest.TestCase):
    """_make_suggester 工厂 — 用 subprocess 隔离 optuna 真实 import。"""

    def test_factory_returns_callable(self):
        r = subprocess.run(
            [sys.executable, "-c", """
import sys
sys.path.insert(0, "%s")
from backtest.optimizer import _make_suggester, OptimizeMethod
# 离散 list
s1 = _make_suggester("x", [5, 10, 20], OptimizeMethod.BAYESIAN)
assert callable(s1)
# int 范围
s2 = _make_suggester("x", (1, 50), OptimizeMethod.BAYESIAN)
assert callable(s2)
# float 范围
s3 = _make_suggester("x", (0.01, 0.5), OptimizeMethod.RANDOM)
assert callable(s3)
# 非法输入
try:
    _make_suggester("x", 42, OptimizeMethod.GRID)
    raise AssertionError("应抛 ValueError")
except ValueError:
    pass
# 3-tuple 视为 categorical（合法）
s4 = _make_suggester("x", (1, 2, 3), OptimizeMethod.GRID)
assert callable(s4)
print("FACTORY_OK")
""" % str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout}\nstderr={r.stderr}")
        self.assertIn("FACTORY_OK", r.stdout)


class TestOptimizerSignature(unittest.TestCase):
    """StrategyOptimizer.optimize 接受 method/n_trials 参数（源码层验证）。"""

    def test_optimize_signature(self):
        src = _module_path()
        tree = ast.parse(src.read_text(encoding="utf-8"))
        # 找 optimize 方法
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "StrategyOptimizer":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "optimize":
                        args = [a.arg for a in item.args.args]
                        self.assertIn("method", args)
                        self.assertIn("n_trials", args)
                        return
        self.fail("找不到 StrategyOptimizer.optimize")

    def test_optimizer_module_imports(self):
        """确认 optimizer.py 顶部 import 了需要的模块。"""
        src = _module_path().read_text(encoding="utf-8")
        self.assertIn("from concurrent.futures import ProcessPoolExecutor", src)
        self.assertIn("from enum import Enum", src)
        self.assertIn("from itertools import product", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
