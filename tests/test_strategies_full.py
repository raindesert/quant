"""Tests for all trading strategies with realistic signal-generation scenarios."""
import unittest

from strategy.examples.sma import SMAStrategy
from strategy.examples.rsi import RSIStrategy
from strategy.examples.macd import MACDStrategy
from strategy.examples.bollinger import BollingerStrategy
from strategy.examples.momentum import MomentumStrategy
from strategy.examples.mean_reversion import MeanReversionStrategy
from strategy.base import Signal


def make_bar(close, open_p=None, high=None, low=None, volume=1e6):
    open_p = open_p or close
    high = high or max(open_p, close)
    low = low or min(open_p, close)
    bar = {
        "symbol": "TEST",
        "date": "2024-01-01",
        "timestamp": "2024-01-01",
        "open": open_p,
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
        "last_price": close,
    }
    return bar


class TestSMABullishCrossoverGeneratesBuy(unittest.TestCase):
    def test_golden_cross_buy_signal(self):
        """SMA golden cross (fast MA crosses above slow MA) triggers BUY."""
        strat = SMAStrategy(fast=5, slow=10)

        # Build 15 flat bars so both MAs are equal at 100
        for _ in range(15):
            strat.on_bar(make_bar(close=100.0))

        # Now push price up — fast MA rises above slow MA
        # After flat: fast_ma=slow_ma=100, prev_fast=prev_slow=100
        # Add bar with 110: fast_ma=102, slow_ma=101, prev_fast=prev_slow=100
        # 100 <= 100 and 102 > 101 → BUY
        sig = strat.on_bar(make_bar(close=110.0))
        self.assertEqual(sig, Signal.BUY)


class TestSMABearishCrossoverGeneratesSell(unittest.TestCase):
    def test_dead_cross_sell_signal(self):
        """SMA dead cross (fast MA crosses below slow MA) triggers SELL."""
        strat = SMAStrategy(fast=5, slow=10)

        # Build up: 12 flat at 100 (both MAs = 100)
        for _ in range(12):
            strat.on_bar(make_bar(close=100.0))

        # Spike to 110 (fast MA will be 110, slow MA rises to ~105)
        for _ in range(5):
            strat.on_bar(make_bar(close=110.0))

        # Set position to allow sell
        strat.set_position("TEST", 1000)

        # Now decline — fast MA drops below slow MA
        # Add 7 declining bars to cross down
        for p in [108, 106, 104, 102, 100, 98, 96]:
            sig = strat.on_bar(make_bar(close=p))
            if sig == Signal.SELL:
                break

        self.assertEqual(sig, Signal.SELL)


class TestRSIOversoldGeneratesBuy(unittest.TestCase):
    def test_rsi_oversold_buy(self):
        """RSI below 30 triggers BUY."""
        strat = RSIStrategy(period=14, oversold=30, overbought=70)

        # Build a steady price then drop sharply
        for p in [100.0] * 15:
            strat.on_bar(make_bar(close=p))

        # Drop price significantly to push RSI below 30
        drop_prices = [99, 98, 97, 96, 95, 94, 93, 92, 91, 90,
                       89, 88, 87, 86, 85, 84, 83, 82]
        sig = Signal.HOLD
        for p in drop_prices:
            bar = make_bar(close=p)
            sig = strat.on_bar(bar)
            if sig == Signal.BUY:
                break

        self.assertEqual(sig, Signal.BUY)


class TestRSIOverboughtGeneratesSell(unittest.TestCase):
    def test_rsi_overbought_sell(self):
        """RSI above 70 triggers SELL."""
        strat = RSIStrategy(period=14, oversold=30, overbought=70)

        # Establish a position first
        strat.set_position("TEST", 1000)

        # Build base prices
        for p in [100.0] * 15:
            strat.on_bar(make_bar(close=p))

        # Pump price up to push RSI above 70
        rise_prices = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110,
                       111, 112, 113, 114, 115, 116, 117, 118]
        sig = Signal.HOLD
        for p in rise_prices:
            bar = make_bar(close=p)
            sig = strat.on_bar(bar)
            if sig == Signal.SELL:
                break

        self.assertEqual(sig, Signal.SELL)


class TestMACDBullishCrossover(unittest.TestCase):
    def test_macd_bullish_cross(self):
        """MACD crosses above signal line triggers BUY."""
        strat = MACDStrategy(fast=12, slow=26, signal=9)

        # Initialize with steady prices (MACD near zero, signal near zero)
        for i in range(60):
            strat.on_bar(make_bar(close=100.0 + (i % 5) * 0.01))

        # Now push price up significantly — MACD will rise above signal
        for i in range(40):
            sig = strat.on_bar(make_bar(close=100.0 + i * 0.5))
            if sig == Signal.BUY:
                break

        self.assertEqual(sig, Signal.BUY)


class TestMACDBearishCrossover(unittest.TestCase):
    def test_macd_bearish_cross(self):
        """MACD crosses below signal line triggers SELL."""
        strat = MACDStrategy(fast=12, slow=26, signal=9)

        # Initialize with rising prices (MACD above signal)
        for i in range(60):
            strat.on_bar(make_bar(close=100.0 + i * 0.5))

        # Set position to allow sell
        strat.set_position("TEST", 1000)

        # Now push price down — MACD will fall below signal
        for i in range(40):
            sig = strat.on_bar(make_bar(close=130.0 - i * 0.5))
            if sig == Signal.SELL:
                break

        self.assertEqual(sig, Signal.SELL)


class TestBollingerBreakoutBuy(unittest.TestCase):
    def test_bollinger_breakout_buy(self):
        """Price breaks below lower band triggers BUY."""
        strat = BollingerStrategy(period=20, std_dev=2.0)

        # Build stable prices to establish bands around 100
        for _ in range(25):
            bar = make_bar(close=100.0)
            strat.on_bar(bar)

        # Drop price below lower band
        for p in [99, 98, 97, 96, 95, 94, 93, 92, 91, 90]:
            sig = strat.on_bar(make_bar(close=p))
            if sig == Signal.BUY:
                break

        self.assertEqual(sig, Signal.BUY)


class TestMeanReversionBuy(unittest.TestCase):
    def test_mean_reversion_buy(self):
        """Price significantly below MA triggers BUY."""
        strat = MeanReversionStrategy(period=20, threshold=0.05)

        # Build stable prices to establish MA at 100
        for _ in range(25):
            bar = make_bar(close=100.0)
            strat.on_bar(bar)

        # Drop price well below MA (> 5%)
        for p in [99, 98, 97, 96, 95, 94]:
            sig = strat.on_bar(make_bar(close=p))
            if sig == Signal.BUY:
                break

        self.assertEqual(sig, Signal.BUY)


class TestMomentumBuy(unittest.TestCase):
    def test_momentum_buy(self):
        """Positive momentum (price rising) triggers BUY."""
        strat = MomentumStrategy(period=10, threshold=0.02)

        # Build initial prices
        for _ in range(12):
            bar = make_bar(close=100.0)
            strat.on_bar(bar)

        # Now price goes up significantly to generate positive momentum
        for p in [101, 102, 103, 104, 105]:
            sig = strat.on_bar(make_bar(close=p))
            if sig == Signal.BUY:
                break

        self.assertEqual(sig, Signal.BUY)


if __name__ == "__main__":
    unittest.main()
