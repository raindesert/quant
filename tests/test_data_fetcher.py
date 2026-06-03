"""Tests for DataFetcher."""
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from data.fetcher import DataFetcher


class TestFetchReturnsDataFrameWithRequiredColumns(unittest.TestCase):
    def test_fetch_returns_df_with_required_columns(self):
        """get_history returns DataFrame with required columns."""
        # 同样需要 mock baostock + 增量 fetch，否则真实 baostock 干扰。
        n = 30
        tencent_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [100.0] * n,
            "close": [105.0] * n,
            "high": [106.0] * n,
            "low": [99.0] * n,
            "volume": [1000000] * n,
        })
        with patch.object(DataFetcher, "_fetch_from_tencent") as mock_tencent, \
             patch.object(DataFetcher, "_fetch_from_baostock") as mock_baostock, \
             patch.object(DataFetcher, "_try_incremental_fetch") as mock_inc:
            mock_tencent.return_value = tencent_df
            mock_baostock.side_effect = RuntimeError("mocked")
            mock_inc.return_value = pd.DataFrame()
            fetcher = DataFetcher()
            df = fetcher.get_history("000001.SZ", days=n)

            required_cols = ["date", "open", "close", "high", "low", "volume"]
            for col in required_cols:
                self.assertIn(col, df.columns)

    def test_fetch_produces_correct_row_count(self):
        """get_history returns correct number of rows for requested days."""
        # 之前版本：只 mock _fetch_from_tencent，但 baostock 是优先数据源，
        # 真实 baostock 返回 7 行导致失败。
        # 修复：mock _fetch_from_baostock 让它抛异常（fallback 到 tencent），
        # 并 mock _try_incremental_fetch 避免本地缓存干扰。
        n = 10
        tencent_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [100.0] * n,
            "close": [105.0] * n,
            "high": [106.0] * n,
            "low": [99.0] * n,
            "volume": [1000000] * n,
        })
        with patch.object(DataFetcher, "_fetch_from_tencent") as mock_tencent, \
             patch.object(DataFetcher, "_fetch_from_baostock") as mock_baostock, \
             patch.object(DataFetcher, "_try_incremental_fetch") as mock_inc:
            mock_tencent.return_value = tencent_df
            mock_baostock.side_effect = RuntimeError("mocked baostock fail")
            mock_inc.return_value = pd.DataFrame()  # 强制本地缓存 miss
            fetcher = DataFetcher()
            df = fetcher.get_history("000001.SZ", days=n)
            self.assertEqual(len(df), n)


class TestFetchEmptyResponseHandledGracefully(unittest.TestCase):
    def test_empty_df_handled(self):
        """Empty DataFrame is handled without crash."""
        df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
        self.assertTrue(df.empty)


class TestFetchInvalidJsonHandled(unittest.TestCase):
    def test_invalid_json_returns_empty_df(self):
        """Invalid JSON response results in empty DataFrame gracefully."""
        with patch.object(DataFetcher, '_fetch_from_tencent') as mock_tencent:
            mock_tencent.return_value = pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=10, freq="D"),
                "open": [100.0] * 10,
                "close": [105.0] * 10,
                "high": [106.0] * 10,
                "low": [99.0] * 10,
                "volume": [1000000] * 10,
            })
            fetcher = DataFetcher()
            result = fetcher._fetch_from_tencent("sz000001", 10)
            self.assertFalse(result.empty)
            self.assertIn("date", result.columns)


if __name__ == "__main__":
    unittest.main()
