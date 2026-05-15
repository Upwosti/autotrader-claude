"""
Break of Structure (BOS) / Change of Character (CHoCH) detection — ICT concept.

Market structure:
  - Uptrend: series of higher highs (HH) and higher lows (HL)
  - Downtrend: series of lower lows (LL) and lower highs (LH)

A BOS occurs when:
  - Bullish BOS: price breaks above the last significant swing high with displacement
  - Bearish BOS: price breaks below the last significant swing low with displacement

A CHoCH (Change of Character) is the FIRST break of the prevailing structure,
signalling a potential trend reversal — this is what ICT traders look for after a sweep.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List
from config import StrategyParams


@dataclass
class StructureLevel:
    price: float
    index: int
    level_type: str   # 'swing_high' | 'swing_low'


@dataclass
class BOS:
    broken_level: StructureLevel
    break_candle_index: int
    direction: str            # 'bullish_bos' | 'bearish_bos'
    confirmation: str         # 'candle_close' | 'wick'
    displacement: bool        # was the break candle impulsive (large range)?
    break_price: float
    is_choch: bool = False    # True if this is the first break against prior trend


class BOSDetector:
    """Identifies Break of Structure events using swing highs/lows."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def _find_swing_highs(self, df: pd.DataFrame) -> List[StructureLevel]:
        """
        Find swing highs: local maximum where the high is the highest
        in a window of ±lookback bars.
        """
        highs = df["high"].values
        n = len(highs)
        lb = self.params.bos_lookback
        levels = []
        for i in range(lb, n - lb):
            window = highs[max(0, i - lb): i + lb + 1]
            if highs[i] == np.max(window):
                levels.append(StructureLevel(
                    price=float(highs[i]), index=i, level_type="swing_high"
                ))
        return levels

    def _find_swing_lows(self, df: pd.DataFrame) -> List[StructureLevel]:
        """
        Find swing lows: local minimum where the low is the lowest
        in a window of ±lookback bars.
        """
        lows = df["low"].values
        n = len(lows)
        lb = self.params.bos_lookback
        levels = []
        for i in range(lb, n - lb):
            window = lows[max(0, i - lb): i + lb + 1]
            if lows[i] == np.min(window):
                levels.append(StructureLevel(
                    price=float(lows[i]), index=i, level_type="swing_low"
                ))
        return levels

    def _is_displacement(self, df: pd.DataFrame, idx: int) -> bool:
        """
        Displacement candle: its range is >= 1.5× the average of the 3 prior candles.
        An impulsive displacement candle is required for a valid ICT BOS.
        """
        if idx < 3:
            return False
        body = abs(df["close"].iloc[idx] - df["open"].iloc[idx])
        candle_range = df["high"].iloc[idx] - df["low"].iloc[idx]
        # Use the larger of body or full range
        move = max(body, candle_range)
        prev_ranges = [
            df["high"].iloc[i] - df["low"].iloc[i]
            for i in range(idx - 3, idx)
        ]
        avg_range = float(np.mean(prev_ranges)) if prev_ranges else 0
        return avg_range > 0 and move >= avg_range * 1.5

    def _determine_prior_trend(self, swing_highs: List[StructureLevel],
                                swing_lows: List[StructureLevel]) -> str:
        """
        Determine the prevailing trend from the last two swing points.
        Returns 'bullish', 'bearish', or 'neutral'.
        """
        all_points = sorted(swing_highs + swing_lows, key=lambda s: s.index)
        if len(all_points) < 2:
            return "neutral"
        last = all_points[-1]
        prev = all_points[-2]
        if last.level_type == "swing_high" and prev.level_type == "swing_high":
            return "bullish" if last.price > prev.price else "bearish"
        if last.level_type == "swing_low" and prev.level_type == "swing_low":
            return "bullish" if last.price > prev.price else "bearish"
        return "neutral"

    def detect(self, df: pd.DataFrame) -> List[BOS]:
        """Detect all BOS/CHoCH events in the dataframe."""
        swing_highs = self._find_swing_highs(df)
        swing_lows = self._find_swing_lows(df)

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        bos_list: List[BOS] = []

        prior_trend = self._determine_prior_trend(swing_highs, swing_lows)

        # Bullish BOS: price breaks above a swing high
        for level in swing_highs:
            for i in range(level.index + 1, n):
                if self.params.bos_confirmation == "candle_close":
                    broke = closes[i] > level.price
                    break_price = float(closes[i])
                else:
                    broke = highs[i] > level.price
                    break_price = float(highs[i])

                if broke:
                    disp = self._is_displacement(df, i)
                    is_choch = (prior_trend == "bearish")
                    bos_list.append(BOS(
                        broken_level=level,
                        break_candle_index=i,
                        direction="bullish_bos",
                        confirmation=self.params.bos_confirmation,
                        displacement=disp,
                        break_price=break_price,
                        is_choch=is_choch,
                    ))
                    break

        # Bearish BOS: price breaks below a swing low
        for level in swing_lows:
            for i in range(level.index + 1, n):
                if self.params.bos_confirmation == "candle_close":
                    broke = closes[i] < level.price
                    break_price = float(closes[i])
                else:
                    broke = lows[i] < level.price
                    break_price = float(lows[i])

                if broke:
                    disp = self._is_displacement(df, i)
                    is_choch = (prior_trend == "bullish")
                    bos_list.append(BOS(
                        broken_level=level,
                        break_candle_index=i,
                        direction="bearish_bos",
                        confirmation=self.params.bos_confirmation,
                        displacement=disp,
                        break_price=break_price,
                        is_choch=is_choch,
                    ))
                    break

        return bos_list

    def get_latest_bos(self, df: pd.DataFrame) -> Optional[BOS]:
        """Return the most recent BOS event (preferring CHoCH with displacement)."""
        bos_list = self.detect(df)
        if not bos_list:
            return None
        # Prefer CHoCH with displacement, fall back to any BOS
        choch_with_disp = [b for b in bos_list if b.is_choch and b.displacement]
        if choch_with_disp:
            return max(choch_with_disp, key=lambda b: b.break_candle_index)
        return max(bos_list, key=lambda b: b.break_candle_index)

    def get_bias(self, df: pd.DataFrame) -> str:
        """Return 'bullish', 'bearish', or 'neutral' based on latest BOS."""
        bos = self.get_latest_bos(df)
        if bos is None:
            return "neutral"
        return "bullish" if bos.direction == "bullish_bos" else "bearish"
