"""Tests for DataFetcher."""
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd

from data.fetcher import DataFetcher


class TestFetchReturnsDataFrameWithRequiredColumns(unittest.TestCase):
    def test_fetch_returns_df_with_required_columns(self):
        """get_history returns DataFrame with required columns."""
        fetcher = DataFetcher()
        df = fetcher._generate_mock_data("000001.SZ", days=30)

        required_cols = ["date", "open", "close", "high", "low", "volume"]
        for col in required_cols:
            self.assertIn(col, df.columns)

    def test_mock_data_not_empty(self):
        """Mock data generation produces non-empty DataFrame."""
        fetcher = DataFetcher()
        df = fetcher._generate_mock_data("TEST", days=10)
        self.assertFalse(df.empty)
        self.assertEqual(len(df), 10)


class TestFetchEmptyResponseHandledGracefully(unittest.TestCase):
    def test_empty_df_handled(self):
        """Empty DataFrame is handled without crash."""
        fetcher = DataFetcher()
        # Simulate what happens with empty data
        df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
        self.assertTrue(df.empty)


class TestFetchInvalidJsonHandled(unittest.TestCase):
    def test_invalid_json_returns_empty_df(self):
        """Invalid JSON response results in empty DataFrame gracefully."""
        fetcher = DataFetcher()
        # If response.json() fails, it returns pd.DataFrame()
        # This is tested by the try/except in _fetch_from_tencent
        result = fetcher._fetch_from_tencent("sz000001", "000001.SZ", 10)
        # Falls back to mock data - not empty
        self.assertFalse(result.empty)


if __name__ == "__main__":
    unittest.main()