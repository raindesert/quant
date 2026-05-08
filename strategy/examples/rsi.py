"""RSI 策略 - 相对强弱指标 (Wilder's smoothing)"""
from strategy.base import BaseStrategy, Signal


class RSIStrategy(BaseStrategy):
    """RSI 超卖买入，超买卖出

    使用 Wilder's 平滑算法计算 RSI:
    - 初始平均涨跌幅使用简单均值
    - 后续使用 Wilder 权重: avg = (prev_avg * (period - 1) + current) / period
    """

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__("RSI")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.prices = []
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.initialized = False

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])

        if len(self.prices) < self.period + 1:
            return Signal.HOLD

        # 计算当期涨跌
        delta = self.prices[-1] - self.prices[-2]
        gain = max(delta, 0)
        loss = max(-delta, 0)

        if not self.initialized:
            # 初始平均: 简单均值
            gains = [max(self.prices[i] - self.prices[i-1], 0) for i in range(1, len(self.prices))]
            losses = [max(-(self.prices[i] - self.prices[i-1]), 0) for i in range(1, len(self.prices))]
            self.avg_gain = sum(gains[-self.period:]) / self.period
            self.avg_loss = sum(losses[-self.period:]) / self.period
            self.initialized = True
        else:
            # Wilder's 平滑: avg = (prev_avg * (period - 1) + current) / period
            self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
            self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

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
