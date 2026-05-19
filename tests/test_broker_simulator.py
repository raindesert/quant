"""Tests for SimulatorBroker."""
import unittest
from datetime import datetime

from broker.simulator import SimulatorBroker


class TestBuyExecutionWithCommission(unittest.TestCase):
    def test_buy_execution_commission_deducted(self):
        """Buy order deducts commission correctly (min_commission=5.0 applies when rate × value < 5)."""
        broker = SimulatorBroker(initial_cash=1_000_000, commission=0.0003)
        # trade_value = 1000×10 = 10000, commission = max(10000×0.0003, 5.0) = 5.0
        success = broker.buy("TEST", price=10.0, quantity=1000)

        self.assertTrue(success)
        total_cost = 1000 * 10.0 + 5.0  # 10005.0
        self.assertAlmostEqual(broker.cash, 1_000_000 - total_cost, places=2)
        self.assertEqual(broker.get_position("TEST"), 1000)

    def test_buy_rejected_when_insufficient_cash(self):
        """Buy fails when cash cannot cover total cost including commission."""
        broker = SimulatorBroker(initial_cash=1000, commission=0.0003)
        success = broker.buy("TEST", price=100.0, quantity=100)  # needs 10005

        self.assertFalse(success)
        self.assertEqual(broker.cash, 1000)
        self.assertEqual(broker.get_position("TEST"), 0)


class TestSellExecutionWithCommission(unittest.TestCase):
    def test_sell_execution_commission_deducted(self):
        """Sell order deducts commission + stamp_tax correctly."""
        broker = SimulatorBroker(initial_cash=1_000_000, commission=0.0003, stamp_tax=0.001)
        broker.buy("TEST", price=10.0, quantity=1000)

        cash_before = broker.cash
        success = broker.sell("TEST", price=12.0, quantity=1000)

        self.assertTrue(success)
        # trade_value = 12000, commission = max(12000×0.0003, 5.0) = 5.0, stamp = max(12000×0.001, 5.0) = 12.0
        proceeds = 12000 - 5.0 - 12.0  # 11983.0
        self.assertAlmostEqual(broker.cash, cash_before + proceeds, places=2)
        self.assertEqual(broker.get_position("TEST"), 0)


class TestBuyRejectedWhenInsufficientCash(unittest.TestCase):
    def test_buy_rejected_properly(self):
        """Verify proper handling when cash is insufficient."""
        broker = SimulatorBroker(initial_cash=500, commission=0.0003)
        success = broker.buy("TEST", price=50.0, quantity=100)

        self.assertFalse(success)
        self.assertEqual(broker.cash, 500)
        self.assertEqual(broker.get_position("TEST"), 0)


if __name__ == "__main__":
    unittest.main()
