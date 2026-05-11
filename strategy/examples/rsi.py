"""RSI 策略 - 相对强弱指标 (Wilder's smoothing)"""
from strategy.base import BaseStrategy, Signal


class RSIStrategy(BaseStrategy):
    """RSI 超卖买入，超买卖出"""

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__("RSI")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.initialized = False
        self._prev_price = None
        self._warmup_prices = []

    def on_bar(self, bar: dict) -> str:
        price = bar["close"]

        if self._prev_price is None:
            self._prev_price = price
            if not self.initialized:
                self._warmup_prices.append(price)
            return Signal.HOLD

        delta = price - self._prev_price
        self._prev_price = price
        gain = max(delta, 0)
        loss = max(-delta, 0)

        if not self.initialized:
            self._warmup_prices.append(price)
            if len(self._warmup_prices) >= self.period:
                gains = []
                losses = []
                for i in range(1, len(self._warmup_prices)):
                    d = self._warmup_prices[i] - self._warmup_prices[i - 1]
                    gains.append(max(d, 0))
                    losses.append(max(-d, 0))
                self.avg_gain = sum(gains) / self.period
                self.avg_loss = sum(losses) / self.period
                self.initialized = True
                self._warmup_prices = []
        else:
            self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        if not self.initialized:
            return Signal.HOLD

        if self.avg_loss == 0:
            rsi = 100.0
        else:
            rs = self.avg_gain / self.avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        position = self.get_position(bar["symbol"])

        if rsi < self.oversold and position == 0:
            return Signal.BUY
        elif rsi > self.overbought and position > 0:
            return Signal.SELL

        return Signal.HOLD

    def reset(self):
        super().reset()
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.initialized = False
        self._prev_price = None
        self._warmup_prices = []
