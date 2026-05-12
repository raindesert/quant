"""双均线策略示例"""
from __future__ import annotations

from typing import Any

from strategy.base import BaseStrategy, Signal


class SMAStrategy(BaseStrategy):
    """简单移动平均线策略 - 金叉买入，死叉卖出"""

    MAX_PRICES = 0

    def __init__(self, fast: int = 5, slow: int = 20):
        super().__init__("SMA", fast=fast, slow=slow)
        self.fast = fast
        self.slow = slow
        self.MAX_PRICES = slow + 2
        self.prices: list[float] = []

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        return {"fast": 5, "slow": 20}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        return {"fast": [5, 10, 15, 20], "slow": [30, 60, 120]}

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) > self.MAX_PRICES:
            self.prices = self.prices[-(self.slow + 1):]

        if len(self.prices) < self.slow + 1:
            return Signal.HOLD

        fast_ma = sum(self.prices[-self.fast:]) / self.fast
        slow_ma = sum(self.prices[-self.slow:]) / self.slow
        prev_fast_ma = sum(self.prices[-self.fast - 1:-1]) / self.fast
        prev_slow_ma = sum(self.prices[-self.slow - 1:-1]) / self.slow

        position = self.get_position(bar["symbol"])

        if prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma:
            if position == 0:
                return Signal.BUY
        elif prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma:
            if position > 0:
                return Signal.SELL

        return Signal.HOLD

    def reset(self):
        super().reset()
        self.prices = []
