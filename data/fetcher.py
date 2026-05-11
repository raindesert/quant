"""行情数据获取模块。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

try:
    import baostock as bs

    BAOSTOCK_AVAILABLE = True
except ImportError:
    BAOSTOCK_AVAILABLE = False

logger = logging.getLogger("quant")


class DataFetcher:
    """获取历史行情与实时行情。"""

    _history_cache: dict[str, tuple[pd.DataFrame, float]] = {}
    _CACHE_TTL = 3600

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://finance.qq.com/",
            }
        )

    @staticmethod
    def _to_tencent_symbol(symbol: str) -> str:
        parts = symbol.split(".")
        if len(parts) == 2:
            code, exchange = parts
            prefix = {"SZ": "sz", "SH": "sh"}.get(exchange.upper(), "sz")
            return f"{prefix}{code}"
        return f"sz{symbol}"

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        parts = symbol.split(".")
        if len(parts) == 2:
            code, exchange = parts
            suffix = {"SZ": "sz", "SH": "sh"}.get(exchange.upper(), "sz")
            return f"{code}.{suffix}"
        return symbol

    def get_history(self, symbol: str, days: int = 250) -> pd.DataFrame:
        if days <= 0:
            raise ValueError("days 必须大于 0")

        cache_key = f"{symbol}_{days}"
        now = time.time()
        cached_entry = self._history_cache.get(cache_key)
        if cached_entry is not None:
            cached_df, cached_time = cached_entry
            if now - cached_time < self._CACHE_TTL:
                return cached_df.copy()

        if BAOSTOCK_AVAILABLE:
            try:
                df = self._fetch_from_baostock(symbol, days)
                if not df.empty and len(df) >= days * 0.6:
                    self._history_cache[cache_key] = (df.copy(), now)
                    return df
            except Exception as exc:
                logger.warning("baostock获取失败: %s", exc)

        tx_symbol = self._to_tencent_symbol(symbol)
        try:
            df = self._fetch_from_tencent(tx_symbol, days)
            if not df.empty and len(df) >= days * 0.7:
                self._history_cache[cache_key] = (df.copy(), now)
                return df
        except Exception as exc:
            logger.warning("腾讯API获取失败: %s", exc)

        raise RuntimeError(f"无法获取 {symbol} 历史数据，所有数据源均失败")

    def _fetch_from_baostock(self, symbol: str, days: int) -> pd.DataFrame:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                bs.login()
                code = self._to_baostock_code(symbol)
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

                rs = bs.query_history_k_data_plus(
                    code,
                    "date,open,close,high,low,volume,amount",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="2",
                )
                if rs.error_code != "0":
                    raise RuntimeError(f"baostock error: {rs.error_msg}")

                data = rs.data
                if not data or len(data) == 0:
                    return pd.DataFrame()

                df = pd.DataFrame(
                    data,
                    columns=["date", "open", "close", "high", "low", "volume", "amount"],
                )
                for col in ["open", "close", "high", "low", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["date"] = pd.to_datetime(df["date"])
                df["turnover"] = 0.0
                return df.dropna().sort_values("date").reset_index(drop=True)
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
            finally:
                try:
                    bs.logout()
                except Exception:
                    pass
        return pd.DataFrame()

    def _fetch_from_tencent(self, symbol: str, days: int) -> pd.DataFrame:
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

    def get_realtime(self, symbol: str) -> Optional[dict]:
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
            logger.warning("获取 %s 实时数据失败: %s", symbol, exc)
            return None

    def get_realtime_batch(self, symbols: list[str]) -> dict[str, dict]:
        result = {}
        for symbol in symbols:
            data = self.get_realtime(symbol)
            if data:
                result[symbol] = data
        return result
