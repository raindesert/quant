"""行情数据获取模块。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests

try:
    import akshare as ak

    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class DataFetcher:
    """获取历史行情与实时行情。"""

    _history_cache: dict[str, pd.DataFrame] = {}

    def __init__(self):
        self.cache = self._history_cache
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://finance.qq.com/",
            }
        )

    @staticmethod
    def _to_tencent_symbol(symbol: str) -> str:
        """将 000001.SZ 这类代码转为腾讯接口格式。"""
        parts = symbol.split(".")
        if len(parts) == 2:
            code, exchange = parts
            prefix = {"SZ": "sz", "SH": "sh"}.get(exchange.upper(), "sz")
            return f"{prefix}{code}"
        return f"sz{symbol}"

    @staticmethod
    def _mock_seed(symbol: str) -> int:
        """为模拟数据生成稳定随机种子。"""
        digest = hashlib.md5(symbol.encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def get_history(self, symbol: str, days: int = 250) -> pd.DataFrame:
        """获取历史日线数据。优先使用 AKShare（支持更长周期），腾讯作为降级方案。"""
        if days <= 0:
            raise ValueError("days 必须大于 0")

        cache_key = f"{symbol}_{days}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        # 优先：AKShare（支持多年历史数据）
        if AKSHARE_AVAILABLE:
            try:
                df = self._fetch_from_akshare(symbol, days)
                if not df.empty and len(df) >= days * 0.7:
                    self.cache[cache_key] = df.copy()
                    return df
            except Exception as exc:
                print(f"AKShare获取失败: {exc}")

        # 降级：腾讯财经
        tx_symbol = self._to_tencent_symbol(symbol)
        try:
            df = self._fetch_from_tencent(tx_symbol, days)
            if not df.empty:
                self.cache[cache_key] = df.copy()
                return df
        except Exception as exc:
            print(f"腾讯API获取失败: {exc}")

        #兜底：模拟数据
        print("使用模拟数据")
        df = self._generate_mock_data(symbol, days)
        self.cache[cache_key] = df.copy()
        return df

    def _fetch_from_tencent(self, symbol: str, days: int) -> pd.DataFrame:
        """从腾讯财经接口获取历史日线。"""
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        params = {"param": f"{symbol},day,{start_date},{end_date},{days},qfq"}

        response = self.session.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()

        data_map = payload.get("data") or {}
        if not data_map:
            return pd.DataFrame()

        code = next(iter(data_map))
        code_payload = data_map.get(code) or {}
        day_data = code_payload.get("qfqday") or code_payload.get("day") or []
        if not day_data:
            return pd.DataFrame()

        normalized_rows = [row[:6] for row in day_data if len(row) >= 6]
        df = pd.DataFrame(
            normalized_rows,
            columns=["date", "open", "close", "high", "low", "volume"],
        )
        df["date"] = pd.to_datetime(df["date"])

        for column in ["open", "close", "high", "low", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df["amount"] = df["volume"] * df["close"]
        df["turnover"] = 0.0
        return df.dropna().sort_values("date").reset_index(drop=True)

    def _fetch_from_akshare(self, symbol: str, days: int) -> pd.DataFrame:
        """从 AKShare 获取历史日线。"""
        code = symbol.split(".")[0] if "." in symbol else symbol
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df.empty:
            return df

        df = df.iloc[:, :8].copy()
        df.columns = ["date", "open", "close", "high", "low", "volume", "amount", "turnover"]
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def _generate_mock_data(self, symbol: str, days: int) -> pd.DataFrame:
        """生成稳定的模拟日线数据。"""
        rng = np.random.default_rng(self._mock_seed(symbol))
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        base_price = 15.0 if "000001" in symbol else 50.0

        returns = rng.normal(0.0005, 0.02, days)
        close_prices = base_price * np.exp(np.cumsum(returns))
        open_prices = close_prices * (1 + rng.uniform(-0.01, 0.01, days))
        high_prices = np.maximum(open_prices, close_prices) * (1 + rng.uniform(0, 0.02, days))
        low_prices = np.minimum(open_prices, close_prices) * (1 - rng.uniform(0, 0.02, days))
        volumes = rng.uniform(1e6, 1e8, days)

        return pd.DataFrame(
            {
                "date": dates,
                "open": open_prices,
                "close": close_prices,
                "high": high_prices,
                "low": low_prices,
                "volume": volumes,
                "amount": volumes * close_prices,
                "turnover": rng.uniform(1, 10, days),
            }
        )

    def get_realtime(self, symbol: str) -> Optional[dict]:
        """获取实时行情。"""
        tx_symbol = self._to_tencent_symbol(symbol)

        try:
            response = self.session.get(f"https://qt.gtimg.cn/q={tx_symbol}", timeout=10)
            response.raise_for_status()
            data = response.text

            if f"v_{tx_symbol}" not in data or '="' not in data:
                return None

            content = data.split('="', maxsplit=1)[1].rstrip('";')
            fields = content.split("~")
            if len(fields) <= 40:
                return None

            last_price = float(fields[3])
            current_time = datetime.now()
            return {
                "symbol": symbol,
                "name": fields[1],
                "open": float(fields[5]),
                "high": float(fields[33]),
                "low": float(fields[34]),
                "close": last_price,
                "last_price": last_price,
                "prev_close": float(fields[4]),
                "volume": float(fields[6]),
                "date": current_time,
                "timestamp": current_time,
            }
        except Exception as exc:
            print(f"获取 {symbol} 实时数据失败: {exc}")
            return None

    def get_realtime_batch(self, symbols: list[str]) -> dict[str, dict]:
        """批量获取实时行情。"""
        result = {}
        for symbol in symbols:
            data = self.get_realtime(symbol)
            if data:
                result[symbol] = data
        return result
