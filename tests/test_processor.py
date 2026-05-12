"""单元测试 — 数据处理器。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.processor import DataProcessor


def _make_df(days: int = 100, base_price: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range(start="2024-01-01", periods=days)
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.02, days)
    close = base_price * (1 + returns).cumprod()
    return pd.DataFrame({
        "date": dates,
        "open": close * (1 + np.random.uniform(-0.01, 0.01, days)),
        "high": close * (1 + np.random.uniform(0, 0.03, days)),
        "low": close * (1 - np.random.uniform(0, 0.03, days)),
        "close": close,
        "volume": np.random.randint(100_000, 10_000_000, days),
    })


class TestClean:
    def test_removes_nan(self):
        df = _make_df(50)
        df.loc[5, "close"] = np.nan
        result = DataProcessor.clean(df)
        assert result["close"].isna().sum() == 0

    def test_removes_zero_close(self):
        df = _make_df(50)
        df.loc[10, "close"] = 0
        result = DataProcessor.clean(df)
        assert (result["close"] <= 0).sum() == 0

    def test_removes_negative_volume(self):
        df = _make_df(50)
        df.loc[15, "volume"] = -100
        result = DataProcessor.clean(df)
        assert (result["volume"] < 0).sum() == 0

    def test_preserves_valid_data(self):
        df = _make_df(50)
        result = DataProcessor.clean(df)
        assert len(result) > 0

    def test_high_gte_low(self):
        df = _make_df(50)
        result = DataProcessor.clean(df)
        assert (result["high"] >= result["low"]).all()


class TestAddMA:
    def test_adds_ma_columns(self):
        df = _make_df(100)
        result = DataProcessor.add_ma(df)
        assert "ma5" in result.columns
        assert "ma10" in result.columns
        assert "ma20" in result.columns
        assert "ma60" in result.columns

    def test_custom_periods(self):
        df = _make_df(100)
        result = DataProcessor.add_ma(df, periods=[3, 7])
        assert "ma3" in result.columns
        assert "ma7" in result.columns

    def test_ma_values(self):
        df = _make_df(30)
        result = DataProcessor.add_ma(df, periods=[5])
        expected = df["close"].iloc[:5].mean()
        assert abs(result["ma5"].iloc[4] - expected) < 0.01


class TestAddBollinger:
    def test_adds_bollinger_columns(self):
        df = _make_df(100)
        result = DataProcessor.add_bollinger(df)
        assert "bb_mid" in result.columns
        assert "bb_upper" in result.columns
        assert "bb_lower" in result.columns

    def test_upper_gte_lower(self):
        df = _make_df(100)
        result = DataProcessor.add_bollinger(df)
        valid = result.dropna(subset=["bb_upper", "bb_lower"])
        assert (valid["bb_upper"] >= valid["bb_lower"]).all()


class TestAddRSI:
    def test_adds_rsi_column(self):
        df = _make_df(100)
        result = DataProcessor.add_rsi(df)
        assert "rsi" in result.columns

    def test_rsi_range(self):
        df = _make_df(100)
        result = DataProcessor.add_rsi(df)
        valid = result.dropna(subset=["rsi"])
        assert (valid["rsi"] >= 0).all()
        assert (valid["rsi"] <= 100).all()


class TestAddMACD:
    def test_adds_macd_columns(self):
        df = _make_df(100)
        result = DataProcessor.add_macd(df)
        assert "macd_dif" in result.columns
        assert "macd_dea" in result.columns
        assert "macd_hist" in result.columns


class TestAddATR:
    def test_adds_atr_column(self):
        df = _make_df(100)
        result = DataProcessor.add_atr(df)
        assert "atr" in result.columns

    def test_atr_positive(self):
        df = _make_df(100)
        result = DataProcessor.add_atr(df)
        valid = result.dropna(subset=["atr"])
        assert (valid["atr"] > 0).all()


class TestAddKDJ:
    def test_adds_kdj_columns(self):
        df = _make_df(100)
        result = DataProcessor.add_kdj(df)
        assert "k" in result.columns
        assert "d" in result.columns
        assert "j" in result.columns


class TestAddAllIndicators:
    def test_adds_all(self):
        df = _make_df(100)
        result = DataProcessor.add_all_indicators(df)
        expected_cols = [
            "ma5", "ma10", "ma20", "ma60",
            "bb_mid", "bb_upper", "bb_lower",
            "rsi", "macd_dif", "macd_dea", "macd_hist",
            "atr", "k", "d", "j",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"
