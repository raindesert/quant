"""策略基类 — 定义策略生命周期、参数声明和信号常量。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Signal:
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class BaseStrategy(ABC):
    """策略基类。

    生命周期:
        on_init()  → 回测开始前调用（初始化指标、加载参数等）
        on_bar()   → 每根K线调用（核心逻辑）
        on_order() → 订单成交后回调
        on_finish() → 回测结束后调用（统计、报告等）

    参数声明:
        子类可覆盖 get_params() 和 get_param_grid() 类方法，
        声明策略接受的参数及其优化范围。
    """

    def __init__(self, name: str, **kwargs):
        self.name = name
        self.positions: dict[str, int] = {}
        self._params = kwargs

    @abstractmethod
    def on_bar(self, bar: dict) -> str:
        """每根K线调用，返回 Signal.BUY / Signal.SELL / Signal.HOLD。"""
        pass

    def on_init(self, context: dict | None = None):
        """回测开始前调用，用于初始化指标等。"""
        pass

    def on_order(self, order: dict):
        """订单成交后回调，可据此调整止损线等。"""
        pass

    def on_finish(self, context: dict | None = None):
        """回测结束后调用，用于统计和报告。"""
        pass

    def set_position(self, symbol: str, quantity: int):
        self.positions[symbol] = quantity

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def reset(self):
        self.positions = {}

    @classmethod
    def get_params(cls) -> dict[str, Any]:
        """返回策略的默认参数。子类可覆盖。"""
        return {}

    @classmethod
    def get_param_grid(cls) -> dict[str, list]:
        """返回策略的参数优化范围。子类可覆盖。"""
        return {}

    @property
    def params(self) -> dict[str, Any]:
        return self._params
