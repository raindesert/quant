"""P2-4 HTML 报告测试 — subprocess 隔离避免触发 matplotlib 真实 import。"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _run_in_subprocess(code: str) -> tuple[int, str, str]:
    """在独立 python 进程里跑代码，避开测试间的 sys.modules 污染。"""
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode, r.stdout, r.stderr


def _run_in_subprocess_env(code: str, env: dict) -> tuple[int, str, str]:
    """在独立 python 进程里跑代码（用 os.environ 传 path 避免 % 格式化冲突）。"""
    import os
    full_env = {**os.environ, **env}
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, env=full_env,
    )
    return r.returncode, r.stdout, r.stderr


QUANT_ROOT = str(Path(__file__).resolve().parent.parent)


class TestExportHtmlReport(unittest.TestCase):
    def test_basic_html_generated(self):
        # 不在 _run_in_subprocess 用 % 格式化（子代码内 % 会冲突），
        # 改成 os.environ 传 path
        import os
        env = {"QUANT_ROOT": str(Path(__file__).resolve().parent.parent)}
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_report

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "report.html"
    summary = {
        "symbol": "000001.SZ",
        "strategy": "sma",
        "profit_pct": 25.5,
        "annual_return": 12.3,
        "sharpe_ratio": 1.85,
        "max_drawdown_pct": -8.2,
        "win_rate": 60.0,
        "trades": 42,
        "profit_factor": 2.1,
        "final_value": 1255000,
    }
    export_html_report(summary, p, include_charts=False)
    html = p.read_text(encoding="utf-8")
    assert "<html" in html
    assert "000001.SZ" in html
    assert "+25.50%" in html
    assert "夏普比率" in html
    assert "1.85" in html
    print("HTML_OK")
""", env=env)
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("HTML_OK", out)

    def test_html_with_trades_table(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_report

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "r.html"
    summary = {"symbol": "X", "strategy": "Y", "profit_pct": 5.0,
               "sharpe_ratio": 1.0, "max_drawdown_pct": -3.0,
               "win_rate": 50.0, "trades": 2, "profit_factor": 1.5,
               "final_value": 1050000}
    trades = [
        {"date": "2024-01-02", "action": "BUY", "price": 10.5, "shares": 1000, "amount": 10500},
        {"date": "2024-01-10", "action": "SELL", "price": 11.2, "shares": 1000, "amount": 11200},
    ]
    export_html_report(summary, p, trades=trades, include_charts=False)
    html = p.read_text(encoding="utf-8")
    assert "交易明细" in html
    assert "2024-01-02" in html
    assert "BUY" in html
    print("TRADES_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("TRADES_OK", out)

    def test_html_truncates_long_trades(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_report

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "r.html"
    summary = {"symbol": "X", "strategy": "Y", "profit_pct": 0,
               "sharpe_ratio": 0, "max_drawdown_pct": 0,
               "win_rate": 0, "trades": 100, "profit_factor": 0,
               "final_value": 1000000}
    trades = [{"date": f"2024-01-{i:02d}", "action": "BUY", "price": 10, "shares": 100, "amount": 1000}
              for i in range(1, 80)]
    export_html_report(summary, p, trades=trades, include_charts=False)
    html = p.read_text(encoding="utf-8")
    assert "仅显示前 50 条" in html
    print("TRUNC_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("TRUNC_OK", out)

    def test_html_no_charts(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_report

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "r.html"
    summary = {"symbol": "X", "strategy": "Y", "profit_pct": 1.0,
               "sharpe_ratio": 0.5, "max_drawdown_pct": -2.0,
               "win_rate": 50.0, "trades": 1, "profit_factor": 1.0,
               "final_value": 1010000}
    export_html_report(summary, p, include_charts=False)
    html = p.read_text(encoding="utf-8")
    assert "图表" not in html  # 没图
    assert "关键指标" in html
    print("NOCHART_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("NOCHART_OK", out)

    def test_html_with_charts(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_report
import datetime

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "r.html"
    equity = [
        {"date": "2024-01-01", "value": 1000000},
        {"date": "2024-01-02", "value": 1010000},
        {"date": "2024-01-03", "value": 1005000},
    ]
    summary = {"symbol": "X", "strategy": "Y", "profit_pct": 1.0,
               "sharpe_ratio": 0.5, "max_drawdown_pct": -0.5,
               "win_rate": 50.0, "trades": 1, "profit_factor": 1.0,
               "final_value": 1005000,
               "equity_curve": equity}
    export_html_report(summary, p, include_charts=True)
    html = p.read_text(encoding="utf-8")
    assert "data:image/png;base64," in html
    print("CHART_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("CHART_OK", out)


class TestExportHtmlComparison(unittest.TestCase):
    def test_comparison_basic(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
import tempfile
from pathlib import Path
from backtest.output import export_html_comparison

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "cmp.html"
    results = [
        {"strategy": "sma", "symbol": "000001.SZ", "profit_pct": 10.0,
         "annual_return": 5.0, "sharpe_ratio": 1.2, "max_drawdown_pct": -8.0,
         "win_rate": 60.0, "trades": 20, "profit_factor": 1.5},
        {"strategy": "rsi", "symbol": "000001.SZ", "profit_pct": -2.0,
         "annual_return": -1.0, "sharpe_ratio": 0.3, "max_drawdown_pct": -12.0,
         "win_rate": 45.0, "trades": 30, "profit_factor": 0.9},
    ]
    export_html_comparison(results, p, title="对比测试")
    html = p.read_text(encoding="utf-8")
    assert "策略对比报告" in html or "对比测试" in html
    assert "sma" in html
    assert "rsi" in html
    print("CMP_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("CMP_OK", out)

    def test_comparison_empty_raises(self):
        rc, out, err = _run_in_subprocess_env("""\
import sys, os
sys.path.insert(0, os.environ["QUANT_ROOT"])
from backtest.output import export_html_comparison
try:
    export_html_comparison([], "/tmp/nope.html")
    raise AssertionError("应抛 ValueError")
except ValueError as e:
    assert "results 不能为空" in str(e)
    print("EMPTY_OK")
""", env={"QUANT_ROOT": QUANT_ROOT})
        self.assertEqual(rc, 0, f"stdout={out}\nstderr={err}")
        self.assertIn("EMPTY_OK", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
