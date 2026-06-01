"""
CRT (Candle Range Theory) + TBS (Turtle Body Soup).

CRT: a reference candle's High/Low defines a range. We wait for price to sweep
the CRT High (OHP = Old High Purged) or Low (OLP = Old Low Purged) before
entering.

TBS: bodies of confirmation candles forming inside the CRT range. Need >= 2
bodies inside for a high-probability setup. Equal highs inside the TBS = avoid
(unfilled liquidity present).

Entry models inside the TBS:
    Model #1 = Order Block entry
    Model #2 = FVG entry
    Model #3 = BOS entry
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List

from config import StrategyParams


@dataclass
class CRT:
    index: int               # index of the CRT reference candle
    high: float
    low: float
    midpoint: float
    range_size: float


@dataclass
class TBS:
    crt_index: int
    bodies_inside: int
    high_swept: bool         # OHP — CRT high purged
    low_swept: bool          # OLP — CRT low purged
    equal_highs_inside: bool
    direction: str           # 'bullish' | 'bearish' | 'none'


class CRTTBSDetector:
    """Detects the CRT reference candle and the TBS forming inside its range."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect_crt(self, df: pd.DataFrame) -> Optional[CRT]:
        """The CRT reference candle is a large-range candle (a key level).
        We pick the most recent candle whose range exceeds the rolling average
        by crt_range_factor, leaving room for confirmation candles after it."""
        n = len(df)
        if n < 5:
            return None
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        rng = h - l
        avg = float(np.mean(rng)) if n else 0.0
        thr = avg * self.params.crt_range_factor
        # search backwards, keep at least 2 candles after the CRT for TBS
        for i in range(n - 3, -1, -1):
            if rng[i] >= thr and rng[i] > 0:
                return CRT(index=i, high=float(h[i]), low=float(l[i]),
                           midpoint=float((h[i] + l[i]) / 2), range_size=float(rng[i]))
        return None

    def detect_tbs(self, df: pd.DataFrame, crt: CRT) -> Optional[TBS]:
        """Count confirmation bodies forming inside the CRT range after the CRT
        candle, and detect whether the CRT high/low have been purged."""
        if crt is None:
            return None
        n = len(df)
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)

        start = crt.index + 1
        if start >= n:
            return None

        body_top = np.maximum(o[start:], c[start:])
        body_bot = np.minimum(o[start:], c[start:])
        inside = (body_top <= crt.high) & (body_bot >= crt.low)
        bodies_inside = int(np.sum(inside))

        high_swept = bool(np.any(h[start:] > crt.high))
        low_swept = bool(np.any(l[start:] < crt.low))

        # equal highs inside TBS — liquidity that argues against entry
        inside_highs = h[start:][inside] if inside.any() else np.array([])
        equal_highs = self._has_equal(inside_highs)

        # Direction: after CRT High purged we look for bearish reversal TBS,
        # after CRT Low purged we look for bullish reversal TBS.
        direction = "none"
        if low_swept and not high_swept:
            direction = "bullish"
        elif high_swept and not low_swept:
            direction = "bearish"
        elif high_swept and low_swept:
            # whichever was swept last (later bar) defines the reversal
            last_high = np.max(np.nonzero(h[start:] > crt.high)[0]) if high_swept else -1
            last_low = np.max(np.nonzero(l[start:] < crt.low)[0]) if low_swept else -1
            direction = "bearish" if last_high > last_low else "bullish"

        return TBS(
            crt_index=crt.index, bodies_inside=bodies_inside,
            high_swept=high_swept, low_swept=low_swept,
            equal_highs_inside=equal_highs, direction=direction,
        )

    def _has_equal(self, values: np.ndarray) -> bool:
        if values.size < 2:
            return False
        tol = self.params.equal_level_tolerance_pips * self.pip_size
        for i in range(len(values)):
            if np.sum(np.abs(values - values[i]) <= tol) >= 2:
                return True
        return False

    def is_high_probability_tbs(self, df: pd.DataFrame, crt: CRT, tbs: TBS) -> bool:
        """High probability requires >= min bodies inside, a purge of one side,
        and NO equal highs inside (which would signal trapped liquidity)."""
        if crt is None or tbs is None:
            return False
        enough_bodies = tbs.bodies_inside >= self.params.tbs_min_bodies_inside
        purged = tbs.high_swept or tbs.low_swept
        return bool(enough_bodies and purged and not tbs.equal_highs_inside
                    and tbs.direction != "none")
