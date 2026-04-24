"""双均线策略示例"""
from strategy.base import BaseStrategy, Signal


class SMAStrategy(BaseStrategy):
    """简单移动平均线策略 - 金叉买入，死叉卖出"""

    def __init__(self, fast: int = 5, slow: int = 20):
        super().__init__("SMA")
        self.fast = fast
        self.slow = slow
        self.prices = []

    def on_bar(self, bar: dict) -> str:
        self.prices.append(bar["close"])
        if len(self.prices) < self.slow:
            return Signal.HOLD

        fast_ma = sum(self.prices[-self.fast:]) / self.fast
        slow_ma = sum(self.prices[-self.slow:]) / self.slow
        prev_fast_ma = sum(self.prices[-self.fast-1:-1]) / self.fast
        prev_slow_ma = sum(self.prices[-self.slow-1:-1]) / self.slow

        position = self.get_position(bar["symbol"])

        if prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma:
            if position == 0:
                return Signal.BUY
        elif prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma:
            if position > 0:
                return Signal.SELL

        return Signal.HOLD
