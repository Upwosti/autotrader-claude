"""
SMT (Smart Money Technique) Divergence — "a crack in correlation".

A failure swing where one asset makes a new high/low while its correlated
partner fails to confirm. Most powerful at HTF PD arrays.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional

from config import StrategyParams, SMT_CORRELATION_PAIRS


@dataclass
class SMTDivergence:
    direction: str           # 'bullish' | 'bearish'
    asset1_extreme: float
    asset2_extreme: float
    description: str


class SMTDivergenceDetector:
    """Compares the last two swing highs/lows between two correlated assets."""

    CORRELATION_PAIRS = SMT_CORRELATION_PAIRS

    def __init__(self, params: StrategyParams):
        self.params = params

    def _last_two_swings(self, df: pd.DataFrame, lookback: int):
        """Return ((prev_high, last_high), (prev_low, last_low)) over lookback bars."""
        sub = df.iloc[-lookback:] if len(df) > lookback else df
        highs = sub["high"].to_numpy(dtype=float)
        lows = sub["low"].to_numpy(dtype=float)
        half = max(1, self.params.structure_swing_lookback)
        n = len(highs)
        sh, sl = [], []
        for i in range(half, n - half):
            if highs[i] == np.max(highs[i - half:i + half + 1]):
                sh.append(highs[i])
            if lows[i] == np.min(lows[i - half:i + half + 1]):
                sl.append(lows[i])
        # fall back to global extremes when not enough pivots
        if len(sh) < 2:
            sh = [float(highs.max()), float(highs[-1])] if n else [0.0, 0.0]
        if len(sl) < 2:
            sl = [float(lows.min()), float(lows[-1])] if n else [0.0, 0.0]
        return (sh[-2], sh[-1]), (sl[-2], sl[-1])

    def detect_divergence(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        lookback: int = 20,
    ) -> Optional[SMTDivergence]:
        """df1 is the traded asset, df2 its correlated partner.

        bullish_smt: df1 makes a lower low while df2 fails to (higher low).
        bearish_smt: df1 makes a higher high while df2 fails to (lower high)."""
        if len(df1) < 3 or len(df2) < 3:
            return None
        (h1_prev, h1_last), (l1_prev, l1_last) = self._last_two_swings(df1, lookback)
        (h2_prev, h2_last), (l2_prev, l2_last) = self._last_two_swings(df2, lookback)

        # Bearish SMT: asset1 higher high, asset2 lower (failure) high
        if h1_last > h1_prev and h2_last < h2_prev:
            return SMTDivergence(
                direction="bearish",
                asset1_extreme=float(h1_last), asset2_extreme=float(h2_last),
                description="asset1 made HH while partner failed (bearish SMT)",
            )
        # Bullish SMT: asset1 lower low, asset2 higher (failure) low
        if l1_last < l1_prev and l2_last > l2_prev:
            return SMTDivergence(
                direction="bullish",
                asset1_extreme=float(l1_last), asset2_extreme=float(l2_last),
                description="asset1 made LL while partner failed (bullish SMT)",
            )
        return None

    def partner_for(self, pair: str) -> Optional[str]:
        info = self.CORRELATION_PAIRS.get(pair)
        return info["partner"] if info else None
