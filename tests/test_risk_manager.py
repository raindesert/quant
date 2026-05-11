"""单元测试 — 风控管理器。"""
from __future__ import annotations

import pytest

from risk.manager import RiskCheckResult, RiskManager


@pytest.fixture
def manager():
    return RiskManager(
        max_position_pct=0.25,
        max_positions=3,
        max_drawdown_pct=0.20,
        max_daily_loss_pct=0.03,
        max_stock_loss_pct=0.10,
        enabled=True,
    )


@pytest.fixture
def disabled_manager():
    return RiskManager(enabled=False)


class TestRiskCheckResult:
    def test_allowed(self):
        r = RiskCheckResult(allowed=True, adjusted_quantity=100)
        assert r.allowed is True
        assert r.adjusted_quantity == 100

    def test_denied(self):
        r = RiskCheckResult(allowed=False, reason="超限", adjusted_quantity=0)
        assert r.allowed is False
        assert "超限" in r.reason


class TestRiskManagerInit:
    def test_default_values(self):
        m = RiskManager()
        assert m.max_position_pct == 0.25
        assert m.max_positions == 10
        assert m.max_drawdown_pct == 0.20
        assert m.enabled is True

    def test_custom_values(self, manager):
        assert manager.max_position_pct == 0.25
        assert manager.max_positions == 3
        assert manager.max_drawdown_pct == 0.20
        assert manager.max_daily_loss_pct == 0.03
        assert manager.max_stock_loss_pct == 0.10


class TestReset:
    def test_reset_clears_state(self, manager):
        manager._peak_value = 1_500_000
        manager._circuit_breaker = True
        manager._circuit_breaker_reason = "test"
        manager._stock_entry_values = {"000001.SZ": 10.0}
        manager.reset()
        assert manager._peak_value == 0.0
        assert manager._circuit_breaker is False
        assert manager._circuit_breaker_reason == ""
        assert len(manager._stock_entry_values) == 0


class TestCheckBuy:
    def test_normal_buy_allowed(self, manager):
        result = manager.check_buy(
            symbol="000001.SZ",
            price=10.0,
            quantity=1000,
            total_value=1_000_000,
            cash=500_000,
            positions={},
            last_prices={},
        )
        assert result.allowed is True
        assert result.adjusted_quantity == 1000

    def test_max_positions_reached(self, manager):
        positions = {"000001.SZ": 1000, "600000.SH": 500, "300001.SZ": 800}
        result = manager.check_buy(
            symbol="688001.SH",
            price=10.0,
            quantity=1000,
            total_value=1_000_000,
            cash=500_000,
            positions=positions,
            last_prices={"000001.SZ": 10.0, "600000.SH": 20.0, "300001.SZ": 15.0},
        )
        assert result.allowed is False

    def test_add_to_existing_position_allowed(self, manager):
        positions = {"000001.SZ": 1000, "600000.SH": 500}
        result = manager.check_buy(
            symbol="000001.SZ",
            price=10.0,
            quantity=1000,
            total_value=1_000_000,
            cash=500_000,
            positions=positions,
            last_prices={"000001.SZ": 10.0, "600000.SH": 20.0},
        )
        assert result.allowed is True

    def test_position_pct_adjusted(self, manager):
        result = manager.check_buy(
            symbol="000001.SZ",
            price=10.0,
            quantity=50000,
            total_value=1_000_000,
            cash=900_000,
            positions={},
            last_prices={},
        )
        assert result.allowed is True
        assert result.adjusted_quantity < 50000

    def test_circuit_breaker_blocks_buy(self, manager):
        manager._circuit_breaker = True
        manager._circuit_breaker_reason = "回撤过大"
        result = manager.check_buy(
            symbol="000001.SZ",
            price=10.0,
            quantity=1000,
            total_value=1_000_000,
            cash=500_000,
            positions={},
            last_prices={},
        )
        assert result.allowed is False

    def test_disabled_manager_allows(self, disabled_manager):
        result = disabled_manager.check_buy(
            symbol="000001.SZ",
            price=10.0,
            quantity=50000,
            total_value=1_000_000,
            cash=900_000,
            positions={},
            last_prices={},
        )
        assert result.allowed is True
        assert result.adjusted_quantity == 50000


class TestCheckSell:
    def test_normal_sell_allowed(self, manager):
        result = manager.check_sell(
            symbol="000001.SZ",
            price=11.0,
            quantity=1000,
            entry_price=10.0,
            total_value=1_000_000,
        )
        assert result.allowed is True
        assert result.adjusted_quantity == 1000

    def test_loss_exceeds_threshold_forced_sell(self, manager):
        result = manager.check_sell(
            symbol="000001.SZ",
            price=8.5,
            quantity=1000,
            entry_price=10.0,
            total_value=1_000_000,
        )
        assert result.allowed is True
        assert "强平" in result.reason

    def test_loss_within_threshold_allowed(self, manager):
        result = manager.check_sell(
            symbol="000001.SZ",
            price=9.2,
            quantity=1000,
            entry_price=10.0,
            total_value=1_000_000,
        )
        assert result.allowed is True
        assert result.reason == ""

    def test_disabled_manager_allows_sell(self, disabled_manager):
        result = disabled_manager.check_sell(
            symbol="000001.SZ",
            price=8.0,
            quantity=1000,
            entry_price=10.0,
            total_value=1_000_000,
        )
        assert result.allowed is True


class TestUpdatePortfolioState:
    def test_peak_value_updated(self, manager):
        manager.update_portfolio_state(1_200_000, {}, {})
        assert manager._peak_value == 1_200_000
        manager.update_portfolio_state(1_100_000, {}, {})
        assert manager._peak_value == 1_200_000
        manager.update_portfolio_state(1_300_000, {}, {})
        assert manager._peak_value == 1_300_000

    def test_drawdown_circuit_breaker(self, manager):
        manager.update_portfolio_state(1_000_000, {}, {})
        manager._peak_value = 1_000_000
        manager.update_portfolio_state(790_000, {}, {})
        assert manager._circuit_breaker is True
        assert "回撤" in manager._circuit_breaker_reason

    def test_daily_loss_circuit_breaker(self, manager):
        manager._prev_day_value = 1_000_000
        manager.update_portfolio_state(960_000, {}, {})
        assert manager._circuit_breaker is True
        assert "单日亏损" in manager._circuit_breaker_reason

    def test_no_circuit_breaker_within_limits(self, manager):
        manager.update_portfolio_state(1_000_000, {}, {})
        manager.update_portfolio_state(990_000, {}, {})
        assert manager._circuit_breaker is False


class TestOnNewDay:
    def test_circuit_breaker_resets(self, manager):
        manager._circuit_breaker = True
        manager._circuit_breaker_reason = "test"
        manager.on_new_day()
        assert manager._circuit_breaker is False
        assert manager._circuit_breaker_reason == ""


class TestGetStatus:
    def test_status_dict(self, manager):
        status = manager.get_status()
        assert "enabled" in status
        assert "circuit_breaker" in status
        assert "max_position_pct" in status
        assert "max_positions" in status
        assert "max_drawdown_pct" in status
        assert "max_daily_loss_pct" in status
        assert "max_stock_loss_pct" in status
        assert status["enabled"] is True
        assert status["circuit_breaker"] is False
