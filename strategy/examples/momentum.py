"""动量策略 - 追涨杀跌"""
from __future__ import annotations

from typing import Any

from strategy.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """动量策略 - 价格上涨趋势中买入，下跌趋势中卖出"""

    def __init__(self, period: int = 20, threshold: float = 0.02):
        super().__init__("Momentum", period=period, threshold=threshold)
        self.period = period
        self.threshold = threshold
        self.prices: list[float] = []

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        return {"period": 20, "threshold": 0.02}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        return {"period": [5, 10, 20], "threshold": [0.01, 0.02, 0.05]}

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) > self.period + 1:
            self.prices = self.prices[-(self.period + 1):]

        if len(self.prices) < self.period + 1:
            return Signal.HOLD

        momentum = (self.prices[-1] - self.prices[-self.period]) / self.prices[-self.period]

        position = self.get_position(bar["symbol"])

        if momentum > self.threshold and position == 0:
            return Signal.BUY
        elif momentum < -self.threshold and position > 0:
            return Signal.SELL

        return Signal.HOLD

    def reset(self):
        super().reset()
        self.prices = []
