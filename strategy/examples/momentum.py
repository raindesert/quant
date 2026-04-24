"""动量策略 - 追涨杀跌"""
from strategy.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """动量策略 - 价格上涨趋势中买入，下跌趋势中卖出"""

    def __init__(self, period: int = 20, threshold: float = 0.02):
        super().__init__("Momentum")
        self.period = period
        self.threshold = threshold  # 动量阈值 2%
        self.prices = []

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) < self.period + 1:
            return Signal.HOLD

        # 计算动量 (期间收益率)
        momentum = (self.prices[-1] - self.prices[-self.period-1]) / self.prices[-self.period-1]

        position = self.get_position(bar["symbol"])

        # 动量大于阈值且无持仓，买入
        if momentum > self.threshold and position == 0:
            return Signal.BUY
        # 动量小于负阈值且有持仓，卖出
        elif momentum < -self.threshold and position > 0:
            return Signal.SELL

        return Signal.HOLD
