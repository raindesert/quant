"""单元测试 — Walk-Forward 验证模块。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtest.walk_forward import WalkForwardResult, WalkForwardValidator, WalkForwardWindow


def _make_test_df(days: int = 300, base_price: float = 10.0, symbol: str = "000001.SZ"):
    """生成测试用的 DataFrame，模拟行情数据。"""
    dates = pd.bdate_range(start="2024-01-01", periods=days)
    import numpy as np
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.02, days)
    close = base_price * (1 + returns).cumprod()
    df = pd.DataFrame({
        "date": dates,
        "open": close * (1 + np.random.uniform(-0.01, 0.01, days)),
        "high": close * (1 + np.random.uniform(0, 0.03, days)),
        "low": close * (1 - np.random.uniform(0, 0.03, days)),
        "close": close,
        "volume": np.random.randint(100_000, 10_000_000, days),
    })
    return df


class TestWalkForwardWindow:
    def test_default_values(self):
        w = WalkForwardWindow(
            window_id=1,
            train_start="2024-01-01",
            train_end="2024-04-30",
            test_start="2024-05-01",
            test_end="2024-06-30",
        )
        assert w.window_id == 1
        assert w.train_result == {}
        assert w.test_result == {}

    def test_with_results(self):
        w = WalkForwardWindow(
            window_id=1,
            train_start="2024-01-01",
            train_end="2024-04-30",
            test_start="2024-05-01",
            test_end="2024-06-30",
            train_result={"profit_pct": 5.0},
            test_result={"profit_pct": 2.0},
        )
        assert w.train_result["profit_pct"] == 5.0
        assert w.test_result["profit_pct"] == 2.0


class TestWalkForwardResult:
    def test_default_values(self):
        r = WalkForwardResult(strategy_name="sma", symbol="000001.SZ")
        assert r.avg_train_return == 0.0
        assert r.avg_test_return == 0.0
        assert r.degradation_ratio == 0.0
        assert r.is_overfit is False

    def test_summary_output(self):
        r = WalkForwardResult(
            strategy_name="sma",
            symbol="000001.SZ",
            avg_train_return=10.0,
            avg_test_return=5.0,
            degradation_ratio=0.5,
            is_overfit=False,
            windows=[
                WalkForwardWindow(
                    window_id=1,
                    train_start="2024-01-01",
                    train_end="2024-04-30",
                    test_start="2024-05-01",
                    test_end="2024-06-30",
                    train_result={"profit_pct": 10.0, "sharpe_ratio": 1.5},
                    test_result={"profit_pct": 5.0, "sharpe_ratio": 0.8},
                )
            ],
        )
        summary = r.summary()
        assert "Walk-Forward" in summary
        assert "sma" in summary
        assert "10.00%" in summary

    def test_overfit_detection(self):
        r = WalkForwardResult(
            strategy_name="sma",
            symbol="000001.SZ",
            avg_train_return=20.0,
            avg_test_return=-5.0,
            degradation_ratio=-0.25,
            is_overfit=True,
        )
        assert r.is_overfit is True


class TestWalkForwardValidator:
    def test_init_defaults(self):
        v = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ")
        assert v.train_days == 120
        assert v.test_days == 60
        assert v.step_days == 60
        assert v.overfit_threshold == 0.5

    def test_init_custom(self):
        v = WalkForwardValidator(
            strategy_name="rsi",
            symbol="600000.SH",
            train_days=90,
            test_days=30,
            step_days=30,
            overfit_threshold=0.3,
        )
        assert v.train_days == 90
        assert v.test_days == 30
        assert v.overfit_threshold == 0.3

    @patch("backtest.walk_forward.DataFetcher")
    @patch("backtest.walk_forward.DataProcessor")
    def test_validate_with_mock_data(self, mock_processor_cls, mock_fetcher_cls):
        test_df = _make_test_df(300)

        mock_fetcher = MagicMock()
        mock_fetcher.get_history.return_value = test_df
        mock_fetcher_cls.return_value = mock_fetcher

        mock_processor = MagicMock()
        mock_processor.clean.return_value = test_df
        mock_processor_cls.return_value = mock_processor

        validator = WalkForwardValidator(
            strategy_name="sma",
            symbol="000001.SZ",
            train_days=60,
            test_days=30,
            step_days=30,
        )
        result = validator.validate()

        assert isinstance(result, WalkForwardResult)
        assert result.strategy_name == "sma"
        assert result.symbol == "000001.SZ"
        assert len(result.windows) > 0

    @patch("backtest.walk_forward.DataFetcher")
    @patch("backtest.walk_forward.DataProcessor")
    def test_validate_empty_data(self, mock_processor_cls, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher.get_history.return_value = pd.DataFrame()
        mock_fetcher_cls.return_value = mock_fetcher

        mock_processor = MagicMock()
        mock_processor_cls.return_value = mock_processor

        validator = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ")
        result = validator.validate()

        assert len(result.windows) == 0
        assert result.avg_train_return == 0.0

    @patch("backtest.walk_forward.DataFetcher")
    @patch("backtest.walk_forward.DataProcessor")
    def test_validate_insufficient_data(self, mock_processor_cls, mock_fetcher_cls):
        short_df = _make_test_df(50)

        mock_fetcher = MagicMock()
        mock_fetcher.get_history.return_value = short_df
        mock_fetcher_cls.return_value = mock_fetcher

        mock_processor = MagicMock()
        mock_processor.clean.return_value = short_df
        mock_processor_cls.return_value = mock_processor

        validator = WalkForwardValidator(
            strategy_name="sma",
            symbol="000001.SZ",
            train_days=120,
            test_days=60,
        )
        result = validator.validate()

        assert len(result.windows) == 0


class TestWalkForwardAggregation:
    def test_aggregate_normal(self):
        windows = [
            WalkForwardWindow(
                window_id=1,
                train_start="2024-01-01",
                train_end="2024-04-30",
                test_start="2024-05-01",
                test_end="2024-06-30",
                train_result={"profit_pct": 10.0, "sharpe_ratio": 1.5},
                test_result={"profit_pct": 6.0, "sharpe_ratio": 0.9},
            ),
            WalkForwardWindow(
                window_id=2,
                train_start="2024-03-01",
                train_end="2024-06-30",
                test_start="2024-07-01",
                test_end="2024-08-31",
                train_result={"profit_pct": 8.0, "sharpe_ratio": 1.2},
                test_result={"profit_pct": 4.0, "sharpe_ratio": 0.6},
            ),
        ]

        validator = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ")
        result = validator._aggregate(windows)

        assert result.avg_train_return == 9.0
        assert result.avg_test_return == 5.0
        assert abs(result.degradation_ratio - 5.0 / 9.0) < 0.01
        assert result.is_overfit is False

    def test_aggregate_overfit(self):
        windows = [
            WalkForwardWindow(
                window_id=1,
                train_start="2024-01-01",
                train_end="2024-04-30",
                test_start="2024-05-01",
                test_end="2024-06-30",
                train_result={"profit_pct": 20.0, "sharpe_ratio": 2.0},
                test_result={"profit_pct": 2.0, "sharpe_ratio": 0.2},
            ),
        ]

        validator = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ", overfit_threshold=0.5)
        result = validator._aggregate(windows)

        assert result.degradation_ratio < 0.5
        assert result.is_overfit is True

    def test_aggregate_negative_test(self):
        windows = [
            WalkForwardWindow(
                window_id=1,
                train_start="2024-01-01",
                train_end="2024-04-30",
                test_start="2024-05-01",
                test_end="2024-06-30",
                train_result={"profit_pct": 10.0, "sharpe_ratio": 1.0},
                test_result={"profit_pct": -5.0, "sharpe_ratio": -0.5},
            ),
        ]

        validator = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ")
        result = validator._aggregate(windows)

        assert result.is_overfit is True

    def test_aggregate_empty_windows(self):
        validator = WalkForwardValidator(strategy_name="sma", symbol="000001.SZ")
        result = validator._aggregate([])
        assert result.avg_train_return == 0.0
        assert result.avg_test_return == 0.0
