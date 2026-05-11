"""策略基类"""
from abc import ABC, abstractmethod


class Signal:
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
        pass

    def on_order(self, order: dict):
        pass

    def set_position(self, symbol: str, quantity: int):
        self.positions[symbol] = quantity

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def reset(self):
        self.positions = {}
