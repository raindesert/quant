"""数据处理模块 - 清洗、复权、指标计算"""
from __future__ import annotations

import numpy as np
import pandas as pd


class DataProcessor:

    @staticmethod
    def clean(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.dropna(subset=["date", "close", "open", "high", "low"])
        df = df[df["close"] > 0]
        df = df[df["open"] > 0]
        df = df[df["high"] >= df["low"]]
        df = df[df["high"] >= df[["open", "close"]].max(axis=1)]
        df = df[df["low"] <= df[["open", "close"]].min(axis=1)]
        if "volume" in df.columns:
            df.loc[df["volume"] < 0, "volume"] = 0
        daily_returns = df["close"].pct_change()
        df = df[(daily_returns.abs() < 0.3) | (daily_returns.isna())]
        df = df.reset_index(drop=True)
        return df

    @staticmethod
    def add_ma(df: pd.DataFrame, periods: list | None = None) -> pd.DataFrame:
        df = df.copy()
        for period in (periods or [5, 10, 20, 60]):
            df[f"ma{period}"] = df["close"].rolling(window=period).mean()
        return df

    @staticmethod
    def add_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
        df = df.copy()
        df["bb_mid"] = df["close"].rolling(window=period).mean()
        df["bb_std"] = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_mid"] + std * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - std * df["bb_std"]
        return df

    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        df = df.copy()
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    @staticmethod
    def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        df = df.copy()
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        df["macd_dif"] = ema_fast - ema_slow
        df["macd_dea"] = df["macd_dif"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2
        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """添加平均真实波幅 (ATR)。"""
        df = df.copy()
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=period).mean()
        return df

    @staticmethod
    def add_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
        """添加 KDJ 指标。"""
        df = df.copy()
        low_n = df["low"].rolling(window=n).min()
        high_n = df["high"].rolling(window=n).max()
        denom = high_n - low_n
        rsv = np.where(denom > 0, (df["close"] - low_n) / denom * 100, 50.0)
        rsv = pd.Series(rsv, index=df.index)

        k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
        d = k.ewm(alpha=1 / m2, adjust=False).mean()
        j = 3 * k - 2 * d

        df["k"] = k
        df["d"] = d
        df["j"] = j
        return df

    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """一次性添加所有常用指标。"""
        df = DataProcessor.add_ma(df)
        df = DataProcessor.add_bollinger(df)
        df = DataProcessor.add_rsi(df)
        df = DataProcessor.add_macd(df)
        df = DataProcessor.add_atr(df)
        df = DataProcessor.add_kdj(df)
        return df
