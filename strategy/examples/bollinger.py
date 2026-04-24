"""布林带策略 - 价格触及下轨买入，上轨卖出"""
from strategy.base import BaseStrategy, Signal


class BollingerStrategy(BaseStrategy):
    """布林带策略 - 突破下轨买入，突破上轨卖出"""

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__("Bollinger")
        self.period = period
        self.std_dev = std_dev
        self.prices = []

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) < self.period:
            return Signal.HOLD

        recent_prices = self.prices[-self.period:]
        mid = sum(recent_prices) / self.period
        std = (sum((p - mid) ** 2 for p in recent_prices) / self.period) ** 0.5
        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std

        position = self.get_position(bar["symbol"])
        current_price = bar["close"]

        # 价格突破下轨买入
        if current_price < lower and position == 0:
            return Signal.BUY
        # 价格突破上轨卖出
        elif current_price > upper and position > 0:
            return Signal.SELL

        return Signal.HOLD
