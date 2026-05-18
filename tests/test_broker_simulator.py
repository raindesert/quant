"""Tests for SimulatorBroker."""
import unittest
from datetime import datetime

from broker.simulator import SimulatorBroker


class TestBuyExecutionWithCommission(unittest.TestCase):
    def test_buy_execution_commission_deducted(self):
        """Buy order deducts commission correctly."""
        broker = SimulatorBroker(initial_cash=1_000_000, commission=0.0003)
        success = broker.buy("TEST", price=10.0, quantity=1000)

        self.assertTrue(success)
        expected_cost = 1000 * 10.0 * (1 + 0.0003)  # 10030.0
        self.assertAlmostEqual(broker.cash, 1_000_000 - expected_cost, places=2)
        self.assertEqual(broker.get_position("TEST"), 1000)

    def test_buy_rejected_when_insufficient_cash(self):
        """Buy fails when cash cannot cover total cost including commission."""
        broker = SimulatorBroker(initial_cash=1000, commission=0.0003)
        success = broker.buy("TEST", price=100.0, quantity=100)  # needs 10030

        self.assertFalse(success)
        self.assertEqual(broker.cash, 1000)
        self.assertEqual(broker.get_position("TEST"), 0)


class TestSellExecutionWithCommission(unittest.TestCase):
    def test_sell_execution_commission_deducted(self):
        """Sell order deducts commission correctly."""
        broker = SimulatorBroker(initial_cash=1_000_000, commission=0.0003)
        broker.buy("TEST", price=10.0, quantity=1000)

        cash_before = broker.cash
        success = broker.sell("TEST", price=12.0, quantity=1000)

        self.assertTrue(success)
        expected_proceeds = 1000 * 12.0 * (1 - 0.0003)  # 11996.4
        self.assertAlmostEqual(broker.cash, cash_before + expected_proceeds, places=2)
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