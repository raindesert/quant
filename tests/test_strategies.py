"""单元测试 — 策略信号逻辑。"""
from __future__ import annotations

import pytest

from strategy.base import Signal
from strategy.examples.sma import SMAStrategy
from strategy.examples.rsi import RSIStrategy
from strategy.examples.macd import MACDStrategy
from strategy.examples.bollinger import BollingerStrategy
from strategy.examples.momentum import MomentumStrategy
from strategy.examples.mean_reversion import MeanReversionStrategy


def _make_bar(close: float, symbol: str = "000001.SZ", open_: float | None = None):
    return {
        "symbol": symbol,
        "close": close,
        "open": open_ if open_ is not None else close,
        "high": close,
        "low": close,
        "volume": 1000000,
    }


class TestSMAStrategy:
    def test_hold_before_warmup(self):
        s = SMAStrategy(fast=3, slow=5)
        for i in range(5):
            result = s.on_bar(_make_bar(10.0 + i))
            assert result == Signal.HOLD

    def test_golden_cross_buy(self):
        s = SMAStrategy(fast=3, slow=5)
        prices = [10, 9, 8, 7, 6, 5, 4, 5, 6, 7, 8]
        results = []
        for p in prices:
            results.append(s.on_bar(_make_bar(p)))
        assert Signal.BUY in results

    def test_death_cross_sell(self):
        s = SMAStrategy(fast=3, slow=5)
        prices = [5, 6, 7, 8, 9, 10, 9, 8]
        results = []
        for p in prices:
            s.on_bar(_make_bar(p))
        s.set_position("000001.SZ", 100)
        prices2 = [7, 6, 5]
        results2 = []
        for p in prices2:
            results2.append(s.on_bar(_make_bar(p)))
        assert Signal.SELL in results2

    def test_reset(self):
        s = SMAStrategy(fast=3, slow=5)
        for i in range(10):
            s.on_bar(_make_bar(10.0 + i))
        assert len(s.prices) > 0
        s.reset()
        assert len(s.prices) == 0

    def test_memory_limit(self):
        s = SMAStrategy(fast=5, slow=20)
        for i in range(1000):
            s.on_bar(_make_bar(10.0 + i * 0.01))
        assert len(s.prices) <= s.MAX_PRICES + 1


class TestRSIStrategy:
    def test_hold_before_warmup(self):
        s = RSIStrategy(period=5)
        for i in range(4):
            result = s.on_bar(_make_bar(10.0))
            assert result == Signal.HOLD

    def test_oversold_buy(self):
        s = RSIStrategy(period=5, oversold=30, overbought=70)
        for p in [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]:
            s.on_bar(_make_bar(p))
        result = s.on_bar(_make_bar(0.5))
        assert result == Signal.BUY

    def test_overbought_sell(self):
        s = RSIStrategy(period=5, oversold=30, overbought=70)
        for p in [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]:
            s.on_bar(_make_bar(p))
        s.set_position("000001.SZ", 100)
        result = s.on_bar(_make_bar(20))
        assert result == Signal.SELL

    def test_reset(self):
        s = RSIStrategy()
        s.on_bar(_make_bar(10.0))
        s.reset()
        assert s.initialized is False
        assert s._prev_price is None


class TestMACDStrategy:
    def test_hold_before_warmup(self):
        s = MACDStrategy()
        for i in range(25):
            result = s.on_bar(_make_bar(10.0))
            assert result == Signal.HOLD

    def test_reset(self):
        s = MACDStrategy()
        s.on_bar(_make_bar(10.0))
        s.reset()
        assert s.fast_ema is None
        assert s._bar_count == 0


class TestBollingerStrategy:
    def test_hold_before_warmup(self):
        s = BollingerStrategy(period=5)
        for i in range(4):
            result = s.on_bar(_make_bar(10.0))
            assert result == Signal.HOLD

    def test_reset(self):
        s = BollingerStrategy()
        s.on_bar(_make_bar(10.0))
        s.reset()
        assert len(s.prices) == 0


class TestMomentumStrategy:
    def test_hold_before_warmup(self):
        s = MomentumStrategy(period=5)
        for i in range(4):
            result = s.on_bar(_make_bar(10.0))
            assert result == Signal.HOLD

    def test_reset(self):
        s = MomentumStrategy()
        s.on_bar(_make_bar(10.0))
        s.reset()
        assert len(s.prices) == 0


class TestMeanReversionStrategy:
    def test_hold_before_warmup(self):
        s = MeanReversionStrategy(period=5)
        for i in range(4):
            result = s.on_bar(_make_bar(10.0))
            assert result == Signal.HOLD

    def test_reset(self):
        s = MeanReversionStrategy()
        s.on_bar(_make_bar(10.0))
        s.reset()
        assert len(s.prices) == 0
