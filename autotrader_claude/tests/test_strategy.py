"""
Unit tests for ICT strategy components.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import unittest
from datetime import datetime, timedelta

from config import StrategyParams, ACTIVE_PARAMS
from strategy.liquidity import LiquidityDetector
from strategy.bos import BOSDetector
from strategy.fvg import FVGDetector
from strategy.confidence import ConfidenceScorer
from strategy.ict_engine import ICTEngine


def make_df(n=100, trend="bull") -> pd.DataFrame:
    """Create synthetic OHLCV dataframe."""
    np.random.seed(42)
    base = 2000.0
    closes = [base]
    direction = 1 if trend == "bull" else -1
    for _ in range(n - 1):
        change = direction * np.random.uniform(0, 2) + np.random.randn() * 3
        closes.append(max(closes[-1] + change, 100))

    data = []
    start = datetime(2024, 1, 1, 8, 0)
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        high = max(o, c) + abs(np.random.randn()) * 2
        low = min(o, c) - abs(np.random.randn()) * 2
        data.append({
            "open": o, "high": high, "low": low, "close": c,
            "volume": np.random.randint(100, 1000),
            "time": start + timedelta(hours=i),
        })
    df = pd.DataFrame(data)
    df.set_index("time", inplace=True)
    return df


class TestLiquidityDetector(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.df = make_df(100)

    def test_returns_list(self):
        det = LiquidityDetector(self.params)
        result = det.find_levels(self.df)
        self.assertIsInstance(result, list)

    def test_level_has_required_fields(self):
        det = LiquidityDetector(self.params)
        levels = det.find_levels(self.df)
        if levels:
            level = levels[0]
            self.assertIn("price", level.__dict__)
            self.assertIn("level_type", level.__dict__)
            self.assertIn("touches", level.__dict__)

    def test_get_latest_sweep_none_on_empty(self):
        det = LiquidityDetector(self.params)
        tiny_df = make_df(5)
        sweep = det.get_latest_sweep(tiny_df)
        self.assertIsNone(sweep)


class TestBOSDetector(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_bias_returns_valid_string(self):
        det = BOSDetector(self.params)
        df = make_df(80, "bull")
        bias = det.get_bias(df)
        self.assertIn(bias, ["bullish", "bearish", "neutral"])

    def test_bull_trend_bias(self):
        det = BOSDetector(self.params)
        df = make_df(80, "bull")
        bias = det.get_bias(df)
        self.assertIsNotNone(bias)

    def test_get_latest_bos_structure(self):
        det = BOSDetector(self.params)
        df = make_df(80)
        bos = det.get_latest_bos(df)
        if bos is not None:
            self.assertIn(bos.direction, ["bullish", "bearish"])
            self.assertIsInstance(bos.level, float)
            self.assertIsInstance(bos.confirmed, bool)


class TestFVGDetector(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_find_fvgs_returns_list(self):
        det = FVGDetector(self.params)
        df = make_df(60)
        fvgs = det.find_fvgs(df)
        self.assertIsInstance(fvgs, list)

    def test_fvg_fields(self):
        det = FVGDetector(self.params)
        df = make_df(60)
        fvgs = det.find_fvgs(df)
        for fvg in fvgs:
            self.assertIn(fvg.direction, ["bullish", "bearish"])
            self.assertGreater(fvg.top, fvg.bottom)

    def test_nearest_fvg_returns_none_when_no_fvgs(self):
        det = FVGDetector(self.params)
        tiny_df = make_df(3)
        result = det.nearest_fvg(tiny_df, 2000.0, "bullish")
        self.assertIsNone(result)


class TestConfidenceScorer(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_score_in_range(self):
        scorer = ConfidenceScorer(self.params)
        df = make_df(80)
        score = scorer.score(
            df=df,
            direction="bullish",
            sweep_confirmed=True,
            bos_confirmed=True,
            fvg_valid=True,
            htf_bias="bullish",
            spread_pips=1.5,
        )
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 10)

    def test_low_score_with_conflicts(self):
        scorer = ConfidenceScorer(self.params)
        df = make_df(80)
        score = scorer.score(
            df=df,
            direction="bullish",
            sweep_confirmed=False,
            bos_confirmed=False,
            fvg_valid=False,
            htf_bias="bearish",
            spread_pips=5.0,
        )
        self.assertLess(score, 5)


class TestICTEngine(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.engine = ICTEngine(self.params)

    def test_generate_signal_returns_none_or_signal(self):
        df = make_df(100)
        result = self.engine.generate_signal(df, "XAUUSD")
        if result is not None:
            self.assertIn(result.direction, ["buy", "sell"])
            self.assertGreater(result.confidence_score, 0)
            self.assertGreater(result.take_profit, 0)
            self.assertGreater(result.stop_loss, 0)

    def test_signal_has_valid_rrr(self):
        df = make_df(100)
        result = self.engine.generate_signal(df, "XAUUSD")
        if result is not None:
            self.assertGreaterEqual(result.rrr, self.params.min_rrr)


if __name__ == "__main__":
    unittest.main()
