"""
Double Purge Theory.

Both BSL (buy-side liquidity / highs) and SSL (sell-side liquidity / lows) get
swept before the real directional move. Three formations:

    classic     : higher-high sweep then lower-low sweep (or vice versa), then
                  the real move runs opposite to the LAST purge.
    one_candle  : a single candle sweeps both the range high AND low then closes
                  back inside.
    amd         : Accumulation -> Manipulation (purges both sides) -> Distribution.

direction = 'bullish' (last purge took SSL -> expect up) or 'bearish'.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional

from config import StrategyParams


@dataclass
class DoublePurge:
    formation: str           # 'classic' | 'one_candle' | 'amd'
    direction: str           # 'bullish' | 'bearish'
    high_swept: float
    low_swept: float
    index: int               # bar index where the double purge completes
    description: str


class DoublePurgeDetector:
    """Detects double purges across a lookback window."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame, lookback: int = 50) -> List[DoublePurge]:
        n = len(df)
        if n < 5:
            return []
        lb = min(lookback, n)
        sub = df.iloc[-lb:]
        o = sub["open"].to_numpy(dtype=float)
        h = sub["high"].to_numpy(dtype=float)
        l = sub["low"].to_numpy(dtype=float)
        c = sub["close"].to_numpy(dtype=float)
        offset = n - lb
        out: List[DoublePurge] = []

        out += self._one_candle(o, h, l, c, offset)
        out += self._classic(h, l, c, offset)
        out += self._amd(h, l, c, offset)
        return out

    # ── one candle purge ──────────────────────────────────────────────────
    def _one_candle(self, o, h, l, c, offset) -> List[DoublePurge]:
        out = []
        m = len(h)
        win = max(3, self.params.structure_swing_lookback)
        for i in range(win, m):
            prior_high = np.max(h[max(0, i - win):i])
            prior_low = np.min(l[max(0, i - win):i])
            took_high = h[i] > prior_high
            took_low = l[i] < prior_low
            closed_inside = prior_low <= c[i] <= prior_high
            if took_high and took_low and closed_inside:
                direction = "bullish" if c[i] > o[i] else "bearish"
                out.append(DoublePurge(
                    formation="one_candle", direction=direction,
                    high_swept=float(prior_high), low_swept=float(prior_low),
                    index=offset + i,
                    description="single candle swept both sides, closed inside",
                ))
        return out

    # ── classic MSS trap ──────────────────────────────────────────────────
    def _classic(self, h, l, c, offset) -> List[DoublePurge]:
        out = []
        m = len(h)
        win = max(3, self.params.structure_swing_lookback)
        for i in range(win, m - 1):
            ref_high = np.max(h[max(0, i - win):i])
            ref_low = np.min(l[max(0, i - win):i])
            # high taken first, then low taken shortly after -> expect bullish
            if h[i] > ref_high:
                fwd = l[i + 1:i + 1 + win]
                if fwd.size and np.min(fwd) < ref_low:
                    out.append(DoublePurge(
                        formation="classic", direction="bullish",
                        high_swept=float(ref_high), low_swept=float(ref_low),
                        index=offset + i,
                        description="HH sweep then LL sweep — real move up",
                    ))
            elif l[i] < ref_low:
                fwd = h[i + 1:i + 1 + win]
                if fwd.size and np.max(fwd) > ref_high:
                    out.append(DoublePurge(
                        formation="classic", direction="bearish",
                        high_swept=float(ref_high), low_swept=float(ref_low),
                        index=offset + i,
                        description="LL sweep then HH sweep — real move down",
                    ))
        return out

    # ── AMD purge ─────────────────────────────────────────────────────────
    def _amd(self, h, l, c, offset) -> List[DoublePurge]:
        out = []
        m = len(h)
        if m < 9:
            return out
        third = m // 3
        accum = slice(0, third)
        manip = slice(third, 2 * third)
        acc_high = np.max(h[accum])
        acc_low = np.min(l[accum])
        manip_high = np.max(h[manip])
        manip_low = np.min(l[manip])
        # manipulation must purge both accumulation extremes
        if manip_high > acc_high and manip_low < acc_low:
            last_close = c[-1]
            mid = (acc_high + acc_low) / 2.0
            direction = "bullish" if last_close > mid else "bearish"
            out.append(DoublePurge(
                formation="amd", direction=direction,
                high_swept=float(acc_high), low_swept=float(acc_low),
                index=offset + 2 * third,
                description="AMD: manipulation purged both sides of accumulation",
            ))
        return out

    def get_latest(self, df: pd.DataFrame, lookback: int = 50) -> Optional[DoublePurge]:
        hits = self.detect(df, lookback=lookback)
        if not hits:
            return None
        return max(hits, key=lambda d: d.index)
