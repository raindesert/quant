"""策略注册中心 — 统一管理策略注册表，消除多处重复定义。"""
from __future__ import annotations

from strategy.examples.sma import SMAStrategy
from strategy.examples.rsi import RSIStrategy
from strategy.examples.macd import MACDStrategy
from strategy.examples.bollinger import BollingerStrategy
from strategy.examples.momentum import MomentumStrategy
from strategy.examples.mean_reversion import MeanReversionStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "sma": SMAStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger": BollingerStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def get_strategy_class(name: str):
    """根据名称获取策略类。"""
    return STRATEGY_REGISTRY.get(name.lower())


def create_strategy(name: str, **kwargs):
    """根据名称创建策略实例，支持传入参数。"""
    cls = get_strategy_class(name)
    if cls is None:
        return None
    return cls(**kwargs)


def list_strategies() -> list[str]:
    """列出所有已注册的策略名称。"""
    return list(STRATEGY_REGISTRY.keys())
