"""
Intelligent Stop Loss Engine — structure-aware SL placement.

Replaces ATR-only stop logic with:
  - Structure-aware placement beyond significant swing high/low
  - Liquidity-aware: avoids equal highs/lows (stop clusters)
  - Volatility-adjusted buffer per pair personality
  - Round number avoidance
  - Session-volatility survival check

Usage:
    engine = IntelligentSLEngine()
    sl_price = engine.calculate_sl(df, direction, entry_price, pair, atr)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple


# ── Pair personality (pair-specific noise buffers) ────────────────────────────
PAIR_NOISE_BUFFER_ATR = {
    "XAUUSD": 0.6,   # Gold needs wider SL — high volatility
    "GC=F":   0.6,
    "BTCUSD": 0.8,   # Crypto needs widest buffer
    "ETHUSD": 0.8,
    "GBPUSD": 0.4,
    "EURUSD": 0.35,
    "USDJPY": 0.4,
    "USDCHF": 0.35,
    "AUDUSD": 0.4,
    "NZDUSD": 0.4,
    "USDCAD": 0.4,
    "EURJPY": 0.5,
    "GBPJPY": 0.6,
    "NAS100": 0.7,
    "US30":   0.7,
    "GER40":  0.7,
    "SI=F":   0.6,   # Silver
    "XAGUSD": 0.6,
    "XPTUSD": 0.7,
}
DEFAULT_NOISE_BUFFER = 0.5


@dataclass
class SLResult:
    sl_price: float
    distance_r: float           # distance in R (should be ≥ 1.0)
    swing_used: float           # the swing level we placed behind
    buffer_used: float          # ATR buffer added
    quality: str                # "good" | "acceptable" | "forced"
    notes: str                  = ""


class IntelligentSLEngine:
    """
    Calculates structure-aware stop loss placement.
    """

    def calculate_sl(
        self,
        df: pd.DataFrame,
        direction: str,
        entry_price: float,
        pair: str = "XAUUSD",
        atr: Optional[float] = None,
        lookback: int = 20,
    ) -> SLResult:
        """
        Calculate the optimal SL price for a trade.

        Args:
            df: OHLCV DataFrame (most recent bar = current).
            direction: 'long' or 'short'.
            entry_price: intended entry price.
            pair: trading pair (used for noise buffer).
            atr: current ATR value (computed from df if not provided).
            lookback: bars to look back for significant swings.

        Returns:
            SLResult with sl_price and quality assessment.
        """
        if len(df) < max(lookback, 10):
            return self._fallback_sl(df, direction, entry_price, pair)

        if atr is None or atr <= 0:
            atr = _compute_atr(df)

        noise_buf = PAIR_NOISE_BUFFER_ATR.get(pair, DEFAULT_NOISE_BUFFER) * atr

        # 1. Find significant swing high/low
        swing = self._find_significant_swing(df, direction, lookback)

        # 2. Check liquidity quality of that swing
        swing, quality, notes = self._avoid_liquidity_cluster(
            df, direction, swing, atr, lookback
        )

        # 3. Add noise buffer
        if direction == "long":
            sl = swing - noise_buf
        else:
            sl = swing + noise_buf

        # 4. Avoid round numbers
        sl = self._dodge_round_number(sl, atr, direction)

        # 5. Validate minimum SL distance (1× ATR minimum from entry)
        min_distance = atr * 1.0
        actual_distance = abs(entry_price - sl)
        if actual_distance < min_distance:
            # Widen to minimum
            if direction == "long":
                sl = entry_price - min_distance
            else:
                sl = entry_price + min_distance
            quality = "forced"
            notes += " | widened_to_min_distance"

        # 6. Cap at risk management maximum (3× ATR from entry)
        max_distance = atr * 3.0
        if actual_distance > max_distance:
            if direction == "long":
                sl = entry_price - max_distance
            else:
                sl = entry_price + max_distance
            quality = "forced"
            notes += " | capped_at_max_distance"

        distance_r = abs(entry_price - sl) / atr if atr > 0 else 1.0

        return SLResult(
            sl_price=round(sl, _price_decimals(pair)),
            distance_r=round(distance_r, 2),
            swing_used=round(swing, _price_decimals(pair)),
            buffer_used=round(noise_buf, _price_decimals(pair)),
            quality=quality,
            notes=notes.strip(" |"),
        )

    def _find_significant_swing(
        self,
        df: pd.DataFrame,
        direction: str,
        lookback: int,
    ) -> float:
        """
        Find the most significant swing low (for longs) or swing high (for shorts)
        within lookback bars. Uses fractal-like detection (3-bar minimum).
        """
        window = df.iloc[-lookback:]

        if direction == "long":
            # Find swing lows: bars where low is lower than both neighbours
            lows = window["low"].values
            swing_lows = []
            for i in range(1, len(lows) - 1):
                if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
                    swing_lows.append(lows[i])
            if swing_lows:
                return min(swing_lows[-3:])  # most recent significant low
            return window["low"].min()
        else:
            highs = window["high"].values
            swing_highs = []
            for i in range(1, len(highs) - 1):
                if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
                    swing_highs.append(highs[i])
            if swing_highs:
                return max(swing_highs[-3:])  # most recent significant high
            return window["high"].max()

    def _avoid_liquidity_cluster(
        self,
        df: pd.DataFrame,
        direction: str,
        swing: float,
        atr: float,
        lookback: int,
    ) -> Tuple[float, str, str]:
        """
        Check if `swing` is at a liquidity cluster (equal highs/lows).
        If so, move the swing beyond the cluster.

        Returns (adjusted_swing, quality, notes).
        """
        window = df.iloc[-lookback:]
        cluster_tolerance = atr * 0.15  # levels within 0.15 ATR = "equal"
        notes = ""
        quality = "good"

        if direction == "long":
            # Check for equal lows near our swing
            lows = window["low"].values
            equals_near = sum(1 for l in lows if abs(l - swing) < cluster_tolerance)
            if equals_near >= 3:
                # Strong equal lows = stop cluster — go below the cluster
                cluster_bottom = min(l for l in lows if abs(l - swing) < cluster_tolerance)
                swing = cluster_bottom - atr * 0.1
                quality = "acceptable"
                notes = "equal_lows_cluster_avoided"
        else:
            highs = window["high"].values
            equals_near = sum(1 for h in highs if abs(h - swing) < cluster_tolerance)
            if equals_near >= 3:
                cluster_top = max(h for h in highs if abs(h - swing) < cluster_tolerance)
                swing = cluster_top + atr * 0.1
                quality = "acceptable"
                notes = "equal_highs_cluster_avoided"

        return swing, quality, notes

    def _dodge_round_number(self, sl: float, atr: float, direction: str) -> float:
        """
        Avoid placing SL at round numbers (e.g., 2300.00, 1.2000).
        If SL is within 0.1×ATR of a round number, nudge it away.
        """
        round_magnitude = _round_number_magnitude(sl)
        nearest_round = round(sl / round_magnitude) * round_magnitude
        proximity = abs(sl - nearest_round)
        nudge_threshold = atr * 0.1

        if proximity < nudge_threshold:
            if direction == "long":
                sl = nearest_round - nudge_threshold
            else:
                sl = nearest_round + nudge_threshold

        return sl

    def _fallback_sl(
        self,
        df: pd.DataFrame,
        direction: str,
        entry_price: float,
        pair: str,
    ) -> SLResult:
        """Simple ATR-based fallback when insufficient data."""
        atr = _compute_atr(df) if len(df) >= 5 else entry_price * 0.001
        noise_buf = PAIR_NOISE_BUFFER_ATR.get(pair, DEFAULT_NOISE_BUFFER) * atr

        if direction == "long":
            sl = entry_price - atr - noise_buf
        else:
            sl = entry_price + atr + noise_buf

        return SLResult(
            sl_price=round(sl, _price_decimals(pair)),
            distance_r=1.0,
            swing_used=sl,
            buffer_used=noise_buf,
            quality="forced",
            notes="fallback_atr_only",
        )


def validate_sl(
    sl_price: float,
    entry_price: float,
    pair: str,
    atr: float,
    account_balance: float = 10000.0,
    risk_pct: float = 0.01,
) -> dict:
    """
    Validate that an SL is safe:
      - Minimum 1× ATR from entry
      - Maximum 3× ATR from entry (risk management cap)
      - Can survive normal session volatility (1.5× ATR move)
      - Position size within 1% risk rule

    Returns dict with 'valid', 'issues', 'recommended_lot_size'.
    """
    issues = []
    distance = abs(entry_price - sl_price)
    min_dist = atr
    max_dist = atr * 3.0

    if distance < min_dist:
        issues.append(f"SL too tight: {distance:.5f} < min {min_dist:.5f} (1× ATR)")
    if distance > max_dist:
        issues.append(f"SL too wide: {distance:.5f} > max {max_dist:.5f} (3× ATR)")

    # Volatility survival: SL must survive 1.5× ATR move
    survival_dist = atr * 1.5
    if distance < survival_dist:
        issues.append(f"SL may not survive session volatility (need ≥ {survival_dist:.5f})")

    # Round number check
    round_mag = _round_number_magnitude(sl_price)
    nearest = round(sl_price / round_mag) * round_mag
    if abs(sl_price - nearest) < atr * 0.05:
        issues.append("SL is at round number — possible stop cluster")

    # Lot size for 1% risk
    dollar_risk = account_balance * risk_pct
    lot_size = dollar_risk / (distance * 100) if distance > 0 else 0.01
    lot_size = max(0.01, min(lot_size, 10.0))

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "recommended_lot_size": round(lot_size, 2),
        "distance_atr": round(distance / atr, 2) if atr > 0 else 0,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute current ATR from DataFrame."""
    if len(df) < period:
        high = df["high"].iloc[-1]
        low  = df["low"].iloc[-1]
        return (high - low) * 2.0
    high = df["high"]
    low  = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])


def _price_decimals(pair: str) -> int:
    """Return appropriate decimal places for rounding."""
    if pair in ("XAUUSD", "GC=F", "SI=F", "XAGUSD"):
        return 2
    if pair in ("BTCUSD", "ETHUSD"):
        return 0
    if pair in ("NAS100", "US30", "GER40"):
        return 1
    if pair in ("USDJPY", "EURJPY", "GBPJPY"):
        return 3
    return 5


def _round_number_magnitude(price: float) -> float:
    """Return the round-number magnitude for a price."""
    if price > 10000:
        return 100.0
    elif price > 1000:
        return 50.0
    elif price > 100:
        return 5.0
    elif price > 10:
        return 1.0
    elif price > 1:
        return 0.1
    else:
        return 0.001
