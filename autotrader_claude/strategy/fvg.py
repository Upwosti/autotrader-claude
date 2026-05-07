"""
Fair Value Gap (FVG) detection — ICT concept.
An FVG is a 3-candle imbalance where candle 1 and candle 3 do not overlap.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional
from config import StrategyParams


@dataclass
class FVG:
    top: float
    bottom: float
    midpoint: float
    index: int              # index of candle 2 (middle of the 3-candle pattern)
    direction: str          # 'bullish' | 'bearish'
    size_pips: float
    age_bars: int = 0
    fill_pct: float = 0.0   # how much of the gap has been filled
    valid: bool = True

    @property
    def size(self) -> float:
        return self.top - self.bottom


class FVGDetector:
    """Detects and tracks Fair Value Gaps."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def _pips(self, price_diff: float) -> float:
        return abs(price_diff) / self.pip_size

    def detect(self, df: pd.DataFrame) -> List[FVG]:
        """Detect all FVGs in the dataframe."""
        fvgs: List[FVG] = []
        n = len(df)
        highs = df["high"].values
        lows = df["low"].values

        for i in range(2, n):
            c1_high = highs[i - 2]
            c1_low = lows[i - 2]
            c3_high = highs[i]
            c3_low = lows[i]

            # Bullish FVG: c1 high < c3 low (gap up)
            if c1_high < c3_low:
                gap_size_pips = self._pips(c3_low - c1_high)
                if gap_size_pips >= self.params.fvg_min_size_pips:
                    fvgs.append(FVG(
                        top=c3_low,
                        bottom=c1_high,
                        midpoint=(c3_low + c1_high) / 2,
                        index=i - 1,
                        direction="bullish",
                        size_pips=gap_size_pips,
                    ))

            # Bearish FVG: c1 low > c3 high (gap down)
            elif c1_low > c3_high:
                gap_size_pips = self._pips(c1_low - c3_high)
                if gap_size_pips >= self.params.fvg_min_size_pips:
                    fvgs.append(FVG(
                        top=c1_low,
                        bottom=c3_high,
                        midpoint=(c1_low + c3_high) / 2,
                        index=i - 1,
                        direction="bearish",
                        size_pips=gap_size_pips,
                    ))

        return fvgs

    def get_valid_fvgs(self, df: pd.DataFrame) -> List[FVG]:
        """
        Return FVGs that are still valid (not expired, not filled beyond threshold).
        Updates age and fill percentage before filtering.
        """
        fvgs = self.detect(df)
        n = len(df)
        valid = []

        for fvg in fvgs:
            age = n - 1 - fvg.index
            fvg.age_bars = age
            if age > self.params.fvg_max_age_bars:
                fvg.valid = False
                continue

            # Calculate fill percentage
            recent_high = df["high"].iloc[fvg.index + 1:].max() if fvg.index + 1 < n else fvg.bottom
            recent_low = df["low"].iloc[fvg.index + 1:].min() if fvg.index + 1 < n else fvg.top

            if fvg.direction == "bullish":
                fill = max(0.0, recent_low - fvg.bottom) / fvg.size if fvg.size > 0 else 0
            else:
                fill = max(0.0, fvg.top - recent_high) / fvg.size if fvg.size > 0 else 0

            fvg.fill_pct = min(fill, 1.0)
            if fvg.fill_pct >= self.params.fvg_fill_threshold_pct:
                fvg.valid = False
                continue

            valid.append(fvg)

        return valid

    def nearest_fvg(self, df: pd.DataFrame, current_price: float, direction: str) -> Optional[FVG]:
        """
        Return the nearest valid FVG in the trade direction.
        direction: 'long' → look for bullish FVG below price
                   'short' → look for bearish FVG above price
        """
        valid = self.get_valid_fvgs(df)
        candidates = []

        for fvg in valid:
            if direction == "long" and fvg.direction == "bullish" and fvg.top < current_price:
                candidates.append((abs(current_price - fvg.midpoint), fvg))
            elif direction == "short" and fvg.direction == "bearish" and fvg.bottom > current_price:
                candidates.append((abs(current_price - fvg.midpoint), fvg))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
