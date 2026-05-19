"""KDJ 策略 - K/D金叉买入，死叉卖出"""
from __future__ import annotations

from typing import Any

from strategy.base import BaseStrategy, Signal


class KDJStrategy(BaseStrategy):
    """KDJ 金叉买入，死叉卖出"""

    def __init__(self, n: int = 9, m1: int = 3, m2: int = 3, oversold: float = 20, overbought: float = 80):
        super().__init__("KDJ", n=n, m1=m1, m2=m2, oversold=oversold, overbought=overbought)
        self.n = n
        self.m1 = m1
        self.m2 = m2
        self.oversold = oversold
        self.overbought = overbought
        self._prev_k = None
        self._prev_d = None

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        return {"n": 9, "m1": 3, "m2": 3, "oversold": 20, "overbought": 80}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        return {
            "n": [9, 14, 21],
            "m1": [2, 3, 5],
            "m2": [2, 3, 5],
            "oversold": [15, 20, 25],
            "overbought": [75, 80, 85],
        }

    def on_bar(self, bar: dict) -> str:
        k = bar.get("k")
        d = bar.get("d")
        j = bar.get("j")

        if k is None or d is None or j is None:
            return Signal.HOLD

        position = self.get_position(bar["symbol"])

        if self._prev_k is not None and self._prev_d is not None:
            if self._prev_k <= self._prev_d and k > d and d < self.oversold and position == 0:
                self._prev_k = k
                self._prev_d = d
                return Signal.BUY
            if self._prev_k >= self._prev_d and k < d and d > self.overbought and position > 0:
                self._prev_k = k
                self._prev_d = d
                return Signal.SELL

        self._prev_k = k
        self._prev_d = d
        return Signal.HOLD

    def reset(self):
        super().reset()
        self._prev_k = None
        self._prev_d = None