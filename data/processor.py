"""数据处理模块 - 清洗、复权、指标计算"""
import pandas as pd


class DataProcessor:

    @staticmethod
    def clean(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = df.dropna()
        df = df[df["volume"] > 0]
        df = df[df["close"] > 0]
        df = df[df["open"] > 0]
        df = df[df["high"] >= df["low"]]
        df = df[df["high"] >= df[["open", "close"]].max(axis=1)]
        df = df[df["low"] <= df[["open", "close"]].min(axis=1)]
        daily_returns = df["close"].pct_change()
        df = df[daily_returns.abs() < 0.3]
        return df.reset_index(drop=True)

    @staticmethod
    def add_ma(df: pd.DataFrame, periods: list | None = None) -> pd.DataFrame:
        """添加移动平均线"""
        df = df.copy()
        for period in (periods or [5, 10, 20, 60]):
            df[f"ma{period}"] = df["close"].rolling(window=period).mean()
        return df

    @staticmethod
    def add_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
        """添加布林带"""
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
