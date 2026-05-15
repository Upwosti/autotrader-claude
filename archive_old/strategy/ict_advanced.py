"""ICT Advanced Concepts — IFVG, Order Blocks, Breaker Blocks, MSS, OTE, etc."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR (Average True Range) for a DataFrame."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _swing_highs(series: pd.Series, lookback: int = 5) -> pd.Series:
    """Return a boolean series marking swing highs."""
    result = pd.Series(False, index=series.index)
    for i in range(lookback, len(series) - lookback):
        window = series.iloc[i - lookback : i + lookback + 1]
        if series.iloc[i] == window.max():
            result.iloc[i] = True
    return result


def _swing_lows(series: pd.Series, lookback: int = 5) -> pd.Series:
    """Return a boolean series marking swing lows."""
    result = pd.Series(False, index=series.index)
    for i in range(lookback, len(series) - lookback):
        window = series.iloc[i - lookback : i + lookback + 1]
        if series.iloc[i] == window.min():
            result.iloc[i] = True
    return result


def _fvg_zones(df: pd.DataFrame) -> List[Dict]:
    """Detect raw Fair Value Gaps (3-candle pattern)."""
    zones: List[Dict] = []
    if len(df) < 3:
        return zones
    for i in range(1, len(df) - 1):
        prev_high = df["high"].iloc[i - 1]
        prev_low = df["low"].iloc[i - 1]
        next_high = df["high"].iloc[i + 1]
        next_low = df["low"].iloc[i + 1]
        # Bullish FVG: gap between prev_high and next_low (next_low > prev_high)
        if next_low > prev_high:
            zones.append(
                {
                    "type": "bullish",
                    "high": next_low,
                    "low": prev_high,
                    "midpoint": (next_low + prev_high) / 2,
                    "bar_index": i,
                    "filled": False,
                }
            )
        # Bearish FVG: gap between prev_low and next_high (next_high < prev_low)
        if next_high < prev_low:
            zones.append(
                {
                    "type": "bearish",
                    "high": prev_low,
                    "low": next_high,
                    "midpoint": (prev_low + next_high) / 2,
                    "bar_index": i,
                    "filled": False,
                }
            )
    return zones


# ---------------------------------------------------------------------------
# 1. IFVG Detector
# ---------------------------------------------------------------------------

class IFVGDetector:
    """Detect Inverse Fair Value Gaps (previously filled FVGs acting as S/R)."""

    def __init__(self, lookback: int = 50) -> None:
        self.lookback = lookback

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        """Detect IFVGs in the supplied DataFrame.

        Returns a list of dicts with keys:
            type, high, low, midpoint, bar_index, strength
        """
        if len(df) < 5:
            return []

        window = df.tail(self.lookback).reset_index(drop=True)
        raw_fvgs = _fvg_zones(window)
        ifvgs: List[Dict] = []

        for fvg in raw_fvgs:
            fvg_high = fvg["high"]
            fvg_low = fvg["low"]
            bar_idx = fvg["bar_index"]

            # Check bars AFTER the FVG formation for a fill (price entered the gap)
            filled = False
            touched_after_fill = False
            fill_bar = None

            for j in range(bar_idx + 1, len(window)):
                bar = window.iloc[j]
                if fvg["type"] == "bullish":
                    if bar["low"] <= fvg_high and bar["high"] >= fvg_low:
                        filled = True
                        fill_bar = j
                        break
                else:  # bearish
                    if bar["high"] >= fvg_low and bar["low"] <= fvg_high:
                        filled = True
                        fill_bar = j
                        break

            if not filled or fill_bar is None:
                continue

            # After fill, check if price revisited the zone (acting as S/R)
            for j in range(fill_bar + 1, len(window)):
                bar = window.iloc[j]
                if fvg["type"] == "bullish":
                    # Zone should now act as resistance (price tags it from below)
                    if bar["high"] >= fvg_low and bar["high"] <= fvg_high:
                        touched_after_fill = True
                        break
                else:
                    # Zone should now act as support (price tags it from above)
                    if bar["low"] <= fvg_high and bar["low"] >= fvg_low:
                        touched_after_fill = True
                        break

            if not touched_after_fill:
                continue

            # Strength = size of gap relative to recent ATR
            atr_val = _atr(window).iloc[-1]
            gap_size = fvg_high - fvg_low
            strength = min(100, int((gap_size / (atr_val + 1e-10)) * 50))

            ifvgs.append(
                {
                    "type": fvg["type"],
                    "high": fvg_high,
                    "low": fvg_low,
                    "midpoint": fvg["midpoint"],
                    "bar_index": bar_idx,
                    "strength": strength,
                }
            )

        return ifvgs


# ---------------------------------------------------------------------------
# 2. Order Block Detector
# ---------------------------------------------------------------------------

class OrderBlockDetector:
    """Detect Bullish and Bearish Order Blocks."""

    def __init__(self, displacement_atr_mult: float = 3.0, atr_period: int = 14) -> None:
        self.displacement_atr_mult = displacement_atr_mult
        self.atr_period = atr_period

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        """Detect order blocks.

        Returns list of dicts:
            type, high, low, midpoint, bar_index, displacement_atr, mitigated
        """
        if len(df) < self.atr_period + 3:
            return []

        atr = _atr(df, self.atr_period)
        obs: List[Dict] = []

        for i in range(1, len(df) - 1):
            current = df.iloc[i]
            atr_val = atr.iloc[i]
            if atr_val == 0:
                continue

            # Look for displacement starting from bar i+1
            max_look = min(i + 6, len(df))
            for j in range(i + 1, max_look):
                displacement = df["close"].iloc[j] - df["close"].iloc[i]
                disp_atr = abs(displacement) / atr_val

                if disp_atr >= self.displacement_atr_mult:
                    if displacement > 0:
                        # Bullish displacement — find last bearish candle before i+1
                        ob_bar = i
                        for k in range(i, -1, -1):
                            if df["close"].iloc[k] < df["open"].iloc[k]:
                                ob_bar = k
                                break
                        ob = df.iloc[ob_bar]
                        mitigated = self._is_mitigated(df, ob_bar, "bullish")
                        obs.append(
                            {
                                "type": "bullish",
                                "high": ob["high"],
                                "low": ob["low"],
                                "midpoint": (ob["high"] + ob["low"]) / 2,
                                "bar_index": ob_bar,
                                "displacement_atr": round(disp_atr, 2),
                                "mitigated": mitigated,
                            }
                        )
                    else:
                        # Bearish displacement — find last bullish candle before i+1
                        ob_bar = i
                        for k in range(i, -1, -1):
                            if df["close"].iloc[k] > df["open"].iloc[k]:
                                ob_bar = k
                                break
                        ob = df.iloc[ob_bar]
                        mitigated = self._is_mitigated(df, ob_bar, "bearish")
                        obs.append(
                            {
                                "type": "bearish",
                                "high": ob["high"],
                                "low": ob["low"],
                                "midpoint": (ob["high"] + ob["low"]) / 2,
                                "bar_index": ob_bar,
                                "displacement_atr": round(disp_atr, 2),
                                "mitigated": mitigated,
                            }
                        )
                    break  # one displacement per OB candidate

        # Deduplicate by bar_index, keep highest displacement
        seen: Dict[int, Dict] = {}
        for ob in obs:
            idx = ob["bar_index"]
            if idx not in seen or ob["displacement_atr"] > seen[idx]["displacement_atr"]:
                seen[idx] = ob
        return list(seen.values())

    @staticmethod
    def _is_mitigated(df: pd.DataFrame, ob_bar: int, ob_type: str) -> bool:
        ob = df.iloc[ob_bar]
        for j in range(ob_bar + 1, len(df)):
            bar = df.iloc[j]
            if ob_type == "bullish":
                if bar["low"] <= ob["high"] and bar["low"] >= ob["low"]:
                    return True
            else:
                if bar["high"] >= ob["low"] and bar["high"] <= ob["high"]:
                    return True
        return False


# ---------------------------------------------------------------------------
# 3. Breaker Block Detector
# ---------------------------------------------------------------------------

class BreakerBlockDetector:
    """Detect Breaker Blocks — mitigated OBs that then get broken (polarity flip)."""

    def __init__(self, displacement_atr_mult: float = 3.0) -> None:
        self._ob_detector = OrderBlockDetector(displacement_atr_mult)

    def detect(self, df: pd.DataFrame) -> List[Dict]:
        """Detect breaker blocks.

        Returns list of dicts:
            type, original_type, high, low, midpoint, bar_index, displacement_atr, mitigated
        """
        if len(df) < 20:
            return []

        order_blocks = self._ob_detector.detect(df)
        breakers: List[Dict] = []

        for ob in order_blocks:
            if not ob["mitigated"]:
                continue

            ob_bar = ob["bar_index"]
            ob_type = ob["type"]

            # Find the bar where mitigation occurred
            mitigation_bar = None
            for j in range(ob_bar + 1, len(df)):
                bar = df.iloc[j]
                if ob_type == "bullish":
                    if bar["low"] <= ob["high"] and bar["low"] >= ob["low"]:
                        mitigation_bar = j
                        break
                else:
                    if bar["high"] >= ob["low"] and bar["high"] <= ob["high"]:
                        mitigation_bar = j
                        break

            if mitigation_bar is None:
                continue

            # Check if price then breaks THROUGH the OB completely
            broken_through = False
            for j in range(mitigation_bar + 1, len(df)):
                bar = df.iloc[j]
                if ob_type == "bullish":
                    # Bullish OB broken to downside → bearish breaker
                    if bar["close"] < ob["low"]:
                        broken_through = True
                        breaker_type = "bearish"
                        break
                else:
                    # Bearish OB broken to upside → bullish breaker
                    if bar["close"] > ob["high"]:
                        broken_through = True
                        breaker_type = "bullish"
                        break

            if not broken_through:
                continue

            breakers.append(
                {
                    "type": breaker_type,
                    "original_type": ob_type,
                    "high": ob["high"],
                    "low": ob["low"],
                    "midpoint": ob["midpoint"],
                    "bar_index": ob_bar,
                    "displacement_atr": ob["displacement_atr"],
                    "mitigated": True,
                }
            )

        return breakers


# ---------------------------------------------------------------------------
# 4. MSS Detector — Market Structure Shift
# ---------------------------------------------------------------------------

class MSSDetector:
    """Detect Market Structure Shifts (ChoCH indicating trend reversal)."""

    def __init__(self, swing_lookback: int = 10) -> None:
        self.swing_lookback = swing_lookback

    def detect(self, df: pd.DataFrame) -> Optional[Dict]:
        """Detect the most recent MSS.

        Returns dict with keys:
            direction, bar_index, price, swing_broken, confidence
        or None if no MSS found.
        """
        if len(df) < self.swing_lookback * 3:
            return None

        highs = _swing_highs(df["high"], self.swing_lookback)
        lows = _swing_lows(df["low"], self.swing_lookback)

        swing_high_bars = [i for i in range(len(df)) if highs.iloc[i]]
        swing_low_bars = [i for i in range(len(df)) if lows.iloc[i]]

        if len(swing_high_bars) < 2 or len(swing_low_bars) < 2:
            return None

        # Determine prior trend direction using last two swing highs/lows
        last_sh = swing_high_bars[-1]
        prev_sh = swing_high_bars[-2]
        last_sl = swing_low_bars[-1]
        prev_sl = swing_low_bars[-2]

        higher_highs = df["high"].iloc[last_sh] > df["high"].iloc[prev_sh]
        higher_lows = df["low"].iloc[last_sl] > df["low"].iloc[prev_sl]
        lower_highs = df["high"].iloc[last_sh] < df["high"].iloc[prev_sh]
        lower_lows = df["low"].iloc[last_sl] < df["low"].iloc[prev_sl]

        current_price = df["close"].iloc[-1]

        # Bullish MSS: was in downtrend (lower highs + lower lows), price breaks above last swing high
        if lower_highs and lower_lows:
            swing_level = df["high"].iloc[last_sh]
            if current_price > swing_level:
                confidence = self._calc_confidence(df, "bullish", swing_level)
                return {
                    "direction": "bullish",
                    "bar_index": len(df) - 1,
                    "price": current_price,
                    "swing_broken": swing_level,
                    "confidence": confidence,
                }

        # Bearish MSS: was in uptrend (higher highs + higher lows), price breaks below last swing low
        if higher_highs and higher_lows:
            swing_level = df["low"].iloc[last_sl]
            if current_price < swing_level:
                confidence = self._calc_confidence(df, "bearish", swing_level)
                return {
                    "direction": "bearish",
                    "bar_index": len(df) - 1,
                    "price": current_price,
                    "swing_broken": swing_level,
                    "confidence": confidence,
                }

        return None

    def _calc_confidence(self, df: pd.DataFrame, direction: str, swing_level: float) -> int:
        """Estimate confidence 0-100 based on break strength and volume."""
        current_price = df["close"].iloc[-1]
        atr_val = _atr(df).iloc[-1]
        if atr_val == 0:
            return 50

        break_size = abs(current_price - swing_level) / atr_val
        size_score = min(40, int(break_size * 20))

        # Volume confirmation
        avg_vol = df["volume"].tail(20).mean()
        last_vol = df["volume"].iloc[-1]
        vol_score = min(30, int((last_vol / (avg_vol + 1e-10)) * 15))

        # Trend consistency score
        trend_score = 30  # base

        return min(100, size_score + vol_score + trend_score)


# ---------------------------------------------------------------------------
# 5. OTE Calculator — Optimal Trade Entry
# ---------------------------------------------------------------------------

class OTECalculator:
    """Calculate Optimal Trade Entry zones (62–79% Fibonacci retracement)."""

    FIB_0618 = 0.618
    FIB_0705 = 0.705
    FIB_0786 = 0.786

    def calc(self, swing_low: float, swing_high: float, direction: str) -> Dict:
        """Compute OTE levels for a given swing.

        Args:
            swing_low: Swing low price.
            swing_high: Swing high price.
            direction: 'long' or 'short'.

        Returns:
            Dict with entry_low, entry_high, midpoint, fib_0618, fib_0705, fib_0786.
        """
        rng = swing_high - swing_low
        if rng <= 0:
            mid = (swing_high + swing_low) / 2
            return {
                "entry_low": mid,
                "entry_high": mid,
                "midpoint": mid,
                "fib_0618": mid,
                "fib_0705": mid,
                "fib_0786": mid,
            }

        fib_0618 = swing_high - rng * self.FIB_0618
        fib_0705 = swing_high - rng * self.FIB_0705
        fib_0786 = swing_high - rng * self.FIB_0786

        if direction == "long":
            # For long: retracement into 62-79% zone from swing high
            entry_low = swing_high - rng * 0.79
            entry_high = swing_high - rng * 0.62
        else:
            # For short: retracement back up into 21-38% zone from swing low
            entry_low = swing_low + rng * 0.21
            entry_high = swing_low + rng * 0.38
            # Recalculate fibs relative to short direction
            fib_0618 = swing_low + rng * self.FIB_0618
            fib_0705 = swing_low + rng * self.FIB_0705
            fib_0786 = swing_low + rng * self.FIB_0786

        midpoint = (entry_low + entry_high) / 2

        return {
            "entry_low": round(entry_low, 6),
            "entry_high": round(entry_high, 6),
            "midpoint": round(midpoint, 6),
            "fib_0618": round(fib_0618, 6),
            "fib_0705": round(fib_0705, 6),
            "fib_0786": round(fib_0786, 6),
        }


# ---------------------------------------------------------------------------
# 6. Turtle Soup Detector
# ---------------------------------------------------------------------------

class TurtleSoupDetector:
    """Detect Turtle Soup reversal patterns (false breakouts of 20-bar highs/lows)."""

    def __init__(self, lookback: int = 20, reversal_bars: int = 3) -> None:
        self.lookback = lookback
        self.reversal_bars = reversal_bars

    def detect(self, df: pd.DataFrame) -> Optional[Dict]:
        """Detect the most recent Turtle Soup pattern.

        Returns dict with keys:
            direction, swept_level, entry, bar_index
        or None.
        """
        if len(df) < self.lookback + self.reversal_bars + 2:
            return None

        # Check last few bars for a sweep + reversal
        search_start = max(self.lookback, len(df) - 10)
        for i in range(len(df) - 1, search_start - 1, -1):
            # 20-bar range BEFORE bar i
            window = df.iloc[i - self.lookback : i]
            period_high = window["high"].max()
            period_low = window["low"].min()

            current = df.iloc[i]

            # Bearish Turtle Soup: break above 20-bar high then reversal
            if current["high"] > period_high:
                # Check if within reversal_bars price closes back below the high
                reversal_end = min(i + self.reversal_bars + 1, len(df))
                for j in range(i + 1, reversal_end):
                    if df["close"].iloc[j] < period_high:
                        return {
                            "direction": "short",
                            "swept_level": period_high,
                            "entry": period_high,
                            "bar_index": i,
                        }

            # Bullish Turtle Soup: break below 20-bar low then reversal
            if current["low"] < period_low:
                reversal_end = min(i + self.reversal_bars + 1, len(df))
                for j in range(i + 1, reversal_end):
                    if df["close"].iloc[j] > period_low:
                        return {
                            "direction": "long",
                            "swept_level": period_low,
                            "entry": period_low,
                            "bar_index": i,
                        }

        return None


# ---------------------------------------------------------------------------
# 7. SMT Divergence Detector
# ---------------------------------------------------------------------------

class SMTDivergenceDetector:
    """Detect SMT (Smart Money Technique) divergence between correlated pairs."""

    def __init__(self, lookback: int = 10) -> None:
        self.lookback = lookback

    def detect(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        pair1: str,
        pair2: str,
    ) -> Optional[Dict]:
        """Detect SMT divergence between two correlated instruments.

        Returns dict with keys:
            direction, pair1_high, pair2_high, divergence_pct, bar_index
        or None.
        """
        min_len = min(len(df1), len(df2))
        if min_len < self.lookback + 2:
            return None

        df1 = df1.tail(min_len).reset_index(drop=True)
        df2 = df2.tail(min_len).reset_index(drop=True)

        n = len(df1)
        window1 = df1.tail(self.lookback)
        window2 = df2.tail(self.lookback)

        high1 = window1["high"].max()
        high2 = window2["high"].max()
        low1 = window1["low"].min()
        low2 = window2["low"].min()

        high1_bar = window1["high"].idxmax()
        high2_bar = window2["high"].idxmax()
        low1_bar = window1["low"].idxmin()
        low2_bar = window2["low"].idxmin()

        # Bearish SMT: pair1 makes new high but pair2 fails to confirm
        pair1_recent_high = df1["high"].iloc[n - self.lookback - 1 : n - self.lookback].max() if n > self.lookback else df1["high"].iloc[0]
        pair2_recent_high = df2["high"].iloc[n - self.lookback - 1 : n - self.lookback].max() if n > self.lookback else df2["high"].iloc[0]

        p1_new_high = high1 > pair1_recent_high
        p2_new_high = high2 > pair2_recent_high

        if p1_new_high and not p2_new_high:
            div_pct = abs(high1 - high2) / (high1 + 1e-10) * 100
            return {
                "direction": "bearish",
                "pair1_high": round(high1, 6),
                "pair2_high": round(high2, 6),
                "pair1": pair1,
                "pair2": pair2,
                "divergence_pct": round(div_pct, 4),
                "bar_index": int(high1_bar),
            }

        # Bullish SMT: pair1 makes new low but pair2 doesn't
        pair1_recent_low = df1["low"].iloc[n - self.lookback - 1 : n - self.lookback].min() if n > self.lookback else df1["low"].iloc[0]
        pair2_recent_low = df2["low"].iloc[n - self.lookback - 1 : n - self.lookback].min() if n > self.lookback else df2["low"].iloc[0]

        p1_new_low = low1 < pair1_recent_low
        p2_new_low = low2 < pair2_recent_low

        if p1_new_low and not p2_new_low:
            div_pct = abs(low1 - low2) / (low1 + 1e-10) * 100
            return {
                "direction": "bullish",
                "pair1_low": round(low1, 6),
                "pair2_low": round(low2, 6),
                "pair1": pair1,
                "pair2": pair2,
                "divergence_pct": round(div_pct, 4),
                "bar_index": int(low1_bar),
            }

        return None


# ---------------------------------------------------------------------------
# 8. Dealing Range Detector
# ---------------------------------------------------------------------------

class DealingRangeDetector:
    """Detect Premium/Discount zones and ERL/IRL levels within a dealing range."""

    def __init__(self) -> None:
        self._fvg_detector = IFVGDetector(lookback=30)
        self._ob_detector = OrderBlockDetector()

    def detect(self, df: pd.DataFrame, lookback: int = 20) -> Dict:
        """Analyse the dealing range for a given lookback window.

        Returns dict with keys:
            range_high, range_low, midpoint, premium_zone_start, discount_zone_start,
            premium_pct, discount_pct, current_zone, erl_levels, irl_levels
        """
        if len(df) < lookback:
            lookback = len(df)

        window = df.tail(lookback)
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())
        midpoint = (range_high + range_low) / 2
        current_price = float(df["close"].iloc[-1])

        premium_zone_start = midpoint  # above midpoint = premium
        discount_zone_start = midpoint  # below midpoint = discount

        rng = range_high - range_low
        if rng > 0:
            position_in_range = (current_price - range_low) / rng
        else:
            position_in_range = 0.5

        premium_pct = max(0.0, min(1.0, position_in_range))
        discount_pct = 1.0 - premium_pct

        if position_in_range > 0.55:
            current_zone = "premium"
        elif position_in_range < 0.45:
            current_zone = "discount"
        else:
            current_zone = "equilibrium"

        # ERL: equal highs/lows just outside the range (liquidity clusters)
        erl_levels = self._find_erl(window, range_high, range_low)

        # IRL: FVGs and OBs within the range
        irl_levels = self._find_irl(df, range_high, range_low)

        return {
            "range_high": round(range_high, 6),
            "range_low": round(range_low, 6),
            "midpoint": round(midpoint, 6),
            "premium_zone_start": round(premium_zone_start, 6),
            "discount_zone_start": round(discount_zone_start, 6),
            "premium_pct": round(premium_pct, 4),
            "discount_pct": round(discount_pct, 4),
            "current_zone": current_zone,
            "erl_levels": [round(v, 6) for v in erl_levels],
            "irl_levels": [round(v, 6) for v in irl_levels],
        }

    @staticmethod
    def _find_erl(window: pd.DataFrame, range_high: float, range_low: float) -> List[float]:
        """Find equal highs/lows near range extremes (external liquidity)."""
        tolerance = (range_high - range_low) * 0.005
        erl: List[float] = []

        high_clusters: Dict[float, int] = {}
        low_clusters: Dict[float, int] = {}

        for _, row in window.iterrows():
            h = row["high"]
            l = row["low"]
            # Group highs
            found = False
            for key in list(high_clusters.keys()):
                if abs(h - key) <= tolerance:
                    high_clusters[key] += 1
                    found = True
                    break
            if not found:
                high_clusters[h] = 1
            # Group lows
            found = False
            for key in list(low_clusters.keys()):
                if abs(l - key) <= tolerance:
                    low_clusters[key] += 1
                    found = True
                    break
            if not found:
                low_clusters[l] = 1

        # ERL = levels touched 2+ times near range extremes
        for level, count in high_clusters.items():
            if count >= 2 and abs(level - range_high) <= (range_high - range_low) * 0.03:
                erl.append(level)
        for level, count in low_clusters.items():
            if count >= 2 and abs(level - range_low) <= (range_high - range_low) * 0.03:
                erl.append(level)

        return sorted(set(erl))

    def _find_irl(self, df: pd.DataFrame, range_high: float, range_low: float) -> List[float]:
        """Find FVG midpoints and OB midpoints within the dealing range (internal liquidity)."""
        irl: List[float] = []

        # FVG midpoints inside range
        fvgs = _fvg_zones(df.tail(50).reset_index(drop=True))
        for fvg in fvgs:
            mid = fvg["midpoint"]
            if range_low <= mid <= range_high:
                irl.append(mid)

        # OB midpoints inside range
        obs = self._ob_detector.detect(df.tail(50).reset_index(drop=True))
        for ob in obs:
            mid = ob["midpoint"]
            if range_low <= mid <= range_high:
                irl.append(mid)

        return sorted(set(round(v, 6) for v in irl))


# ---------------------------------------------------------------------------
# 9. Killzone Timer
# ---------------------------------------------------------------------------

class KillzoneTimer:
    """ICT Time and Price theory — killzone detection."""

    # Killzones defined as (name, start_hour_utc, start_min, end_hour_utc, end_min, optimal)
    KILLZONES = [
        ("Asia",    23, 0, 2,  0,  False),
        ("London",   7, 0, 10, 0,  True),
        ("NY AM",   12, 0, 15, 0,  True),
        ("NY PM",   19, 0, 21, 0,  False),
    ]

    def get_current_killzone(self, dt: datetime) -> Dict:
        """Return killzone status for the given UTC datetime.

        Returns dict with keys:
            in_killzone, session, killzone_name, minutes_remaining, optimal
        """
        # Normalise to UTC
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            dt_utc = dt

        hour = dt_utc.hour
        minute = dt_utc.minute
        total_minutes = hour * 60 + minute

        for name, sh, sm, eh, em, optimal in self.KILLZONES:
            start_mins = sh * 60 + sm
            end_mins = eh * 60 + em

            # Handle overnight sessions (Asia spans midnight)
            if start_mins > end_mins:
                in_zone = total_minutes >= start_mins or total_minutes < end_mins
                if in_zone:
                    if total_minutes >= start_mins:
                        mins_rem = (24 * 60 - total_minutes) + end_mins
                    else:
                        mins_rem = end_mins - total_minutes
                    session = self._session_from_name(name)
                    return {
                        "in_killzone": True,
                        "session": session,
                        "killzone_name": name,
                        "minutes_remaining": mins_rem,
                        "optimal": optimal,
                    }
            else:
                if start_mins <= total_minutes < end_mins:
                    mins_rem = end_mins - total_minutes
                    session = self._session_from_name(name)
                    return {
                        "in_killzone": True,
                        "session": session,
                        "killzone_name": name,
                        "minutes_remaining": mins_rem,
                        "optimal": optimal,
                    }

        # Find next killzone
        next_kz, mins_to_next = self._next_killzone(total_minutes)
        return {
            "in_killzone": False,
            "session": "Off-session",
            "killzone_name": next_kz,
            "minutes_remaining": mins_to_next,
            "optimal": False,
        }

    def is_high_probability_time(self, dt: datetime) -> bool:
        """Return True if currently inside any killzone."""
        result = self.get_current_killzone(dt)
        return result["in_killzone"]

    @staticmethod
    def _session_from_name(name: str) -> str:
        mapping = {
            "Asia": "Asia",
            "London": "London",
            "NY AM": "New York",
            "NY PM": "New York",
        }
        return mapping.get(name, name)

    def _next_killzone(self, total_minutes: int) -> Tuple[str, int]:
        """Return (name, minutes_until_start) for the next upcoming killzone."""
        best_name = ""
        best_mins = 99999
        for name, sh, sm, _eh, _em, _opt in self.KILLZONES:
            start_mins = sh * 60 + sm
            diff = start_mins - total_minutes
            if diff < 0:
                diff += 24 * 60
            if diff < best_mins:
                best_mins = diff
                best_name = name
        return best_name, best_mins


# ---------------------------------------------------------------------------
# 10. ICT Advanced Scorer
# ---------------------------------------------------------------------------

class ICTAdvancedScorer:
    """Aggregate ICT signal scorer combining all advanced concepts."""

    def __init__(self) -> None:
        self._ifvg = IFVGDetector()
        self._ob = OrderBlockDetector()
        self._bb = BreakerBlockDetector()
        self._mss = MSSDetector()
        self._ote = OTECalculator()
        self._ts = TurtleSoupDetector()
        self._dr = DealingRangeDetector()
        self._kz = KillzoneTimer()

    def score(self, df: pd.DataFrame, direction: str, current_price: float) -> Dict:
        """Compute a composite ICT score for a potential trade.

        Args:
            df: OHLCV DataFrame (lowercase columns).
            direction: 'bullish' or 'bearish'.
            current_price: current market price.

        Returns:
            Dict with keys:
                total_score, signals_active, order_block_score,
                structure_score, timing_score, zone_score, pattern_score
        """
        signals_active: List[str] = []
        order_block_score = 0
        structure_score = 0
        timing_score = 0
        zone_score = 0
        pattern_score = 0

        # --- Order Block Score (0-20) ---
        try:
            obs = self._ob.detect(df)
            for ob in obs:
                if ob["type"] == direction and not ob["mitigated"]:
                    if ob["low"] <= current_price <= ob["high"]:
                        order_block_score = min(20, order_block_score + 10)
                        signals_active.append(f"order_block_{direction}")
            bbs = self._bb.detect(df)
            for bb in bbs:
                if bb["type"] == direction:
                    if bb["low"] <= current_price <= bb["high"]:
                        order_block_score = min(20, order_block_score + 7)
                        signals_active.append(f"breaker_block_{direction}")
            ifvgs = self._ifvg.detect(df)
            for ifvg in ifvgs:
                if ifvg["type"] == direction:
                    if ifvg["low"] <= current_price <= ifvg["high"]:
                        order_block_score = min(20, order_block_score + 5)
                        signals_active.append("ifvg_zone")
        except Exception:
            pass

        # --- Structure Score (0-20) ---
        try:
            mss = self._mss.detect(df)
            if mss and mss["direction"] == direction:
                conf = mss.get("confidence", 50)
                structure_score = min(20, int(conf / 5))
                signals_active.append("mss_confirmed")
        except Exception:
            pass

        # --- Timing Score (0-20) ---
        try:
            now = datetime.utcnow()
            kz = self._kz.get_current_killzone(now)
            if kz["in_killzone"]:
                timing_score = 20 if kz["optimal"] else 12
                signals_active.append(f"killzone_{kz['killzone_name'].lower().replace(' ', '_')}")
        except Exception:
            pass

        # --- Zone Score (0-20) ---
        try:
            dr = self._dr.detect(df)
            if direction == "bullish" and dr["current_zone"] == "discount":
                zone_score = 20
                signals_active.append("discount_zone")
            elif direction == "bearish" and dr["current_zone"] == "premium":
                zone_score = 20
                signals_active.append("premium_zone")
            elif dr["current_zone"] == "equilibrium":
                zone_score = 8
                signals_active.append("equilibrium_zone")
            # Check if near IRL levels
            for irl in dr.get("irl_levels", []):
                if abs(current_price - irl) / (current_price + 1e-10) < 0.002:
                    zone_score = min(20, zone_score + 5)
                    signals_active.append("near_irl")
                    break
        except Exception:
            pass

        # --- Pattern Score (0-20) ---
        try:
            ts = self._ts.detect(df)
            if ts:
                if (ts["direction"] == "long" and direction == "bullish") or \
                   (ts["direction"] == "short" and direction == "bearish"):
                    pattern_score = min(20, pattern_score + 15)
                    signals_active.append("turtle_soup")
        except Exception:
            pass

        # OTE check — adds to pattern score
        try:
            highs = _swing_highs(df["high"], 10)
            lows = _swing_lows(df["low"], 10)
            sh_bars = [i for i in range(len(df)) if highs.iloc[i]]
            sl_bars = [i for i in range(len(df)) if lows.iloc[i]]
            if sh_bars and sl_bars:
                swing_high = df["high"].iloc[sh_bars[-1]]
                swing_low = df["low"].iloc[sl_bars[-1]]
                ote = self._ote.calc(swing_low, swing_high, direction if direction in ("long", "short") else ("long" if direction == "bullish" else "short"))
                if ote["entry_low"] <= current_price <= ote["entry_high"]:
                    pattern_score = min(20, pattern_score + 10)
                    signals_active.append("ote_zone")
        except Exception:
            pass

        total_score = min(
            100,
            order_block_score + structure_score + timing_score + zone_score + pattern_score,
        )

        return {
            "total_score": total_score,
            "signals_active": list(set(signals_active)),
            "order_block_score": order_block_score,
            "structure_score": structure_score,
            "timing_score": timing_score,
            "zone_score": zone_score,
            "pattern_score": pattern_score,
        }
