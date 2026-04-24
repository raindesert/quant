"""RSI 策略 - 相对强弱指标"""
from strategy.base import BaseStrategy, Signal


class RSIStrategy(BaseStrategy):
    """RSI 超卖买入，超买卖出"""

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__("RSI")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.prices = []

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) < self.period + 1:
            return Signal.HOLD

        # 计算 RSI
        deltas = [self.prices[i] - self.prices[i-1] for i in range(1, len(self.prices))]
        gains = [d if d > 0 else 0 for d in deltas[-self.period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-self.period:]]

        avg_gain = sum(gains) / self.period
        avg_loss = sum(losses) / self.period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        position = self.get_position(bar["symbol"])

        if rsi < self.oversold and position == 0:
            return Signal.BUY
        elif rsi > self.overbought and position > 0:
            return Signal.SELL

        return Signal.HOLD
