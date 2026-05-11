"""单元测试 — 回测引擎核心计算逻辑。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from backtest.base import BaseBacktestEngine
from strategy.base import Signal


class EngineForTest(BaseBacktestEngine):
    """用于测试的引擎子类，暴露基类的受保护方法。"""

    def run(self, *args, **kwargs):
        pass


@pytest.fixture
def engine():
    return EngineForTest(
        initial_cash=1_000_000,
        commission=0.0003,
        stamp_tax=0.001,
        min_commission=5.0,
    )


class TestCalcBuyCost:
    def test_normal_commission(self, engine):
        price, qty = 10.0, 1000
        cost = engine._calc_buy_cost(price, qty)
        raw = price * qty
        expected_commission = max(raw * 0.0003, 5.0)
        assert cost == raw + expected_commission

    def test_min_commission(self, engine):
        price, qty = 10.0, 100
        cost = engine._calc_buy_cost(price, qty)
        raw = price * qty
        expected_commission = max(raw * 0.0003, 5.0)
        assert expected_commission == 5.0
        assert cost == raw + 5.0

    def test_large_order(self, engine):
        price, qty = 50.0, 10000
        cost = engine._calc_buy_cost(price, qty)
        raw = price * qty
        expected_commission = max(raw * 0.0003, 5.0)
        assert cost == raw + expected_commission
        assert expected_commission > 5.0


class TestCalcSellProceeds:
    def test_normal_sell(self, engine):
        price, qty = 10.0, 1000
        proceeds = engine._calc_sell_proceeds(price, qty)
        raw = price * qty
        expected_commission = max(raw * 0.0003, 5.0)
        expected_stamp = raw * 0.001
        assert proceeds == raw - expected_commission - expected_stamp

    def test_min_commission_sell(self, engine):
        price, qty = 10.0, 100
        proceeds = engine._calc_sell_proceeds(price, qty)
        raw = price * qty
        expected_commission = max(raw * 0.0003, 5.0)
        expected_stamp = raw * 0.001
        assert expected_commission == 5.0
        assert proceeds == raw - 5.0 - expected_stamp


class TestSlippage:
    def test_no_slippage(self, engine):
        engine.slippage = 0.0
        assert engine._apply_slippage(10.0, "buy") == 10.0
        assert engine._apply_slippage(10.0, "sell") == 10.0

    def test_percent_slippage_buy(self):
        eng = EngineForTest(slippage=0.001, slippage_type="percent")
        result = eng._apply_slippage(10.0, "buy")
        assert result == pytest.approx(10.01, abs=0.001)

    def test_percent_slippage_sell(self):
        eng = EngineForTest(slippage=0.001, slippage_type="percent")
        result = eng._apply_slippage(10.0, "sell")
        assert result == pytest.approx(9.99, abs=0.001)

    def test_fixed_slippage_buy(self):
        eng = EngineForTest(slippage=0.05, slippage_type="fixed")
        assert eng._apply_slippage(10.0, "buy") == 10.05

    def test_fixed_slippage_sell(self):
        eng = EngineForTest(slippage=0.05, slippage_type="fixed")
        assert eng._apply_slippage(10.0, "sell") == 9.95

    def test_percent_sell_floor(self):
        eng = EngineForTest(slippage=0.99, slippage_type="percent")
        result = eng._apply_slippage(10.0, "sell")
        assert result >= 0.01


class TestLimitUpLimitDown:
    def test_main_board_limit_up(self):
        eng = EngineForTest(check_limit=True)
        eng.last_bars["000001.SZ"] = {"close": 10.0}
        bar = {"close": 11.0}
        assert eng._is_limit_up("000001.SZ", bar) is True

    def test_main_board_not_limit_up(self):
        eng = EngineForTest(check_limit=True)
        eng.last_bars["000001.SZ"] = {"close": 10.0}
        bar = {"close": 10.5}
        assert eng._is_limit_up("000001.SZ", bar) is False

    def test_main_board_limit_down(self):
        eng = EngineForTest(check_limit=True)
        eng.last_bars["000001.SZ"] = {"close": 10.0}
        bar = {"close": 9.0}
        assert eng._is_limit_down("000001.SZ", bar) is True

    def test_kcb_limit_threshold(self):
        assert EngineForTest._get_limit_threshold("688001.SH") == 0.20

    def test_cyb_limit_threshold(self):
        assert EngineForTest._get_limit_threshold("300001.SZ") == 0.20

    def test_main_limit_threshold(self):
        assert EngineForTest._get_limit_threshold("000001.SZ") == 0.10
        assert EngineForTest._get_limit_threshold("600000.SH") == 0.10

    def test_no_prev_close(self):
        eng = EngineForTest(check_limit=True)
        bar = {"close": 11.0}
        assert eng._is_limit_up("000001.SZ", bar) is False
        assert eng._is_limit_down("000001.SZ", bar) is False

    def test_check_limit_disabled(self):
        eng = EngineForTest(check_limit=False)
        eng.last_bars["000001.SZ"] = {"close": 10.0}
        bar = {"close": 11.0}
        assert eng._is_limit_up("000001.SZ", bar) is False


class TestTPlus1:
    def test_same_day_sell_blocked(self):
        eng = EngineForTest(enforce_t_plus_1=True)
        today = datetime(2025, 1, 6)
        eng.entry_dates["000001.SZ"] = today
        assert eng._check_t_plus_1("000001.SZ", today) is False

    def test_next_day_sell_allowed(self):
        eng = EngineForTest(enforce_t_plus_1=True)
        today = datetime(2025, 1, 6)
        tomorrow = datetime(2025, 1, 7)
        eng.entry_dates["000001.SZ"] = today
        assert eng._check_t_plus_1("000001.SZ", tomorrow) is True

    def test_t1_disabled(self):
        eng = EngineForTest(enforce_t_plus_1=False)
        today = datetime(2025, 1, 6)
        eng.entry_dates["000001.SZ"] = today
        assert eng._check_t_plus_1("000001.SZ", today) is True

    def test_no_entry_date(self):
        eng = EngineForTest(enforce_t_plus_1=True)
        today = datetime(2025, 1, 6)
        assert eng._check_t_plus_1("000001.SZ", today) is True


class TestMaxDrawdown:
    def test_no_drawdown(self, engine):
        engine.equity_curve = [
            {"value": 100}, {"value": 110}, {"value": 120},
        ]
        dd, dd_pct = engine._calc_max_drawdown()
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_simple_drawdown(self, engine):
        engine.equity_curve = [
            {"value": 100}, {"value": 120}, {"value": 90}, {"value": 110},
        ]
        dd, dd_pct = engine._calc_max_drawdown()
        assert dd == 30.0
        assert dd_pct == pytest.approx(25.0, abs=0.01)

    def test_empty_curve(self, engine):
        engine.equity_curve = []
        dd, dd_pct = engine._calc_max_drawdown()
        assert dd == 0.0
        assert dd_pct == 0.0

    def test_single_point(self, engine):
        engine.equity_curve = [{"value": 100}]
        dd, dd_pct = engine._calc_max_drawdown()
        assert dd == 0.0


class TestSharpeRatio:
    def test_constant_returns(self, engine):
        engine.equity_curve = [{"value": 100 + i} for i in range(50)]
        sharpe = engine._calc_sharpe_ratio()
        assert sharpe > 0

    def test_too_few_points(self, engine):
        engine.equity_curve = [{"value": 100}, {"value": 101}]
        sharpe = engine._calc_sharpe_ratio()
        assert sharpe == 0.0

    def test_negative_returns(self, engine):
        engine.equity_curve = [{"value": 100 - i * 0.5} for i in range(50)]
        sharpe = engine._calc_sharpe_ratio()
        assert sharpe < 0


class TestAnnualReturn:
    def test_one_year_doubling(self, engine):
        engine.equity_curve = [{"value": 1000000 + i * (1000000 / 244)} for i in range(244)]
        ret = engine._calc_annual_return()
        assert ret > 90
        assert ret < 110

    def test_too_short(self, engine):
        engine.equity_curve = [{"value": 100}, {"value": 200}]
        ret = engine._calc_annual_return()
        assert ret == 0.0


class TestStopLossTakeProfit:
    def test_stop_loss_triggered(self, engine):
        engine.stop_loss = 0.05
        engine.positions["000001.SZ"] = 1000
        engine.entry_prices["000001.SZ"] = 10.0
        pending = {}
        engine._check_stop_loss("000001.SZ", 9.4, pending)
        assert "000001.SZ" in pending
        assert pending["000001.SZ"] == Signal.SELL

    def test_stop_loss_not_triggered(self, engine):
        engine.stop_loss = 0.05
        engine.positions["000001.SZ"] = 1000
        engine.entry_prices["000001.SZ"] = 10.0
        pending = {}
        engine._check_stop_loss("000001.SZ", 9.6, pending)
        assert "000001.SZ" not in pending

    def test_take_profit_triggered(self, engine):
        engine.take_profit = 0.10
        engine.positions["000001.SZ"] = 1000
        engine.entry_prices["000001.SZ"] = 10.0
        pending = {}
        engine._check_take_profit("000001.SZ", 11.1, pending)
        assert "000001.SZ" in pending
        assert pending["000001.SZ"] == Signal.SELL

    def test_take_profit_not_triggered(self, engine):
        engine.take_profit = 0.10
        engine.positions["000001.SZ"] = 1000
        engine.entry_prices["000001.SZ"] = 10.0
        pending = {}
        engine._check_take_profit("000001.SZ", 10.9, pending)
        assert "000001.SZ" not in pending

    def test_no_position(self, engine):
        engine.stop_loss = 0.05
        pending = {}
        engine._check_stop_loss("000001.SZ", 9.0, pending)
        assert "000001.SZ" not in pending


class TestTradeStats:
    def test_basic_stats(self, engine):
        d1 = datetime(2025, 1, 6)
        d2 = datetime(2025, 1, 10)
        engine.trades = [
            {"date": d1, "symbol": "000001.SZ", "action": "BUY", "price": 10.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 5.0, "slippage_cost": 0.0},
            {"date": d2, "symbol": "000001.SZ", "action": "SELL", "price": 11.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 16.0, "slippage_cost": 0.0},
        ]
        stats = engine._calc_trade_stats()
        assert stats["trades"] == 2
        assert stats["win_rate"] == 100.0
        assert stats["avg_holding_days"] == 4.0

    def test_losing_trade(self, engine):
        d1 = datetime(2025, 1, 6)
        d2 = datetime(2025, 1, 8)
        engine.trades = [
            {"date": d1, "symbol": "000001.SZ", "action": "BUY", "price": 10.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 5.0, "slippage_cost": 0.0},
            {"date": d2, "symbol": "000001.SZ", "action": "SELL", "price": 9.0, "quantity": 1000, "entry_price": 10.0, "commission_cost": 14.0, "slippage_cost": 0.0},
        ]
        stats = engine._calc_trade_stats()
        assert stats["win_rate"] == 0.0
        assert stats["avg_loss"] > 0

    def test_no_trades(self, engine):
        engine.trades = []
        stats = engine._calc_trade_stats()
        assert stats["trades"] == 0
        assert stats["win_rate"] == 0.0
