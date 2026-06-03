"""P2-2 多策略并行组合测试 — subprocess 隔离。"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUANT_ROOT = str(Path(__file__).resolve().parent.parent)


def _run(code: str) -> tuple[int, str, str]:
    env = {**os.environ, "QUANT_ROOT": QUANT_ROOT}
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, env=env,
    )
    return r.returncode, r.stdout, r.stderr


class TestMultiStrategyEngine(unittest.TestCase):
    def test_init_default_weights(self):
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine
e = MultiStrategyEngine(strategies=["sma", "rsi"], symbol="000001.SZ")
# 等分权重
assert abs(sum(e.weights) - 1.0) < 1e-6
assert abs(e.weights[0] - 0.5) < 1e-6
assert abs(e.weights[1] - 0.5) < 1e-6
print("INIT_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("INIT_OK", out)

    def test_init_custom_weights_validates(self):
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine
# 权重和不=1 → 抛错
try:
    MultiStrategyEngine(strategies=["sma", "rsi"], symbol="X", weights=[0.6, 0.6])
    raise AssertionError("应抛 ValueError")
except ValueError as e:
    assert "总和" in str(e)
# 负权重
try:
    MultiStrategyEngine(strategies=["sma"], symbol="X", weights=[-0.5])
    raise AssertionError("应抛 ValueError")
except ValueError as e:
    assert "不能为负" in str(e)
# 长度不一致
try:
    MultiStrategyEngine(strategies=["sma", "rsi"], symbol="X", weights=[0.5, 0.3, 0.2])
    raise AssertionError("应抛 ValueError")
except ValueError as e:
    assert "不一致" in str(e)
print("VALIDATE_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("VALIDATE_OK", out)

    def test_init_empty_strategies_raises(self):
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine
try:
    MultiStrategyEngine(strategies=[], symbol="X")
    raise AssertionError("应抛 ValueError")
except ValueError as e:
    assert "不能为空" in str(e)
print("EMPTY_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("EMPTY_OK", out)

    def test_combine_aggregates_correctly(self):
        """_combine 方法把 2 个子策略的 equity_curve 加权汇总。"""
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine

# 不调 run()（避免真实数据），直接测 _combine
e = MultiStrategyEngine(
    strategies=["sma", "rsi"], symbol="X",
    initial_cash=1000000, weights=[0.6, 0.4],
)

# 构造 2 个子策略结果
sub1 = {
    "strategy": "sma",
    "equity_curve": [
        {"date": "2024-01-01", "value": 1000000},
        {"date": "2024-01-02", "value": 1100000},
        {"date": "2024-01-03", "value": 1200000},
    ],
    "profit_pct": 20.0,
    "sharpe_ratio": 1.5,
    "max_drawdown_pct": -5.0,
    "win_rate": 60.0,
    "trades": 10,
    "profit_factor": 2.0,
}
sub2 = {
    "strategy": "rsi",
    "equity_curve": [
        {"date": "2024-01-01", "value": 1000000},
        {"date": "2024-01-02", "value": 1050000},
        {"date": "2024-01-03", "value": 1080000},
    ],
    "profit_pct": 8.0,
    "sharpe_ratio": 1.0,
    "max_drawdown_pct": -3.0,
    "win_rate": 50.0,
    "trades": 5,
    "profit_factor": 1.5,
}

combined = e._combine([sub1, sub2])
# 1/3: 0.6*1200000 + 0.4*1080000 = 720000 + 432000 = 1152000
assert abs(combined["equity_curve"][2]["value"] - 1152000) < 1.0
# 收益：(1152000 / (0.6*1000000 + 0.4*1000000) - 1) * 100 = 15.2%
assert abs(combined["profit_pct"] - 15.2) < 0.1
# 交易数 = 10 + 5
assert combined["trades"] == 15
# 3 个 equity_curve 点
assert len(combined["equity_curve"]) == 3
print("COMBINE_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("COMBINE_OK", out)

    def test_combine_handles_errors(self):
        """子策略有 error 时被忽略。"""
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine

e = MultiStrategyEngine(strategies=["sma", "rsi"], symbol="X")

# 一个有 error，一个 OK
sub1 = {"strategy": "sma", "error": "no data"}
sub2 = {
    "strategy": "rsi",
    "equity_curve": [
        {"date": "2024-01-01", "value": 1000000},
        {"date": "2024-01-02", "value": 1050000},
    ],
    "profit_pct": 5.0, "sharpe_ratio": 1.0, "max_drawdown_pct": -2.0,
    "win_rate": 50.0, "trades": 5, "profit_factor": 1.2,
}

combined = e._combine([sub1, sub2])
# 浮点精度：5.000000000000004 ~ 5.0
assert abs(combined["profit_pct"] - 5.0) < 1e-6  # 只有 sub2 有效
assert combined["n_strategies"] == 1
print("ERR_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("ERR_OK", out)

    def test_combine_all_invalid_returns_zero(self):
        """所有子策略都失败时返回零值。"""
        rc, out, err = _run("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.multi_strategy import MultiStrategyEngine

e = MultiStrategyEngine(strategies=["sma"], symbol="X", initial_cash=500000)
combined = e._combine([{"strategy": "sma", "error": "x"}])
assert combined["profit_pct"] == 0.0
assert combined["final_value"] == 500000
assert combined["n_strategies"] == 0
print("ALLERR_OK")
""")
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("ALLERR_OK", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
