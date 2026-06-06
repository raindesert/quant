"""Tests for history_store (SQLite-backed backtest history persistence).

Uses a per-test temporary DB via QUANT_HISTORY_DB env var — never touches
the real data/history.db.
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_module(tmpdir: str):
    """Reimport history_store with QUANT_HISTORY_DB pointing at a fresh DB."""
    os.environ["QUANT_HISTORY_DB"] = str(Path(tmpdir) / "history_test.db")
    sys.modules.pop("history_store", None)
    return importlib.import_module("history_store")


class TestHistoryStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.hs = _fresh_module(self._tmp)
        self.hs.init_db()

    def tearDown(self):
        # Don't clear() — each test should leave DB as-is, but force module
        # to re-init next time by clearing _initialized.
        self.hs._initialized = False
        try:
            os.remove(Path(self._tmp) / "history_test.db")
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self._tmp)
        except OSError:
            pass

    # ============== CRUD ==============

    def test_save_and_get_roundtrip(self):
        new_id = self.hs.save(
            "backtest",
            {"symbol": "000001.SZ", "strategy": "sma",
             "profit_pct": 5.2, "sharpe_ratio": 1.1, "trades": 10,
             "max_drawdown_pct": 3.4, "win_rate": 0.6},
        )
        self.assertGreater(new_id, 0)
        rec = self.hs.get(new_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["symbol"], "000001.SZ")
        self.assertEqual(rec["strategy"], "sma")
        self.assertAlmostEqual(rec["profit_pct"], 5.2)
        self.assertEqual(rec["mode"], "backtest")
        self.assertEqual(rec["summary"]["symbol"], "000001.SZ")

    def test_save_with_extra(self):
        new_id = self.hs.save(
            "multi_strategy",
            {"symbol": "600000.SH", "strategy": "rsi", "profit_pct": -1.0},
            extra={"sub_strategies": ["rsi", "macd", "bollinger"]},
        )
        rec = self.hs.get(new_id)
        self.assertEqual(rec["extra"]["sub_strategies"],
                         ["rsi", "macd", "bollinger"])

    def test_list_recent_ordering(self):
        # 3 saves; list_recent should return newest first
        self.hs.save("backtest", {"symbol": "A", "profit_pct": 1.0})
        self.hs.save("backtest", {"symbol": "B", "profit_pct": 2.0})
        self.hs.save("backtest", {"symbol": "C", "profit_pct": 3.0})
        recs = self.hs.list_recent()
        self.assertEqual(len(recs), 3)
        self.assertEqual([r["symbol"] for r in recs], ["C", "B", "A"])
        # profit_pct values should also be in descending time order
        self.assertEqual([r["profit_pct"] for r in recs], [3.0, 2.0, 1.0])

    def test_list_for_symbol(self):
        self.hs.save("backtest", {"symbol": "000001.SZ", "profit_pct": 1.0})
        self.hs.save("backtest", {"symbol": "600000.SH", "profit_pct": 2.0})
        self.hs.save("backtest", {"symbol": "000001.SZ", "profit_pct": 3.0})
        recs = self.hs.list_for_symbol("000001.SZ")
        self.assertEqual(len(recs), 2)
        self.assertTrue(all(r["symbol"] == "000001.SZ" for r in recs))

    def test_delete(self):
        new_id = self.hs.save("backtest", {"symbol": "X", "profit_pct": 1.0})
        self.assertTrue(self.hs.delete(new_id))
        self.assertIsNone(self.hs.get(new_id))
        # second delete returns False
        self.assertFalse(self.hs.delete(new_id))

    def test_clear(self):
        for i in range(5):
            self.hs.save("backtest", {"symbol": f"S{i}", "profit_pct": float(i)})
        n = self.hs.clear()
        self.assertEqual(n, 5)
        self.assertEqual(len(self.hs.list_recent()), 0)

    # ============== stats() ==============

    def test_stats_empty(self):
        s = self.hs.stats()
        self.assertEqual(s["total"], 0)
        self.assertNotIn("best", s)

    def test_stats_aggregates(self):
        self.hs.save("backtest", {"symbol": "A", "strategy": "sma",
                                   "profit_pct": 10.0, "sharpe_ratio": 2.0})
        self.hs.save("backtest", {"symbol": "A", "strategy": "rsi",
                                   "profit_pct": -5.0, "sharpe_ratio": 0.5})
        self.hs.save("backtest", {"symbol": "B", "strategy": "sma",
                                   "profit_pct": 3.0, "sharpe_ratio": 1.0})
        s = self.hs.stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["distinct_symbols"], 2)
        self.assertEqual(s["distinct_strategies"], 2)
        self.assertEqual(s["profitable"], 2)
        self.assertAlmostEqual(s["avg_profit_pct"], (10 - 5 + 3) / 3)
        self.assertEqual(s["best"]["profit_pct"], 10.0)
        self.assertEqual(s["worst"]["profit_pct"], -5.0)
        self.assertEqual(s["best"]["symbol"], "A")
        self.assertEqual(s["worst"]["strategy"], "rsi")

    # ============== FIFO cap ==============

    def test_fifo_cap_enforced(self):
        # Inject a tiny cap (3) by patching _DEFAULT_LIMIT
        with mock.patch.object(self.hs, "_DEFAULT_LIMIT", 3):
            for i in range(7):
                self.hs.save("backtest", {"symbol": f"S{i}", "profit_pct": float(i)})
        recs = self.hs.list_recent(limit=100)
        self.assertEqual(len(recs), 3)
        # Newest 3: indices 6, 5, 4
        self.assertEqual([r["profit_pct"] for r in recs], [6.0, 5.0, 4.0])

    # ============== Cross-session persistence ==============

    def test_cross_session_persistence(self):
        """The killer test: data saved by one module instance should survive
        a fresh import (simulating Streamlit page reload / app restart)."""
        id1 = self.hs.save("backtest", {"symbol": "PERSIST", "profit_pct": 7.7})
        # Drop module and reimport with same DB path
        hs2 = _fresh_module(self._tmp)
        rec = hs2.get(id1)
        self.assertIsNotNone(rec, "Record should survive module reimport")
        self.assertEqual(rec["symbol"], "PERSIST")
        self.assertAlmostEqual(rec["profit_pct"], 7.7)
        # list_recent should also see it
        recs = hs2.list_recent()
        self.assertTrue(any(r["id"] == id1 for r in recs))

    # ============== Edge cases ==============

    def test_save_rejects_non_dict_summary(self):
        with self.assertRaises(TypeError):
            self.hs.save("backtest", "not a dict")  # type: ignore[arg-type]

    def test_missing_fields_default_gracefully(self):
        """Empty summary → record should have safe defaults, not crash."""
        new_id = self.hs.save("backtest", {})
        rec = self.hs.get(new_id)
        self.assertEqual(rec["symbol"], "?")
        self.assertEqual(rec["strategy"], "?")
        self.assertEqual(rec["profit_pct"], 0.0)
        self.assertEqual(rec["trades"], 0)

    def test_corrupt_json_in_db_does_not_crash(self):
        """If summary_json is hand-corrupted in DB, get() should return {} not raise."""
        new_id = self.hs.save("backtest", {"symbol": "X"})
        # Bypass save() and write garbage directly
        self.hs._connect().execute(
            "UPDATE backtest_runs SET summary_json = ? WHERE id = ?",
            ("{not valid json", new_id),
        ).connection.commit()
        rec = self.hs.get(new_id)
        self.assertEqual(rec["summary"], {})
        # And list_recent should still work
        self.assertEqual(len(self.hs.list_recent()), 1)

    def test_init_db_idempotent(self):
        """Calling init_db() multiple times should be safe."""
        self.hs.init_db()
        self.hs.init_db()
        # Save still works
        new_id = self.hs.save("backtest", {"symbol": "Z"})
        self.assertIsNotNone(self.hs.get(new_id))


if __name__ == "__main__":
    unittest.main()
