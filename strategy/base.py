"""策略基类"""
from abc import ABC, abstractmethod
from typing import Optional


class Signal:
    """交易信号"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class BaseStrategy(ABC):
    """策略基类"""

    def __init__(self, name: str):
        self.name = name
        self.positions = {}

    @abstractmethod
    def on_bar(self, bar: dict) -> str:
        """
        处理每个bar数据
        返回: Signal.BUY / Signal.SELL / Signal.HOLD
        """
        pass

    def on_order(self, order: dict):
        """订单执行回调"""
        pass

    def set_position(self, symbol: str, quantity: int):
        """设置持仓"""
        self.positions[symbol] = quantity

    def get_position(self, symbol: str) -> int:
        """获取持仓"""
        return self.positions.get(symbol, 0)
