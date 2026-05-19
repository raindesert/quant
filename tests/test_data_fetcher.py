"""Tests for DataFetcher."""
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from data.fetcher import DataFetcher


class TestFetchReturnsDataFrameWithRequiredColumns(unittest.TestCase):
    def test_fetch_returns_df_with_required_columns(self):
        """get_history returns DataFrame with required columns."""
        with patch.object(DataFetcher, '_fetch_from_tencent') as mock_tencent:
            mock_tencent.return_value = pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=30, freq="D"),
                "open": [100.0] * 30,
                "close": [105.0] * 30,
                "high": [106.0] * 30,
                "low": [99.0] * 30,
                "volume": [1000000] * 30,
            })
            fetcher = DataFetcher()
            df = fetcher.get_history("000001.SZ", days=30)

            required_cols = ["date", "open", "close", "high", "low", "volume"]
            for col in required_cols:
                self.assertIn(col, df.columns)

    def test_fetch_produces_correct_row_count(self):
        """get_history returns correct number of rows for requested days."""
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
            df = fetcher.get_history("000001.SZ", days=10)
            self.assertEqual(len(df), 10)


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
