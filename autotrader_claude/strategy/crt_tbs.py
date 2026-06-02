"""
CRT (Candle Range Theory) + TBS (Turtle Body Soup) — multi-timeframe edition.

CRT reference candle is identified on LTF1. Its High/Low defines the range.
TBS confirmation (bodies grouping inside the CRT range) is then watched on LTF2.

Supported CRT→TBS timeframe pairs (from ICT material):
    1H  → 1M    (standard intraday)
    4H  → 5M    (preferred — preferred higher-probability pair ✓)
    D1  → 1H    (swing intraday)
    W1  → 4H    (swing preferred ✓)
    MN  → D1    (position trading)

TBS entry models (all three may fire simultaneously):
    Model 1 — Order Block entry (last OB on LTF2 inside CRT range)
    Model 2 — FVG entry (FVG gap on LTF2 inside CRT range)
    Model 3 — BOS entry (first close beyond swing high/low on LTF2)

AMD 3-Candle Pattern (from CRT AMD image):
    Candle 1: CRT reference candle (large range — HIGH/OPEN/CLOSE/LOW set)
    Candle 2: Accumulation — consolidation near the CRT OPEN (body stays inside)
    Candle 3: Manipulation — spike above CRT HIGH (bearish) or below CRT LOW (bullish)
              → Entry is taken at/after the Manipulation candle close
              → Distribution follows (the actual directional move)
    Rule: the manipulation (entry trigger) must appear on the 3rd candle only.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from config import StrategyParams


# All valid CRT→TBS timeframe pairs.  Marked ✓ = preferred by ICT.
CRT_TBS_PAIRS: List[Tuple[str, str, bool]] = [
    ("1H",  "M1",  False),   # 1H CRT → 1M TBS
    ("4H",  "5M",  True),    # 4H CRT → 5M TBS  ✓ preferred
    ("D1",  "1H",  False),   # Daily CRT → 1H TBS
    ("W1",  "4H",  True),    # Weekly CRT → 4H TBS  ✓ preferred
    ("MN",  "D1",  False),   # Monthly CRT → Daily TBS
]

# Map timeframe label → ratio relative to H4 (used to derive df slices)
TF_RATIO: dict = {
    "M1": 1/240, "5M": 1/48, "15M": 1/16, "30M": 1/8,
    "1H": 1/4, "H1": 1/4, "2H": 1/2, "4H": 1, "H4": 1,
    "6H": 1.5, "D1": 6, "W1": 30, "MN": 130,
}


@dataclass
class AMDPattern:
    """3-candle AMD: Accumulation → Manipulation → Distribution.

    Candle 1 (crt_index)    : CRT reference candle — sets HIGH/OPEN/CLOSE/LOW range
    Candle 2 (accum_index)  : Accumulation — body stays inside CRT range, near OPEN
    Candle 3 (manip_index)  : Manipulation — spike above HIGH (bearish) or below LOW (bullish)
    Entry: at Manipulation candle close (or first retrace OB/FVG after close)
    """
    crt_index: int
    accum_index: int
    manip_index: int        # must equal crt_index + 2  (exactly 3rd candle)
    crt_high: float
    crt_low: float
    crt_open: float
    crt_close: float
    direction: str          # 'bullish' (low swept) | 'bearish' (high swept)
    manip_price: float      # the swept extreme on the manipulation candle
    entry_price: float      # close of manipulation candle = distribution entry
    stop_price: float       # beyond the manipulation extreme + buffer
    valid: bool


@dataclass
class CRT:
    index: int               # bar index inside the CRT dataframe
    high: float
    low: float
    midpoint: float
    range_size: float
    timeframe: str = "4H"    # which timeframe this CRT was detected on


@dataclass
class TBS:
    crt_index: int
    bodies_inside: int
    high_swept: bool         # OHP — CRT high purged on LTF2
    low_swept: bool          # OLP — CRT low purged on LTF2
    equal_highs_inside: bool
    direction: str           # 'bullish' | 'bearish' | 'none'
    timeframe: str = "5M"    # LTF2 where TBS was detected
    entry_model: str = "none"  # 'ob' | 'fvg' | 'bos' | 'none'
    entry_price: float = 0.0
    stop_price: float = 0.0


@dataclass
class MultiTFCRTResult:
    """Result from running one CRT+TBS timeframe pair."""
    crt_tf: str
    tbs_tf: str
    preferred: bool
    crt: Optional[CRT]
    tbs: Optional[TBS]
    is_high_prob: bool
    direction: str    # 'long' | 'short' | 'none'
    entry_model: str  # 'ob' | 'fvg' | 'bos' | 'none'
    entry_price: float
    stop_price: float


class CRTTBSDetector:
    """Detects CRT + TBS across all supported multi-timeframe pairs."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    # ── CRT detection (on LTF1 / CRT timeframe) ──────────────────────────

    def detect_crt(self, df: pd.DataFrame, timeframe: str = "4H") -> Optional[CRT]:
        """Find the most recent CRT reference candle on the given dataframe.
        The CRT candle must have range >= crt_range_factor × avg_range.
        Leaves at least 2 bars after it for TBS confirmation.
        """
        n = len(df)
        if n < 5:
            return None
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        rng = h - l
        avg = float(np.mean(rng))
        thr = avg * self.params.crt_range_factor
        for i in range(n - 3, -1, -1):
            if rng[i] >= thr and rng[i] > 0:
                return CRT(
                    index=i, high=float(h[i]), low=float(l[i]),
                    midpoint=float((h[i] + l[i]) / 2),
                    range_size=float(rng[i]),
                    timeframe=timeframe,
                )
        return None

    # ── TBS detection (on LTF2 / TBS timeframe) ──────────────────────────

    def detect_tbs(self, df: pd.DataFrame, crt: CRT,
                   timeframe: str = "5M") -> Optional[TBS]:
        """Detect Turtle Body Soup on the TBS timeframe dataframe.
        Uses the CRT price range from LTF1 — df here is the LTF2 dataframe.
        Only bars AFTER the CRT candle's approximate time are inspected;
        when no time alignment is available the full df is used.
        """
        if crt is None:
            return None
        n = len(df)
        if n < 3:
            return None
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)

        # Use the full LTF2 slice — caller is responsible for passing only
        # the window that follows the CRT candle on LTF1.
        body_top = np.maximum(o, c)
        body_bot = np.minimum(o, c)
        inside = (body_top <= crt.high) & (body_bot >= crt.low)
        bodies_inside = int(np.sum(inside))

        high_swept = bool(np.any(h > crt.high))
        low_swept = bool(np.any(l < crt.low))

        inside_highs = h[inside] if inside.any() else np.array([])
        equal_highs = self._has_equal(inside_highs)

        direction = "none"
        if low_swept and not high_swept:
            direction = "bullish"
        elif high_swept and not low_swept:
            direction = "bearish"
        elif high_swept and low_swept:
            hi_bars = np.nonzero(h > crt.high)[0]
            lo_bars = np.nonzero(l < crt.low)[0]
            last_hi = int(hi_bars[-1]) if hi_bars.size else -1
            last_lo = int(lo_bars[-1]) if lo_bars.size else -1
            direction = "bearish" if last_hi > last_lo else "bullish"

        # ── Entry model selection (Model 1 OB > Model 2 FVG > Model 3 BOS) ─
        # Convert bullish/bearish to long/short expected by _best_entry_model
        entry_dir = "long" if direction == "bullish" else ("short" if direction == "bearish" else "none")
        entry_model, entry_price, stop_price = self._best_entry_model(
            o, h, l, c, crt, entry_dir)

        return TBS(
            crt_index=crt.index,
            bodies_inside=bodies_inside,
            high_swept=high_swept,
            low_swept=low_swept,
            equal_highs_inside=equal_highs,
            direction=direction,
            timeframe=timeframe,
            entry_model=entry_model,
            entry_price=entry_price,
            stop_price=stop_price,
        )

    def _best_entry_model(
        self, o, h, l, c, crt: CRT, direction: str
    ) -> Tuple[str, float, float]:
        """Try entry Model 1 (OB) → Model 2 (FVG) → Model 3 (BOS).
        Returns (model_name, entry_price, stop_price).
        """
        buf = self.pip_size * 5
        n = len(c)

        # Model 1 — Order Block: last bearish candle before a bullish impulse
        # (long) or last bullish candle before a bearish impulse (short)
        if direction == "long" and n >= 3:
            for i in range(n - 3, 0, -1):
                if o[i] > c[i] and c[i] >= crt.low:  # bearish OB candle
                    entry = float(h[i])  # top of OB
                    stop = float(l[i]) - buf
                    if crt.low <= entry <= crt.high:
                        return "ob", entry, stop
        elif direction == "short" and n >= 3:
            for i in range(n - 3, 0, -1):
                if c[i] > o[i] and h[i] <= crt.high:  # bullish OB candle
                    entry = float(l[i])  # bottom of OB
                    stop = float(h[i]) + buf
                    if crt.low <= entry <= crt.high:
                        return "ob", entry, stop

        # Model 2 — FVG: gap between candle[i-1].high and candle[i+1].low (bullish)
        if n >= 3:
            for i in range(1, n - 1):
                if direction == "long":
                    gap_bot = h[i - 1]
                    gap_top = l[i + 1]
                    if gap_top > gap_bot and crt.low <= gap_bot <= crt.high:
                        mid = (gap_bot + gap_top) / 2
                        return "fvg", float(mid), float(gap_bot) - buf
                elif direction == "short":
                    gap_bot = h[i + 1]
                    gap_top = l[i - 1]
                    if gap_top > gap_bot and crt.low <= gap_bot <= crt.high:
                        mid = (gap_bot + gap_top) / 2
                        return "fvg", float(mid), float(gap_top) + buf

        # Model 3 — BOS: first close beyond the last swing inside the CRT range
        if n >= 4:
            if direction == "long":
                swing_h = float(np.max(h[:-1]))
                if c[-1] > swing_h and crt.low <= swing_h <= crt.high:
                    return "bos", float(c[-1]), float(l[-1]) - buf
            elif direction == "short":
                swing_l = float(np.min(l[:-1]))
                if c[-1] < swing_l and crt.low <= swing_l <= crt.high:
                    return "bos", float(c[-1]), float(h[-1]) + buf

        # Fallback — midpoint entry
        mid = crt.midpoint
        stop = (crt.low - buf) if direction == "long" else (crt.high + buf)
        return "none", mid, stop

    # ── Multi-TF runner ───────────────────────────────────────────────────

    def detect_multi_tf(
        self,
        crt_df: pd.DataFrame,
        tbs_df: pd.DataFrame,
        crt_tf: str = "4H",
        tbs_tf: str = "5M",
    ) -> MultiTFCRTResult:
        """Run a full CRT+TBS analysis across one timeframe pair.

        crt_df  = LTF1 dataframe (CRT reference candle lives here)
        tbs_df  = LTF2 dataframe (TBS bodies confirmed here)
        """
        preferred = any(
            p == crt_tf and t == tbs_tf and pref
            for p, t, pref in CRT_TBS_PAIRS
        )
        crt = self.detect_crt(crt_df, timeframe=crt_tf)
        tbs = None
        high_prob = False
        direction = "none"
        entry_model = "none"
        entry_price = 0.0
        stop_price = 0.0

        if crt is not None:
            tbs = self.detect_tbs(tbs_df, crt, timeframe=tbs_tf)
            high_prob = self.is_high_probability_tbs(crt, tbs)
            if high_prob and tbs:
                direction = "long" if tbs.direction == "bullish" else (
                    "short" if tbs.direction == "bearish" else "none")
                entry_model = tbs.entry_model
                entry_price = tbs.entry_price
                stop_price = tbs.stop_price

        return MultiTFCRTResult(
            crt_tf=crt_tf, tbs_tf=tbs_tf, preferred=preferred,
            crt=crt, tbs=tbs, is_high_prob=high_prob,
            direction=direction, entry_model=entry_model,
            entry_price=entry_price, stop_price=stop_price,
        )

    def detect_all_pairs(
        self,
        dfs: dict,
    ) -> List[MultiTFCRTResult]:
        """Run all 5 CRT+TBS pairs.

        dfs = dict of {timeframe_label: pd.DataFrame}
        Missing timeframes are skipped.
        Results are sorted: preferred pairs first, then by direction confidence.
        """
        results = []
        for crt_tf, tbs_tf, _pref in CRT_TBS_PAIRS:
            if crt_tf in dfs and tbs_tf in dfs:
                r = self.detect_multi_tf(dfs[crt_tf], dfs[tbs_tf], crt_tf, tbs_tf)
                results.append(r)
        results.sort(key=lambda r: (not r.preferred, not r.is_high_prob))
        return results

    # ── Quality gate ──────────────────────────────────────────────────────

    def is_high_probability_tbs(self, crt: Optional[CRT], tbs: Optional[TBS]) -> bool:
        """High probability: ≥ min bodies inside, one side purged, no equal highs."""
        if crt is None or tbs is None:
            return False
        return bool(
            tbs.bodies_inside >= self.params.tbs_min_bodies_inside
            and (tbs.high_swept or tbs.low_swept)
            and not tbs.equal_highs_inside
            and tbs.direction != "none"
        )

    # ── AMD 3-Candle Pattern ──────────────────────────────────────────────

    def detect_amd(self, df: pd.DataFrame) -> Optional[AMDPattern]:
        """Detect the most recent valid AMD 3-candle CRT pattern.

        Rules (strict):
          Candle 1 (CRT): range >= crt_range_factor × avg_range
          Candle 2 (Accum): body stays inside [CRT.low, CRT.high], near CRT.open
                            (body_top <= CRT.high AND body_bot >= CRT.low)
          Candle 3 (Manip): spike above CRT.high (bearish) OR below CRT.low (bullish)
                            AND it is EXACTLY the 3rd candle after CRT (index+2)
          Entry = close of Candle 3 (distribution starts here)
          SL = beyond the spike extreme + 5 pip buffer
        """
        n = len(df)
        if n < 5:
            return None
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        rng = h - l
        avg_rng = float(np.mean(rng))
        thr = avg_rng * self.params.crt_range_factor
        buf = self.pip_size * 5

        # Search backwards for CRT candle — needs candle+1 and candle+2 after it
        for i in range(n - 3, 0, -1):
            if rng[i] < thr or rng[i] == 0:
                continue
            crt_h, crt_l = float(h[i]), float(l[i])
            crt_o, crt_c = float(o[i]), float(c[i])

            # Candle 2 (i+1): Accumulation — body inside CRT range
            a = i + 1
            a_body_top = max(o[a], c[a])
            a_body_bot = min(o[a], c[a])
            accum_ok = (a_body_top <= crt_h) and (a_body_bot >= crt_l)
            if not accum_ok:
                continue

            # Candle 3 (i+2): Manipulation — spike beyond CRT high or low
            m = i + 2
            high_swept = h[m] > crt_h
            low_swept = l[m] < crt_l

            if not (high_swept or low_swept):
                continue

            # If both sides swept, whichever is MORE extreme decides direction
            if high_swept and low_swept:
                # spike above = bearish if high extension > low extension
                hi_ext = h[m] - crt_h
                lo_ext = crt_l - l[m]
                high_swept = hi_ext >= lo_ext
                low_swept = not high_swept

            if high_swept:
                direction = "bearish"
                manip_price = float(h[m])
                entry_price = float(c[m])   # close of manipulation candle
                stop_price = manip_price + buf
            else:
                direction = "bullish"
                manip_price = float(l[m])
                entry_price = float(c[m])
                stop_price = manip_price - buf

            return AMDPattern(
                crt_index=i,
                accum_index=a,
                manip_index=m,
                crt_high=crt_h, crt_low=crt_l,
                crt_open=crt_o, crt_close=crt_c,
                direction=direction,
                manip_price=manip_price,
                entry_price=entry_price,
                stop_price=stop_price,
                valid=True,
            )
        return None

    def scan_amd_all(self, df: pd.DataFrame) -> List[AMDPattern]:
        """Scan the full dataframe and return ALL valid AMD patterns found
        (sorted most recent first). Useful for pre-computing in backtester."""
        n = len(df)
        if n < 5:
            return []
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        rng = h - l
        avg_rng = float(np.mean(rng))
        thr = avg_rng * self.params.crt_range_factor
        buf = self.pip_size * 5
        results = []

        for i in range(1, n - 2):
            if rng[i] < thr or rng[i] == 0:
                continue
            crt_h, crt_l = float(h[i]), float(l[i])
            crt_o, crt_c = float(o[i]), float(c[i])
            a = i + 1
            a_body_top = max(o[a], c[a])
            a_body_bot = min(o[a], c[a])
            if not ((a_body_top <= crt_h) and (a_body_bot >= crt_l)):
                continue
            m = i + 2
            high_swept = h[m] > crt_h
            low_swept = l[m] < crt_l
            if not (high_swept or low_swept):
                continue
            if high_swept and low_swept:
                hi_ext = h[m] - crt_h
                lo_ext = crt_l - l[m]
                high_swept = hi_ext >= lo_ext
                low_swept = not high_swept
            if high_swept:
                direction, manip_price = "bearish", float(h[m])
                entry_price = float(c[m])
                stop_price = manip_price + buf
            else:
                direction, manip_price = "bullish", float(l[m])
                entry_price = float(c[m])
                stop_price = manip_price - buf
            results.append(AMDPattern(
                crt_index=i, accum_index=a, manip_index=m,
                crt_high=crt_h, crt_low=crt_l, crt_open=crt_o, crt_close=crt_c,
                direction=direction, manip_price=manip_price,
                entry_price=entry_price, stop_price=stop_price, valid=True,
            ))

        results.reverse()
        return results

    # ── Helpers ───────────────────────────────────────────────────────────

    def _has_equal(self, values: np.ndarray) -> bool:
        if values.size < 2:
            return False
        tol = self.params.equal_level_tolerance_pips * self.pip_size
        for v in values:
            if np.sum(np.abs(values - v) <= tol) >= 2:
                return True
        return False
