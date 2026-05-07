"""
Break of Structure (BOS) detection — ICT concept.
A BOS confirms a change in market structure direction.
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
    direction: str           # 'bullish_bos' | 'bearish_bos'
    confirmation: str        # 'candle_close' | 'wick'
    displacement: bool       # was the break impulsive (displacement)?
    break_price: float


class BOSDetector:
    """Identifies Break of Structure events using swing highs/lows."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def _find_swing_highs(self, df: pd.DataFrame) -> List[StructureLevel]:
        highs = df["high"].values
        n = len(highs)
        lb = self.params.bos_lookback
        levels = []
        for i in range(lb, n - lb):
            if highs[i] == max(highs[i - lb: i + lb + 1]):
                levels.append(StructureLevel(
                    price=highs[i], index=i, level_type="swing_high"
                ))
        return levels

    def _find_swing_lows(self, df: pd.DataFrame) -> List[StructureLevel]:
        lows = df["low"].values
        n = len(lows)
        lb = self.params.bos_lookback
        levels = []
        for i in range(lb, n - lb):
            if lows[i] == min(lows[i - lb: i + lb + 1]):
                levels.append(StructureLevel(
                    price=lows[i], index=i, level_type="swing_low"
                ))
        return levels

    def _is_displacement(self, df: pd.DataFrame, idx: int) -> bool:
        """Check if the break candle is a displacement (large impulsive move)."""
        if idx < 3:
            return False
        candle_range = df["high"].iloc[idx] - df["low"].iloc[idx]
        prev_ranges = [
            df["high"].iloc[i] - df["low"].iloc[i]
            for i in range(idx - 3, idx)
        ]
        avg_range = np.mean(prev_ranges) if prev_ranges else 0
        return candle_range >= avg_range * 1.5

    def detect(self, df: pd.DataFrame) -> List[BOS]:
        """Detect all BOS events in the dataframe."""
        swing_highs = self._find_swing_highs(df)
        swing_lows = self._find_swing_lows(df)
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        bos_list: List[BOS] = []

        # Bearish BOS: price closes below a swing low
        for level in swing_lows:
            for i in range(level.index + 1, n):
                if self.params.bos_confirmation == "candle_close":
                    broke = closes[i] < level.price
                else:
                    broke = lows[i] < level.price
                if broke:
                    bos_list.append(BOS(
                        broken_level=level,
                        break_candle_index=i,
                        direction="bearish_bos",
                        confirmation=self.params.bos_confirmation,
                        displacement=self._is_displacement(df, i),
                        break_price=closes[i] if self.params.bos_confirmation == "candle_close" else lows[i],
                    ))
                    break

        # Bullish BOS: price closes above a swing high
        for level in swing_highs:
            for i in range(level.index + 1, n):
                if self.params.bos_confirmation == "candle_close":
                    broke = closes[i] > level.price
                else:
                    broke = highs[i] > level.price
                if broke:
                    bos_list.append(BOS(
                        broken_level=level,
                        break_candle_index=i,
                        direction="bullish_bos",
                        confirmation=self.params.bos_confirmation,
                        displacement=self._is_displacement(df, i),
                        break_price=closes[i] if self.params.bos_confirmation == "candle_close" else highs[i],
                    ))
                    break

        return bos_list

    def get_latest_bos(self, df: pd.DataFrame) -> Optional[BOS]:
        """Return the most recent BOS event."""
        bos_list = self.detect(df)
        if not bos_list:
            return None
        return max(bos_list, key=lambda b: b.break_candle_index)

    def get_bias(self, df: pd.DataFrame) -> str:
        """Return 'bullish', 'bearish', or 'neutral' based on latest BOS."""
        bos = self.get_latest_bos(df)
        if bos is None:
            return "neutral"
        if bos.direction == "bullish_bos":
            return "bullish"
        return "bearish"
