"""动量策略 - 追涨杀跌"""
from strategy.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """动量策略 - 价格上涨趋势中买入，下跌趋势中卖出"""

    def __init__(self, period: int = 20, threshold: float = 0.02):
        super().__init__("Momentum")
        self.period = period
        self.threshold = threshold
        self.prices = []

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
