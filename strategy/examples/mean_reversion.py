"""均值回归策略 - 价格偏离均线太多则反向交易"""
from strategy.base import BaseStrategy, Signal


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略 - 价格偏离均线太多时反向操作"""

    def __init__(self, period: int = 20, threshold: float = 0.05):
        super().__init__("MeanReversion")
        self.period = period
        self.threshold = threshold
        self.prices = []

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) > self.period + 1:
            self.prices = self.prices[-self.period:]

        if len(self.prices) < self.period:
            return Signal.HOLD

        ma = sum(self.prices[-self.period:]) / self.period
        current_price = bar["close"]
        deviation = (current_price - ma) / ma

        position = self.get_position(bar["symbol"])

        if deviation < -self.threshold and position == 0:
            return Signal.BUY
        elif deviation > self.threshold and position > 0:
            return Signal.SELL

        return Signal.HOLD

    def reset(self):
        super().reset()
        self.prices = []
