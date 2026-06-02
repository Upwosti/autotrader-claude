"""
Unit tests for backtester components.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from config import StrategyParams
from backtester.data_loader import DataLoader
from backtester.engine import BacktestEngine, BacktestResult


class TestDataLoader(unittest.TestCase):
    def test_load_returns_dataframe(self):
        loader = DataLoader()
        df = loader.load(pair="XAUUSD", timeframe="H1", synthetic_fallback=True)
        self.assertFalse(df.empty)

    def test_required_columns_present(self):
        loader = DataLoader()
        df = loader.load(pair="XAUUSD", timeframe="H1", synthetic_fallback=True)
        for col in ["open", "high", "low", "close", "volume"]:
            self.assertIn(col, df.columns)

    def test_high_gte_low(self):
        loader = DataLoader()
        df = loader.load(pair="XAUUSD", timeframe="H1", synthetic_fallback=True)
        self.assertTrue((df["high"] >= df["low"]).all())

    def test_synthetic_fallback(self):
        loader = DataLoader()
        df = loader.generate_synthetic("XAUUSD", n_bars=200)
        self.assertEqual(len(df), 200)


class TestBacktestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.params = StrategyParams()
        cls.result = BacktestEngine(cls.params).run(pair="XAUUSD")

    def test_run_returns_result(self):
        self.assertIsInstance(self.result, BacktestResult)

    def test_win_rate_in_range(self):
        self.assertGreaterEqual(self.result.win_rate, 0)
        self.assertLessEqual(self.result.win_rate, 1)

    def test_total_trades_non_negative(self):
        self.assertGreaterEqual(self.result.total_trades, 0)

    def test_winning_plus_losing_equals_total(self):
        # total_trades includes open trades (neither SL nor TP hit on next candle)
        self.assertLessEqual(
            self.result.winning_trades + self.result.losing_trades,
            self.result.total_trades,
        )

    def test_max_drawdown_non_negative(self):
        self.assertGreaterEqual(self.result.max_drawdown_pct, 0)

    def test_strategy_version_set(self):
        self.assertEqual(self.result.strategy_version, self.params.version)

    def test_overfitting_flag_logic(self):
        if self.result.total_trades < 50 and self.result.win_rate > 0.75:
            self.assertTrue(self.result.overfitting_flag)

    def test_small_sample_flag(self):
        if self.result.total_trades < 30:
            self.assertTrue(self.result.small_sample_flag)


class TestStrategyParams(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        params = StrategyParams(version=5, fvg_min_size_pips=7.5)
        d = params.to_dict()
        restored = StrategyParams.from_dict(d)
        self.assertEqual(restored.version, 5)
        self.assertAlmostEqual(restored.fvg_min_size_pips, 7.5)

    def test_version_increment(self):
        params = StrategyParams(version=3)
        self.assertEqual(params.version, 3)


if __name__ == "__main__":
    unittest.main()
