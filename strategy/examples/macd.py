"""MACD 策略。"""
from __future__ import annotations

from typing import Any

from strategy.base import BaseStrategy, Signal


class MACDStrategy(BaseStrategy):
    """MACD 金叉买入，死叉卖出。"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__("MACD", fast=fast, slow=slow, signal=signal)
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.fast_ema = None
        self.slow_ema = None
        self.signal_ema = None
        self.prev_macd = None
        self._bar_count = 0

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        return {"fast": 12, "slow": 26, "signal": 9}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        return {"fast": [8, 12, 16], "slow": [20, 26, 34], "signal": [7, 9, 13]}

    @staticmethod
    def _next_ema(previous: float | None, price: float, period: int) -> float:
        if previous is None:
            return price
        alpha = 2 / (period + 1)
        return alpha * price + (1 - alpha) * previous

    def on_bar(self, bar: dict) -> str:
        price = bar["close"]
        self._bar_count += 1
        if self._bar_count < self.slow:
            self.fast_ema = self._next_ema(self.fast_ema, price, self.fast)
            self.slow_ema = self._next_ema(self.slow_ema, price, self.slow)
            return Signal.HOLD

        self.fast_ema = self._next_ema(self.fast_ema, price, self.fast)
        self.slow_ema = self._next_ema(self.slow_ema, price, self.slow)
        macd = self.fast_ema - self.slow_ema
        previous_signal = self.signal_ema if self.signal_ema is not None else macd
        self.signal_ema = self._next_ema(self.signal_ema, macd, self.signal)

        if self.prev_macd is None:
            self.prev_macd = macd
            return Signal.HOLD

        position = self.get_position(bar["symbol"])
        current_signal = Signal.HOLD

        if self.prev_macd <= previous_signal and macd > self.signal_ema and position == 0:
            current_signal = Signal.BUY
        elif self.prev_macd >= previous_signal and macd < self.signal_ema and position > 0:
            current_signal = Signal.SELL

        self.prev_macd = macd
        return current_signal

    def reset(self):
        super().reset()
        self.fast_ema = None
        self.slow_ema = None
        self.signal_ema = None
        self.prev_macd = None
        self._bar_count = 0
