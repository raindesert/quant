"""布林带策略 - 价格触及下轨买入，上轨卖出"""
from __future__ import annotations

from typing import Any

from strategy.base import BaseStrategy, Signal


class BollingerStrategy(BaseStrategy):
    """布林带策略 - 突破下轨买入，突破上轨卖出"""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__("Bollinger", period=period, std_dev=std_dev)
        self.period = period
        self.std_dev = std_dev
        self.prices: list[float] = []

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        return {"period": 20, "std_dev": 2.0}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        return {"period": [10, 20, 30], "std_dev": [1.5, 2.0, 2.5]}

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) > self.period + 1:
            self.prices = self.prices[-self.period:]

        if len(self.prices) < self.period:
            return Signal.HOLD

        recent_prices = self.prices[-self.period:]
        mid = sum(recent_prices) / self.period
        if self.period < 2:
            std = 0.0
        else:
            variance = sum((p - mid) ** 2 for p in recent_prices) / (self.period - 1)
            std = variance ** 0.5
        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std

        position = self.get_position(bar["symbol"])
        current_price = bar["close"]

        if current_price < lower and position == 0:
            return Signal.BUY
        elif current_price > upper and position > 0:
            return Signal.SELL

        return Signal.HOLD

    def reset(self):
        super().reset()
        self.prices = []
