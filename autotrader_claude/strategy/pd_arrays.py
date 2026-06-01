"""
PD Array detectors — the 12 ICT Premium/Discount arrays.

Each detector exposes:
    detect(df)               -> List[<dataclass>]
    nearest(df, price, dir)  -> Optional[<dataclass>]

All scanning uses numpy vectorised operations where practical. DataFrames are
expected to carry lowercase columns: open / high / low / close / volume and a
DatetimeIndex (the index is not required for detection).

Dataclasses use only standard python types (float/int/str/bool) so they
serialise cleanly and never leak numpy scalar types.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional

from config import StrategyParams


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _arrays(df: pd.DataFrame):
    """Return (open, high, low, close) numpy float arrays."""
    return (
        df["open"].to_numpy(dtype=float),
        df["high"].to_numpy(dtype=float),
        df["low"].to_numpy(dtype=float),
        df["close"].to_numpy(dtype=float),
    )


def _avg_range(highs: np.ndarray, lows: np.ndarray) -> float:
    if len(highs) == 0:
        return 0.0
    return float(np.mean(highs - lows))


def _nearest_by_midpoint(candidates, price: float):
    """Return the candidate whose midpoint is closest to price."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(price - c.midpoint))


# ──────────────────────────────────────────────────────────────────────────────
# 1. Order Block
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class OrderBlock:
    top: float
    bottom: float
    mt: float                # 50% midpoint (mean threshold)
    open_level: float        # OB candle open (validation level)
    midpoint: float
    index: int               # index of the OB candle
    direction: str           # 'bullish' | 'bearish'
    validated: bool          # impulse closed beyond OB open
    has_fvg: bool = False    # overlapping FVG = high probability


class OrderBlockDetector:
    """Bullish OB = last down-close candle before an up impulse.
    Bearish OB = last up-close candle before a down impulse."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[OrderBlock]:
        o, h, l, c = _arrays(df)
        n = len(df)
        if n < 4:
            return []
        lb = max(2, self.params.ob_lookback)
        start = max(1, n - lb - 2)
        rng = h - l
        avg = _avg_range(h, l)
        impulse_thr = avg * self.params.ob_impulse_factor
        obs: List[OrderBlock] = []

        for i in range(start, n - 1):
            impulse = h[i + 1] - l[i + 1]
            if impulse < impulse_thr:
                continue
            # Bullish OB: down candle (close<open) then up impulse closing above OB high
            if c[i] < o[i] and c[i + 1] > o[i + 1] and c[i + 1] > h[i]:
                top, bot = float(h[i]), float(l[i])
                obs.append(OrderBlock(
                    top=top, bottom=bot, mt=(top + bot) / 2.0,
                    open_level=float(o[i]), midpoint=(top + bot) / 2.0,
                    index=i, direction="bullish",
                    validated=bool(c[i + 1] > o[i]),
                ))
            # Bearish OB: up candle then down impulse closing below OB low
            elif c[i] > o[i] and c[i + 1] < o[i + 1] and c[i + 1] < l[i]:
                top, bot = float(h[i]), float(l[i])
                obs.append(OrderBlock(
                    top=top, bottom=bot, mt=(top + bot) / 2.0,
                    open_level=float(o[i]), midpoint=(top + bot) / 2.0,
                    index=i, direction="bearish",
                    validated=bool(c[i + 1] < o[i]),
                ))
        return obs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[OrderBlock]:
        obs = self.detect(df)
        want = "bullish" if direction == "long" else "bearish"
        cands = [ob for ob in obs if ob.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Propulsion Block
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PropulsionBlock:
    top: float
    bottom: float
    mt: float
    midpoint: float
    index: int
    direction: str           # 'bullish' | 'bearish'
    base_ob_index: int       # the first OB it propels off


class PropulsionBlockDetector:
    """A PB is an OB forming off a previous OB without closing beyond the first
    OB's 50% midpoint (MT)."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size
        self._ob = OrderBlockDetector(params, pip_size)

    def detect(self, df: pd.DataFrame) -> List[PropulsionBlock]:
        obs = self._ob.detect(df)
        pbs: List[PropulsionBlock] = []
        # Pair OBs of the same direction that are adjacent in formation.
        by_dir = {"bullish": [], "bearish": []}
        for ob in obs:
            by_dir[ob.direction].append(ob)
        tol = self.params.pb_mt_tolerance
        for direction, group in by_dir.items():
            group = sorted(group, key=lambda x: x.index)
            for a, b in zip(group, group[1:]):
                # second OB must not close beyond MT of the first
                if direction == "bullish":
                    respects = b.bottom >= a.mt - abs(a.top - a.bottom) * tol
                else:
                    respects = b.top <= a.mt + abs(a.top - a.bottom) * tol
                if respects:
                    pbs.append(PropulsionBlock(
                        top=b.top, bottom=b.bottom, mt=b.mt, midpoint=b.midpoint,
                        index=b.index, direction=direction, base_ob_index=a.index,
                    ))
        return pbs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[PropulsionBlock]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [p for p in self.detect(df) if p.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Mitigation Block
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class MitigationBlock:
    top: float
    bottom: float
    mt: float
    midpoint: float
    index: int
    direction: str           # 'bullish' | 'bearish'


def _swing_pivots(highs: np.ndarray, lows: np.ndarray, half: int):
    """Return (swing_high_idx, swing_low_idx) arrays using a half-window pivot."""
    n = len(highs)
    sh, sl = [], []
    for i in range(half, n - half):
        if highs[i] == np.max(highs[i - half: i + half + 1]):
            sh.append(i)
        if lows[i] == np.min(lows[i - half: i + half + 1]):
            sl.append(i)
    return sh, sl


class MitigationBlockDetector:
    """Failure swing + MSS. Bullish MB = highest up candle in the swing high
    that precedes a failure swing confirmed by a bullish MSS (and vice versa)."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[MitigationBlock]:
        o, h, l, c = _arrays(df)
        n = len(df)
        half = max(1, self.params.structure_swing_lookback)
        if n < half * 2 + 3:
            return []
        sh, sl = _swing_pivots(h, l, half)
        mbs: List[MitigationBlock] = []

        # Bullish MB: a swing low forms, price fails to take it out (higher low),
        # then closes above the prior swing high => bullish MSS.
        for j in range(1, len(sl)):
            prev_low_i, low_i = sl[j - 1], sl[j]
            if l[low_i] > l[prev_low_i]:  # failure swing (higher low)
                # find a swing high between them to break for MSS
                mids = [s for s in sh if prev_low_i < s < low_i]
                if not mids:
                    continue
                mss_level = h[mids[-1]]
                after = c[low_i + 1:]
                if after.size and np.any(after > mss_level):
                    # MB candle = highest up-close candle in [prev_low_i, low_i]
                    seg = range(prev_low_i, low_i + 1)
                    ups = [k for k in seg if c[k] > o[k]]
                    if ups:
                        k = max(ups, key=lambda x: h[x])
                        mbs.append(MitigationBlock(
                            top=float(h[k]), bottom=float(l[k]),
                            mt=float((h[k] + l[k]) / 2), midpoint=float((h[k] + l[k]) / 2),
                            index=int(k), direction="bullish",
                        ))

        # Bearish MB: a swing high forms, price fails to take it out (lower high),
        # then closes below prior swing low => bearish MSS.
        for j in range(1, len(sh)):
            prev_hi_i, hi_i = sh[j - 1], sh[j]
            if h[hi_i] < h[prev_hi_i]:
                mids = [s for s in sl if prev_hi_i < s < hi_i]
                if not mids:
                    continue
                mss_level = l[mids[-1]]
                after = c[hi_i + 1:]
                if after.size and np.any(after < mss_level):
                    seg = range(prev_hi_i, hi_i + 1)
                    downs = [k for k in seg if c[k] < o[k]]
                    if downs:
                        k = min(downs, key=lambda x: l[x])
                        mbs.append(MitigationBlock(
                            top=float(h[k]), bottom=float(l[k]),
                            mt=float((h[k] + l[k]) / 2), midpoint=float((h[k] + l[k]) / 2),
                            index=int(k), direction="bearish",
                        ))
        return mbs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[MitigationBlock]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [m for m in self.detect(df) if m.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Breaker Block
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class BreakerBlock:
    top: float
    bottom: float
    mt: float
    midpoint: float
    index: int
    direction: str           # 'bullish' | 'bearish'


class BreakerBlockDetector:
    """Stop-hunt that takes a previous swing then reverses with an MSS.
    Bullish BB = highest up-candles in the recent swing high BEFORE an old low
    was violated and then a bullish MSS prints (failed OB → breaker)."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[BreakerBlock]:
        o, h, l, c = _arrays(df)
        n = len(df)
        half = max(1, self.params.structure_swing_lookback)
        if n < half * 2 + 3:
            return []
        sh, sl = _swing_pivots(h, l, half)
        bbs: List[BreakerBlock] = []

        # Bullish BB: old low taken (sweep) then close back above prior swing high.
        for j in range(1, len(sl)):
            old_low_i, sweep_low_i = sl[j - 1], sl[j]
            if l[sweep_low_i] < l[old_low_i]:  # old low violated (stop hunt)
                mids = [s for s in sh if old_low_i < s < sweep_low_i]
                if not mids:
                    continue
                mss = h[mids[-1]]
                after = c[sweep_low_i + 1:]
                if after.size and np.any(after > mss):
                    seg = range(old_low_i, sweep_low_i + 1)
                    ups = [k for k in seg if c[k] > o[k]]
                    if ups:
                        k = max(ups, key=lambda x: h[x])
                        bbs.append(BreakerBlock(
                            top=float(h[k]), bottom=float(l[k]),
                            mt=float((h[k] + l[k]) / 2), midpoint=float((h[k] + l[k]) / 2),
                            index=int(k), direction="bullish",
                        ))

        # Bearish BB: old high taken then close back below prior swing low.
        for j in range(1, len(sh)):
            old_hi_i, sweep_hi_i = sh[j - 1], sh[j]
            if h[sweep_hi_i] > h[old_hi_i]:
                mids = [s for s in sl if old_hi_i < s < sweep_hi_i]
                if not mids:
                    continue
                mss = l[mids[-1]]
                after = c[sweep_hi_i + 1:]
                if after.size and np.any(after < mss):
                    seg = range(old_hi_i, sweep_hi_i + 1)
                    downs = [k for k in seg if c[k] < o[k]]
                    if downs:
                        k = min(downs, key=lambda x: l[x])
                        bbs.append(BreakerBlock(
                            top=float(h[k]), bottom=float(l[k]),
                            mt=float((h[k] + l[k]) / 2), midpoint=float((h[k] + l[k]) / 2),
                            index=int(k), direction="bearish",
                        ))
        return bbs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[BreakerBlock]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [b for b in self.detect(df) if b.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Rejection Block
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class RejectionBlock:
    top: float               # extreme of the wick
    bottom: float            # body extreme where the wick starts
    ce: float                # consequent encroachment = midpoint of the wick
    midpoint: float
    index: int
    direction: str           # 'bullish' | 'bearish'
    wick_pct: float


class RejectionBlockDetector:
    """Significant wick. Bullish RB starts at lowest body open/close down to the
    wick low (rejection from below). Bearish RB from highest body up to wick high.
    CE = midpoint of the wick."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[RejectionBlock]:
        o, h, l, c = _arrays(df)
        n = len(df)
        if n < 1:
            return []
        rng = h - l
        rng_safe = np.where(rng == 0, np.nan, rng)
        body_top = np.maximum(o, c)
        body_bot = np.minimum(o, c)
        upper_wick = h - body_top
        lower_wick = body_bot - l
        upper_pct = np.nan_to_num(upper_wick / rng_safe)
        lower_pct = np.nan_to_num(lower_wick / rng_safe)
        thr = self.params.rb_min_wick_pct
        rbs: List[RejectionBlock] = []

        for i in range(n):
            # Bearish RB: large upper wick (rejection from above)
            if upper_pct[i] >= thr:
                top, bot = float(h[i]), float(body_top[i])
                rbs.append(RejectionBlock(
                    top=top, bottom=bot, ce=(top + bot) / 2.0,
                    midpoint=(top + bot) / 2.0, index=i,
                    direction="bearish", wick_pct=float(upper_pct[i]),
                ))
            # Bullish RB: large lower wick (rejection from below)
            if lower_pct[i] >= thr:
                top, bot = float(body_bot[i]), float(l[i])
                rbs.append(RejectionBlock(
                    top=top, bottom=bot, ce=(top + bot) / 2.0,
                    midpoint=(top + bot) / 2.0, index=i,
                    direction="bullish", wick_pct=float(lower_pct[i]),
                ))
        return rbs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[RejectionBlock]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [r for r in self.detect(df) if r.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Immediate Rebalance (IRB)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class IRB:
    top: float
    bottom: float
    midpoint: float
    index: int               # index of candle 1 of the 3-candle pattern
    direction: str           # 'bullish' | 'bearish'


class ImmediateRebalanceDetector:
    """3-candle pattern where the 3rd candle pulls back to candle-1's high
    (bullish IRB) or low (bearish IRB) leaving NO FVG. Very strong continuation."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[IRB]:
        o, h, l, c = _arrays(df)
        n = len(df)
        if n < 3:
            return []
        tol_rng = (h - l)
        irbs: List[IRB] = []
        for i in range(n - 2):
            c1h, c1l = h[i], l[i]
            c3h, c3l = h[i + 2], l[i + 2]
            avg = max((tol_rng[i] + tol_rng[i + 1] + tol_rng[i + 2]) / 3.0, 1e-9)
            tol = avg * self.params.irb_tolerance_pct
            # Bullish IRB: up move, c3 pulls back to c1 high (no gap left, c3 low ~ c1 high)
            if c[i + 1] > o[i + 1] and c[i + 2] < o[i + 2] and abs(c3l - c1h) <= tol and c3l >= c1h - tol:
                top, bot = float(max(c1h, c3l)), float(min(c1h, c3l))
                if top == bot:
                    top += avg * 0.01
                irbs.append(IRB(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                index=i, direction="bullish"))
            # Bearish IRB: down move, c3 pulls back to c1 low
            elif c[i + 1] < o[i + 1] and c[i + 2] > o[i + 2] and abs(c3h - c1l) <= tol and c3h <= c1l + tol:
                top, bot = float(max(c1l, c3h)), float(min(c1l, c3h))
                if top == bot:
                    top += avg * 0.01
                irbs.append(IRB(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                index=i, direction="bearish"))
        return irbs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[IRB]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [x for x in self.detect(df) if x.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 7. Liquidity Void (LV)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class LiquidityVoid:
    top: float
    bottom: float
    midpoint: float
    index: int               # index of the second candle
    direction: str           # 'bullish' | 'bearish'


class LiquidityVoidDetector:
    """A true gap with NO overlapping wicks between consecutive candles.
    Bullish LV: c2.low > c1.high. Bearish LV: c2.high < c1.low."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[LiquidityVoid]:
        o, h, l, c = _arrays(df)
        n = len(df)
        if n < 2:
            return []
        lvs: List[LiquidityVoid] = []
        # vectorised gap masks
        bull = l[1:] > h[:-1]
        bear = h[1:] < l[:-1]
        for k in np.nonzero(bull)[0]:
            i = int(k) + 1
            top, bot = float(l[i]), float(h[i - 1])
            lvs.append(LiquidityVoid(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                     index=i, direction="bullish"))
        for k in np.nonzero(bear)[0]:
            i = int(k) + 1
            top, bot = float(l[i - 1]), float(h[i])
            lvs.append(LiquidityVoid(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                     index=i, direction="bearish"))
        return lvs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[LiquidityVoid]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [x for x in self.detect(df) if x.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 8. Volume Imbalance (VI)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class VolumeImbalance:
    top: float
    bottom: float
    midpoint: float
    index: int               # index of the second candle
    direction: str           # 'bullish' | 'bearish'


class VolumeImbalanceDetector:
    """Two candles whose bodies do not overlap but whose wicks do.
    Bullish VI: c2.open > c1.close (gap between bodies) while wicks still cross."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[VolumeImbalance]:
        o, h, l, c = _arrays(df)
        n = len(df)
        if n < 2:
            return []
        vis: List[VolumeImbalance] = []
        for i in range(1, n):
            c1_close = c[i - 1]
            c2_open = o[i]
            # Bullish VI: body gap up but wicks overlap (c2 low <= c1 high)
            if c2_open > c1_close and l[i] <= h[i - 1] and c2_open > c1_close:
                top, bot = float(c2_open), float(c1_close)
                if top > bot:
                    vis.append(VolumeImbalance(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                               index=i, direction="bullish"))
            # Bearish VI: body gap down but wicks overlap
            elif c2_open < c1_close and h[i] >= l[i - 1]:
                top, bot = float(c1_close), float(c2_open)
                if top > bot:
                    vis.append(VolumeImbalance(top=top, bottom=bot, midpoint=(top + bot) / 2.0,
                                               index=i, direction="bearish"))
        return vis

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[VolumeImbalance]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [x for x in self.detect(df) if x.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 9. Balanced Price Range (BPR)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class BPR:
    top: float
    bottom: float
    eq: float                # equilibrium = midpoint
    midpoint: float
    index: int               # index where the second FVG forms
    direction: str           # 'bullish' | 'bearish'


def _detect_fvgs_raw(o, h, l, c, min_pips: float, pip_size: float):
    """Lightweight FVG detection returning tuples (idx2, top, bottom, direction)."""
    n = len(h)
    out = []
    for i in range(2, n):
        if h[i - 2] < l[i]:
            if (l[i] - h[i - 2]) / pip_size >= min_pips:
                out.append((i - 1, float(l[i]), float(h[i - 2]), "bullish"))
        elif l[i - 2] > h[i]:
            if (l[i - 2] - h[i]) / pip_size >= min_pips:
                out.append((i - 1, float(l[i - 2]), float(h[i]), "bearish"))
    return out


class BPRDetector:
    """Two overlapping FVGs of opposite direction. The BPR spans from the high of
    the highest FVG to the low of the lowest FVG; EQ is the midpoint."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[BPR]:
        o, h, l, c = _arrays(df)
        fvgs = _detect_fvgs_raw(o, h, l, c, self.params.fvg_min_size_pips, self.pip_size)
        bprs: List[BPR] = []
        # pair opposite-direction FVGs whose ranges overlap
        for a in range(len(fvgs)):
            ia, ta, ba, da = fvgs[a]
            for b in range(a + 1, len(fvgs)):
                ib, tb, bb, db = fvgs[b]
                if da == db:
                    continue
                lo = max(ba, bb)
                hi = min(ta, tb)
                if hi > lo:  # overlap exists
                    top = max(ta, tb)
                    bot = min(ba, bb)
                    eq = (top + bot) / 2.0
                    # bullish BPR if the later FVG is bullish (support), else bearish
                    later_dir = db if ib > ia else da
                    bprs.append(BPR(top=float(top), bottom=float(bot), eq=eq,
                                    midpoint=eq, index=int(max(ia, ib)),
                                    direction=later_dir))
        return bprs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[BPR]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [x for x in self.detect(df) if x.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# 10. Inversion FVG (IFVG)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class IFVG:
    top: float
    bottom: float
    ce: float                # consequent encroachment (midpoint)
    midpoint: float
    index: int               # index where the FVG originally formed (candle 2)
    direction: str           # 'bullish' | 'bearish' (the inverted/new direction)


class InversionFVGDetector:
    """A failed FVG that gets body-closed through becomes an inversion FVG.
    A bullish FVG (BISI) body-closed below inverts to bearish.
    A bearish FVG (SIBI) body-closed above inverts to bullish."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    def detect(self, df: pd.DataFrame) -> List[IFVG]:
        o, h, l, c = _arrays(df)
        n = len(df)
        fvgs = _detect_fvgs_raw(o, h, l, c, self.params.fvg_min_size_pips, self.pip_size)
        ifvgs: List[IFVG] = []
        for idx2, top, bottom, direction in fvgs:
            after_close = c[idx2 + 1:]
            if after_close.size == 0:
                continue
            if direction == "bullish":
                # bullish FVG fails when a body closes below its bottom
                if np.any(after_close < bottom):
                    ce = (top + bottom) / 2.0
                    ifvgs.append(IFVG(top=float(top), bottom=float(bottom), ce=ce,
                                      midpoint=ce, index=int(idx2), direction="bearish"))
            else:
                if np.any(after_close > top):
                    ce = (top + bottom) / 2.0
                    ifvgs.append(IFVG(top=float(top), bottom=float(bottom), ce=ce,
                                      midpoint=ce, index=int(idx2), direction="bullish"))
        return ifvgs

    def nearest(self, df: pd.DataFrame, price: float, direction: str) -> Optional[IFVG]:
        want = "bullish" if direction == "long" else "bearish"
        cands = [x for x in self.detect(df) if x.direction == want]
        return _nearest_by_midpoint(cands, price)


# ──────────────────────────────────────────────────────────────────────────────
# Aggregate scanner
# ──────────────────────────────────────────────────────────────────────────────
# Priority order per spec: BB > MB > OB > PB > RB > IRB > FVG > IFVG > VI > LV > BPR
PD_PRIORITY = ["BB", "MB", "OB", "PB", "RB", "IRB", "IFVG", "VI", "LV", "BPR"]


@dataclass
class PDArrayHit:
    kind: str                # 'OB','PB','MB','BB','RB','IRB','LV','VI','BPR','IFVG'
    direction: str           # 'bullish' | 'bearish'
    top: float
    bottom: float
    midpoint: float
    index: int
    obj: object = field(default=None, repr=False)

    @property
    def quality(self) -> float:
        return {
            "OB": 2.0, "BB": 2.0, "MB": 2.0, "PB": 2.0,
            "IFVG": 1.5, "FVG": 1.5, "IRB": 1.5, "LV": 1.5, "BPR": 1.5,
            "RB": 1.0, "VI": 1.0,
        }.get(self.kind, 1.0)


class PDArrayScanner:
    """Runs every PD array detector once and exposes prioritised lookups."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size
        self.detectors = {
            "OB": OrderBlockDetector(params, pip_size),
            "PB": PropulsionBlockDetector(params, pip_size),
            "MB": MitigationBlockDetector(params, pip_size),
            "BB": BreakerBlockDetector(params, pip_size),
            "RB": RejectionBlockDetector(params, pip_size),
            "IRB": ImmediateRebalanceDetector(params, pip_size),
            "LV": LiquidityVoidDetector(params, pip_size),
            "VI": VolumeImbalanceDetector(params, pip_size),
            "BPR": BPRDetector(params, pip_size),
            "IFVG": InversionFVGDetector(params, pip_size),
        }

    def scan_all(self, df: pd.DataFrame) -> List[PDArrayHit]:
        hits: List[PDArrayHit] = []
        for kind, det in self.detectors.items():
            for obj in det.detect(df):
                hits.append(PDArrayHit(
                    kind=kind, direction=obj.direction,
                    top=float(obj.top), bottom=float(obj.bottom),
                    midpoint=float(obj.midpoint), index=int(obj.index), obj=obj,
                ))
        return hits

    def best_entry(self, df: pd.DataFrame, price: float, direction: str) -> Optional[PDArrayHit]:
        """Return the highest-priority PD array nearest to price in direction."""
        want = "bullish" if direction == "long" else "bearish"
        hits = [h for h in self.scan_all(df) if h.direction == want]
        if not hits:
            return None
        prio = {k: i for i, k in enumerate(PD_PRIORITY)}
        hits.sort(key=lambda h: (prio.get(h.kind, 99), abs(price - h.midpoint)))
        return hits[0]
