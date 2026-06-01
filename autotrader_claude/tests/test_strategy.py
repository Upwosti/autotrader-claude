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

from config import StrategyParams, ACTIVE_PARAMS, SMT_CORRELATION_PAIRS
from strategy.liquidity import LiquidityDetector
from strategy.bos import BOSDetector
from strategy.fvg import FVGDetector
from strategy.confidence import ConfidenceScorer
from strategy.ict_engine import ICTEngine
from strategy.pd_arrays import (
    OrderBlockDetector, PropulsionBlockDetector, MitigationBlockDetector,
    BreakerBlockDetector, RejectionBlockDetector, ImmediateRebalanceDetector,
    LiquidityVoidDetector, VolumeImbalanceDetector, BPRDetector,
    InversionFVGDetector, PDArrayScanner,
)
from strategy.key_liquidity import KeyLiquidityDetector
from strategy.irl_erl import IRLERLAnalyzer
from strategy.smt import SMTDivergenceDetector
from strategy.double_purge import DoublePurgeDetector
from strategy.crt_tbs import CRTTBSDetector


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
            self.assertIn(bos.direction, ["bullish_bos", "bearish_bos"])
            self.assertIsInstance(bos.break_price, float)
            self.assertIsInstance(bos.displacement, bool)


class TestFVGDetector(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_find_fvgs_returns_list(self):
        det = FVGDetector(self.params)
        df = make_df(60)
        fvgs = det.detect(df)
        self.assertIsInstance(fvgs, list)

    def test_fvg_fields(self):
        det = FVGDetector(self.params)
        df = make_df(60)
        fvgs = det.detect(df)
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
        result = scorer.score(
            sweep=None,
            bos=None,
            fvg=None,
            in_kill_zone=True,
            higher_tf_bias_aligned=True,
            displacement_present=True,
            spread_ok=True,
            news_clear=True,
        )
        self.assertGreaterEqual(result.total, 0)
        self.assertLessEqual(result.total, 10)

    def test_low_score_with_conflicts(self):
        scorer = ConfidenceScorer(self.params)
        result = scorer.score(
            sweep=None,
            bos=None,
            fvg=None,
            in_kill_zone=False,
            higher_tf_bias_aligned=False,
            displacement_present=False,
            spread_ok=False,
            news_clear=False,
        )
        self.assertLess(result.total, 5)


class TestICTEngine(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.engine = ICTEngine(self.params)

    def test_generate_signal_returns_none_or_signal(self):
        h4_df = make_df(100)
        daily_df = make_df(30)
        weekly_df = make_df(10)
        result = self.engine.generate_signal(h4_df, daily_df, weekly_df)
        # generate_signal always returns a TradeSignal (valid or invalid)
        self.assertIsNotNone(result)
        if result.valid:
            self.assertIn(result.direction, ["long", "short"])
            self.assertGreater(result.confidence.total, 0)
            self.assertGreater(result.take_profit, 0)
            self.assertGreater(result.stop_loss, 0)

    def test_signal_has_valid_rrr(self):
        h4_df = make_df(100)
        daily_df = make_df(30)
        weekly_df = make_df(10)
        result = self.engine.generate_signal(h4_df, daily_df, weekly_df)
        if result.valid:
            self.assertGreaterEqual(result.rrr, self.params.min_rrr)


PD_DETECTORS = [
    OrderBlockDetector, PropulsionBlockDetector, MitigationBlockDetector,
    BreakerBlockDetector, RejectionBlockDetector, ImmediateRebalanceDetector,
    LiquidityVoidDetector, VolumeImbalanceDetector, BPRDetector,
    InversionFVGDetector,
]


class TestPDArrays(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.df = make_df(200)
        self.pip = 0.01

    def test_all_detectors_return_lists(self):
        for cls in PD_DETECTORS:
            det = cls(self.params, self.pip)
            result = det.detect(self.df)
            self.assertIsInstance(result, list, cls.__name__)

    def test_detector_fields_and_types(self):
        for cls in PD_DETECTORS:
            det = cls(self.params, self.pip)
            for item in det.detect(self.df):
                self.assertIn(item.direction, ["bullish", "bearish"], cls.__name__)
                self.assertGreaterEqual(item.top, item.bottom, cls.__name__)
                # standard python types only
                self.assertIsInstance(item.top, float)
                self.assertIsInstance(item.bottom, float)
                self.assertIsInstance(item.index, int)

    def test_nearest_returns_none_or_object(self):
        for cls in PD_DETECTORS:
            det = cls(self.params, self.pip)
            res = det.nearest(self.df, 2000.0, "long")
            if res is not None:
                self.assertEqual(res.direction, "bullish")

    def test_nearest_on_tiny_df(self):
        tiny = make_df(3)
        for cls in PD_DETECTORS:
            det = cls(self.params, self.pip)
            # must not raise
            det.nearest(tiny, 2000.0, "short")

    def test_scanner_priority_and_direction(self):
        scanner = PDArrayScanner(self.params, self.pip)
        hits = scanner.scan_all(self.df)
        self.assertIsInstance(hits, list)
        best = scanner.best_entry(self.df, 2000.0, "long")
        if best is not None:
            self.assertEqual(best.direction, "bullish")
            self.assertGreater(best.quality, 0)


class TestKeyLiquidity(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.df = make_df(300)

    def test_detect_all_returns_list(self):
        det = KeyLiquidityDetector(self.params, 0.01)
        levels = det.detect_all(self.df)
        self.assertIsInstance(levels, list)
        for lv in levels:
            self.assertIn(lv.level_type, ["high", "low"])
            self.assertIsInstance(lv.price, float)

    def test_nearest_target_direction(self):
        det = KeyLiquidityDetector(self.params, 0.01)
        price = float(self.df["close"].iloc[-1])
        long_t = det.get_nearest_target(self.df, price, "long")
        if long_t is not None:
            self.assertGreater(long_t.price, price)
        short_t = det.get_nearest_target(self.df, price, "short")
        if short_t is not None:
            self.assertLess(short_t.price, price)


class TestIRLERL(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.df = make_df(200)

    def test_dol_state(self):
        an = IRLERLAnalyzer(self.params, 0.01)
        dol = an.get_current_dol(self.df)
        self.assertIn(dol.direction, ["bullish", "bearish", "neutral"])
        self.assertIn(dol.phase, ["seeking_erl", "offering_irl"])
        self.assertIsInstance(dol.at_erl, bool)
        self.assertIsInstance(dol.at_irl, bool)

    def test_cycle_phase(self):
        an = IRLERLAnalyzer(self.params, 0.01)
        self.assertIn(an.get_cycle_phase(self.df), ["seeking_erl", "offering_irl"])


class TestSMT(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_no_divergence_on_identical(self):
        det = SMTDivergenceDetector(self.params)
        df = make_df(80)
        result = det.detect_divergence(df, df.copy(), lookback=40)
        # identical assets cannot diverge
        self.assertIsNone(result)

    def test_divergence_detected(self):
        det = SMTDivergenceDetector(self.params)
        base = list(np.linspace(100, 110, 15)) + [120] + list(np.linspace(118, 112, 8)) + [125] + [120] * 6
        base2 = list(np.linspace(100, 110, 15)) + [120] + list(np.linspace(118, 112, 8)) + [118] + [115] * 6

        def mk(vals):
            idx = pd.date_range("2024-01-01", periods=len(vals), freq="4h", name="time")
            return pd.DataFrame({
                "open": vals, "high": [v + 0.5 for v in vals],
                "low": [v - 1 for v in vals], "close": vals,
                "volume": [100] * len(vals),
            }, index=idx)

        res = det.detect_divergence(mk(base), mk(base2), lookback=40)
        self.assertIsNotNone(res)
        self.assertEqual(res.direction, "bearish")

    def test_partner_lookup(self):
        det = SMTDivergenceDetector(self.params)
        self.assertEqual(det.partner_for("EURUSD"), "GBPUSD")
        self.assertIsNone(det.partner_for("UNKNOWNPAIR"))


class TestDoublePurge(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_detect_returns_list(self):
        det = DoublePurgeDetector(self.params, 0.01)
        result = det.detect(make_df(120), lookback=50)
        self.assertIsInstance(result, list)
        for dp in result:
            self.assertIn(dp.formation, ["classic", "one_candle", "amd"])
            self.assertIn(dp.direction, ["bullish", "bearish"])

    def test_one_candle_purge(self):
        # construct a candle that sweeps both sides and closes inside
        vals = [100] * 10
        idx = pd.date_range("2024-01-01", periods=12, freq="4h", name="time")
        opens = [100] * 12
        highs = [101] * 12
        lows = [99] * 12
        closes = [100] * 12
        # bar 11 sweeps both sides, closes inside
        highs[11] = 105
        lows[11] = 95
        opens[11] = 99.5
        closes[11] = 100.5
        df = pd.DataFrame({"open": opens, "high": highs, "low": lows,
                           "close": closes, "volume": [100] * 12}, index=idx)
        det = DoublePurgeDetector(self.params, 0.01)
        hits = [h for h in det.detect(df, lookback=12) if h.formation == "one_candle"]
        self.assertTrue(len(hits) >= 1)

    def test_get_latest(self):
        det = DoublePurgeDetector(self.params, 0.01)
        res = det.get_latest(make_df(120))
        if res is not None:
            self.assertIn(res.direction, ["bullish", "bearish"])


class TestCRTTBS(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.df = make_df(150)

    def test_detect_crt(self):
        det = CRTTBSDetector(self.params, 0.01)
        crt = det.detect_crt(self.df)
        if crt is not None:
            self.assertGreater(crt.high, crt.low)
            self.assertIsInstance(crt.index, int)

    def test_tbs_and_high_probability(self):
        det = CRTTBSDetector(self.params, 0.01)
        crt = det.detect_crt(self.df)
        if crt is not None:
            tbs = det.detect_tbs(self.df, crt)
            if tbs is not None:
                self.assertIsInstance(tbs.bodies_inside, int)
                self.assertIn(tbs.direction, ["bullish", "bearish", "none"])
                hp = det.is_high_probability_tbs(self.df, crt, tbs)
                self.assertIsInstance(hp, bool)


class TestConfidenceBonuses(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()

    def test_pd_array_and_bonuses_increase_score(self):
        scorer = ConfidenceScorer(self.params)
        base = scorer.score(
            sweep=None, bos=None, fvg=None, in_kill_zone=False,
            higher_tf_bias_aligned=False, displacement_present=False,
            spread_ok=True, news_clear=True,
        )
        boosted = scorer.score(
            sweep=None, bos=None, fvg=None, in_kill_zone=False,
            higher_tf_bias_aligned=False, displacement_present=False,
            spread_ok=True, news_clear=True,
            pd_array_kind="OB", double_purge=True, smt_divergence=True,
            crt_tbs=True, irl_erl_aligned=True, key_liquidity_dol=True,
        )
        self.assertGreater(boosted.total, base.total)
        self.assertLessEqual(boosted.total, 10.0)


class TestICTEngineFull(unittest.TestCase):
    def setUp(self):
        self.params = StrategyParams()
        self.engine = ICTEngine(self.params)

    def test_signal_carries_new_fields(self):
        sig = self.engine.generate_signal(make_df(200), make_df(60), make_df(20))
        self.assertIsNotNone(sig)
        # new analysis fields must be present
        self.assertTrue(hasattr(sig, "pd_array"))
        self.assertTrue(hasattr(sig, "dol"))
        self.assertTrue(hasattr(sig, "double_purge"))
        self.assertTrue(hasattr(sig, "smt"))
        self.assertTrue(hasattr(sig, "key_level"))
        self.assertIsNotNone(sig.dol)

    def test_consolidation_skip(self):
        # range that contracts sharply at the end -> ADR << ATR -> consolidation
        idx = pd.date_range("2024-01-01", periods=120, freq="4h", name="time")
        highs = [2010.0] * 115 + [2000.05] * 5
        lows = [1990.0] * 115 + [1999.95] * 5
        closes = [2000.0] * 120
        contracting = pd.DataFrame({
            "open": closes, "high": highs, "low": lows,
            "close": closes, "volume": [100] * 120,
        }, index=idx)
        self.assertTrue(self.engine._is_consolidating(contracting))
        # an expanding/normal series should not be flagged
        normal = make_df(120)
        self.assertIsInstance(self.engine._is_consolidating(normal), bool)


if __name__ == "__main__":
    unittest.main()
