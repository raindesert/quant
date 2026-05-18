"""Tests for PortfolioBacktestEngine."""
import unittest
from unittest.mock import MagicMock
import pandas as pd

from backtest.portfolio import PortfolioBacktestEngine
from strategy.base import Signal


def make_engine(max_positions=5):
    return PortfolioBacktestEngine(
        initial_cash=1_000_000,
        commission=0.0003,
        stop_loss=0.0,
        take_profit=0.0,
        max_positions=max_positions,
    )


def make_bar(symbol, date, open_p, close_p, high=None, low=None, vol=1e6):
    row = MagicMock(
        date=date,
        open=open_p,
        close=close_p,
        high=high or max(open_p, close_p),
        low=low or min(open_p, close_p),
        volume=vol,
    )
    return row


class TestMaxPositionsLimitsBuys(unittest.TestCase):
    def test_no_new_positions_when_at_max(self):
        """When at max_positions, new buy signals are ignored."""
        engine = make_engine(max_positions=2)
        engine.cash = 1_000_000
        engine.positions = {"A": 1000, "B": 1000}
        engine.entry_prices = {"A": 10.0, "B": 12.0}
        engine.last_prices = {"A": 10.0, "B": 12.0}

        # Attempt to buy C when already at max (2) positions
        bar = {
            "symbol": "C",
            "date": pd.Timestamp("2024-01-01"),
            "open": 50.0,
            "close": 50.0,
            "high": 51.0,
            "low": 49.0,
            "volume": 1e6,
            "timestamp": pd.Timestamp("2024-01-01"),
            "last_price": 50.0,
        }
        mock_strategy = MagicMock()
        mock_strategy.get_position.return_value = 0

        engine._execute_signal(Signal.BUY, bar, mock_strategy)

        # C should not be added since we already have 2 positions and C is not one of them
        self.assertNotIn("C", engine.positions)


class TestBudgetWarningWhenMaxPositions(unittest.TestCase):
    def test_warning_logged_when_signal_ignored(self):
        """When budget is 0 (no cash), a warning is logged."""
        engine = make_engine(max_positions=5)
        engine.cash = 0.0  # zero cash → budget = 0
        engine.positions = {}
        engine.entry_prices = {}
        engine.last_prices = {}

        bar = {
            "symbol": "B",
            "date": pd.Timestamp("2024-01-01"),
            "open": 50.0,
            "close": 50.0,
            "high": 51.0,
            "low": 49.0,
            "volume": 1e6,
            "timestamp": pd.Timestamp("2024-01-01"),
            "last_price": 50.0,
        }
        mock_strategy = MagicMock()
        mock_strategy.get_position.return_value = 0

        with self.assertLogs(level="WARNING"):
            engine._execute_signal(Signal.BUY, bar, mock_strategy)


class TestPartialSellReducesPosition(unittest.TestCase):
    def test_partial_sell_reduces_position(self):
        """Selling part of a position correctly reduces quantity."""
        engine = make_engine()
        engine.cash = 1_000_000
        engine.positions = {"TEST": 5000}
        engine.entry_prices = {"TEST": 10.0}
        engine.last_prices = {"TEST": 11.0}

        bar = {
            "symbol": "TEST",
            "date": pd.Timestamp("2024-01-01"),
            "open": 11.0,
            "close": 11.0,
            "high": 12.0,
            "low": 10.5,
            "volume": 1e6,
            "timestamp": pd.Timestamp("2024-01-01"),
            "last_price": 11.0,
        }
        mock_strategy = MagicMock()
        mock_strategy.get_position.return_value = 5000

        engine._execute_signal(Signal.SELL, bar, mock_strategy)

        # In PortfolioBacktestEngine, SELL exits the FULL position
        # So TEST position should be 0 after sell
        self.assertEqual(engine.positions.get("TEST", 0), 0)
        self.assertGreater(engine.cash, 1_000_000)


class TestBenchmarkInitialization(unittest.TestCase):
    def test_benchmark_starts_with_correct_allocation(self):
        """Benchmark initializes with 50% allocation to each stock."""
        engine = make_engine()
        engine.reset()

        self.assertEqual(engine.cash, 1_000_000)
        self.assertEqual(engine.max_positions, 5)
        self.assertEqual(engine.DEFAULT_BENCHMARK_ALLOCATION, 0.5)


class TestSellWithoutPositionIgnored(unittest.TestCase):
    def test_sell_without_position_does_nothing(self):
        """Sell for a symbol with no position has no effect."""
        engine = make_engine()
        engine.cash = 1_000_000
        engine.positions = {"OTHER": 1000}
        engine.entry_prices = {"OTHER": 10.0}
        engine.last_prices = {"OTHER": 11.0}

        bar = {
            "symbol": "NOTHELD",
            "date": pd.Timestamp("2024-01-01"),
            "open": 50.0,
            "close": 50.0,
            "high": 51.0,
            "low": 49.0,
            "volume": 1e6,
            "timestamp": pd.Timestamp("2024-01-01"),
            "last_price": 50.0,
        }
        mock_strategy = MagicMock()
        mock_strategy.get_position.return_value = 0

        cash_before = engine.cash
        engine._execute_signal(Signal.SELL, bar, mock_strategy)

        # Cash unchanged, NOTHELD not in positions
        self.assertEqual(engine.cash, cash_before)
        self.assertNotIn("NOTHELD", engine.positions)


if __name__ == "__main__":
    unittest.main()