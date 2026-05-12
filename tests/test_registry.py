"""单元测试 — 策略注册中心。"""
from __future__ import annotations

import pytest

from strategy.registry import (
    STRATEGY_REGISTRY,
    create_strategy,
    get_strategy_class,
    list_strategies,
)


class TestStrategyRegistry:
    def test_all_strategies_registered(self):
        assert "sma" in STRATEGY_REGISTRY
        assert "rsi" in STRATEGY_REGISTRY
        assert "macd" in STRATEGY_REGISTRY
        assert "bollinger" in STRATEGY_REGISTRY
        assert "momentum" in STRATEGY_REGISTRY
        assert "mean_reversion" in STRATEGY_REGISTRY

    def test_get_strategy_class(self):
        from strategy.examples.sma import SMAStrategy
        cls = get_strategy_class("sma")
        assert cls is SMAStrategy

    def test_get_strategy_class_case_insensitive(self):
        cls = get_strategy_class("SMA")
        assert cls is not None

    def test_get_unknown_strategy(self):
        cls = get_strategy_class("unknown")
        assert cls is None

    def test_create_strategy(self):
        strategy = create_strategy("sma")
        assert strategy is not None
        assert strategy.name == "SMA"

    def test_create_strategy_with_params(self):
        strategy = create_strategy("sma", fast=10, slow=30)
        assert strategy.fast == 10
        assert strategy.slow == 30

    def test_create_unknown_strategy(self):
        strategy = create_strategy("nonexistent")
        assert strategy is None

    def test_list_strategies(self):
        names = list_strategies()
        assert len(names) >= 6
        assert "sma" in names
        assert "rsi" in names


class TestStrategyParamDeclaration:
    def test_sma_params(self):
        from strategy.examples.sma import SMAStrategy
        params = SMAStrategy.get_params()
        assert "fast" in params
        assert "slow" in params

    def test_sma_param_grid(self):
        from strategy.examples.sma import SMAStrategy
        grid = SMAStrategy.get_param_grid()
        assert "fast" in grid
        assert "slow" in grid
        assert len(grid["fast"]) > 0

    def test_rsi_params(self):
        from strategy.examples.rsi import RSIStrategy
        params = RSIStrategy.get_params()
        assert "period" in params
        assert "oversold" in params

    def test_macd_params(self):
        from strategy.examples.macd import MACDStrategy
        params = MACDStrategy.get_params()
        assert "fast" in params
        assert "slow" in params
        assert "signal" in params

    def test_bollinger_params(self):
        from strategy.examples.bollinger import BollingerStrategy
        params = BollingerStrategy.get_params()
        assert "period" in params
        assert "std_dev" in params

    def test_strategy_params_property(self):
        strategy = create_strategy("sma", fast=10, slow=30)
        assert strategy.params == {"fast": 10, "slow": 30}
