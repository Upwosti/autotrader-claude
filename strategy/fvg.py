"""
Fair Value Gap (FVG) / Imbalance detection — ICT concept.

An FVG is a 3-candle pattern where there is a price gap (imbalance) between
candle 1 and candle 3:
  - Bullish FVG: candle 1 high < candle 3 low  (upward imbalance)
  - Bearish FVG: candle 1 low > candle 3 high  (downward imbalance)

Price typically returns to fill the FVG (mitigation), offering entry points.
Entry is at the 50% level (midpoint) or the FVG open edge.

ICT rules applied:
  - Minimum size filter (too small = noise)
  - Maximum age filter (old FVGs lose relevance)
  - Fill threshold (partially filled FVGs are still valid)
  - Only use FVGs that align with the trade direction
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
    open_edge: float       # entry edge: bottom for bullish, top for bearish
    index: int             # index of the middle candle (candle 2 of the 3)
    direction: str         # 'bullish' | 'bearish'
    size_pips: float
    size_price: float      # raw price difference
    age_bars: int = 0
    fill_pct: float = 0.0
    valid: bool = True
    # Bullish FVG formed on a large up-move = higher quality
    formed_on_displacement: bool = False

    @property
    def size(self) -> float:
        return self.top - self.bottom


class FVGDetector:
    """Detects and tracks Fair Value Gaps."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def _to_pips(self, price_diff: float) -> float:
        return abs(price_diff) / self.pip_size

    def find_fvgs(self, df: pd.DataFrame) -> List[FVG]:
        """Alias for detect() — for test compatibility."""
        return self.detect(df)

    def detect(self, df: pd.DataFrame) -> List[FVG]:
        """Detect all FVGs in the dataframe."""
        fvgs: List[FVG] = []
        n = len(df)
        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values

        for i in range(2, n):
            c1_high = highs[i - 2]
            c1_low = lows[i - 2]
            c2_range = highs[i - 1] - lows[i - 1]
            c3_high = highs[i]
            c3_low = lows[i]

            # Bullish FVG: gap between c1 high and c3 low (c1_high < c3_low)
            if c1_high < c3_low:
                size_price = c3_low - c1_high
                size_pips = self._to_pips(size_price)
                if size_pips >= self.params.fvg_min_size_pips:
                    # Displacement: middle candle (c2) is a large bullish move
                    c2_body = closes[i - 1] - opens[i - 1]
                    c2_displacement = c2_body > 0 and c2_body >= c2_range * 0.6
                    fvgs.append(FVG(
                        top=float(c3_low),
                        bottom=float(c1_high),
                        midpoint=float((c3_low + c1_high) / 2),
                        open_edge=float(c1_high),   # entry from bottom of gap
                        index=i - 1,
                        direction="bullish",
                        size_pips=size_pips,
                        size_price=size_price,
                        formed_on_displacement=c2_displacement,
                    ))

            # Bearish FVG: gap between c3 high and c1 low (c1_low > c3_high)
            elif c1_low > c3_high:
                size_price = c1_low - c3_high
                size_pips = self._to_pips(size_price)
                if size_pips >= self.params.fvg_min_size_pips:
                    c2_body = opens[i - 1] - closes[i - 1]
                    c2_displacement = c2_body > 0 and c2_body >= c2_range * 0.6
                    fvgs.append(FVG(
                        top=float(c1_low),
                        bottom=float(c3_high),
                        midpoint=float((c1_low + c3_high) / 2),
                        open_edge=float(c1_low),   # entry from top of gap
                        index=i - 1,
                        direction="bearish",
                        size_pips=size_pips,
                        size_price=size_price,
                        formed_on_displacement=c2_displacement,
                    ))

        return fvgs

    def get_valid_fvgs(self, df: pd.DataFrame) -> List[FVG]:
        """
        Return FVGs that are still valid:
        - Not older than fvg_max_age_bars
        - Not filled beyond fvg_fill_threshold_pct
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

            if fvg.size <= 0:
                fvg.valid = False
                continue

            # Calculate fill pct from candles after the FVG formed
            future_slice = df.iloc[fvg.index + 1:]
            if future_slice.empty:
                valid.append(fvg)
                continue

            if fvg.direction == "bullish":
                # Filled when price trades down into the gap
                lowest_after = float(future_slice["low"].min())
                if lowest_after <= fvg.bottom:
                    fill = 1.0
                elif lowest_after < fvg.top:
                    fill = (fvg.top - lowest_after) / fvg.size
                else:
                    fill = 0.0
            else:
                # Bearish FVG: filled when price trades up into the gap
                highest_after = float(future_slice["high"].max())
                if highest_after >= fvg.top:
                    fill = 1.0
                elif highest_after > fvg.bottom:
                    fill = (highest_after - fvg.bottom) / fvg.size
                else:
                    fill = 0.0

            fvg.fill_pct = min(fill, 1.0)
            if fvg.fill_pct >= self.params.fvg_fill_threshold_pct:
                fvg.valid = False
                continue

            valid.append(fvg)

        return valid

    def nearest_fvg(self, df: pd.DataFrame, current_price: float,
                    direction: str) -> Optional[FVG]:
        """
        Return the nearest valid, unfilled FVG for the given direction.
        For a 'long' trade: look for a bullish FVG below current price.
        For a 'short' trade: look for a bearish FVG above current price.
        Returns the closest FVG by midpoint distance.
        """
        valid = self.get_valid_fvgs(df)
        candidates = []

        for fvg in valid:
            if direction == "long" and fvg.direction == "bullish":
                if fvg.top < current_price:
                    candidates.append((abs(current_price - fvg.midpoint), fvg))
            elif direction == "short" and fvg.direction == "bearish":
                if fvg.bottom > current_price:
                    candidates.append((abs(current_price - fvg.midpoint), fvg))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
