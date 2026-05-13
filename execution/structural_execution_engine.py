"""
OMEGA Structural Execution Engine

Regime-aware SL, TP, and trailing calculation.
Stops using ATR-only logic. Uses:
  - Swing structure (significant highs/lows)
  - Volatility buffers (per-pair noise)
  - Regime multipliers (from market_regime_engine)
  - Liquidity positioning (avoids obvious stop clusters)

Targets: WR 55-65%, RRR 2.5-5.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False

# Pair-specific noise buffer (ATR multiples to add beyond structure)
PAIR_NOISE = {
    "XAUUSD": 0.50, "GC=F":  0.50,
    "XAGUSD": 0.45, "SI=F":  0.45,
    "BTCUSD": 0.80, "ETHUSD": 0.80,
    "GBPUSD": 0.35, "GBPJPY": 0.45,
    "EURUSD": 0.30, "EURJPY": 0.40,
    "USDJPY": 0.30, "USDCHF": 0.30,
    "AUDUSD": 0.30, "NZDUSD": 0.30,
    "USDCAD": 0.30, "NAS100": 0.60,
    "US30":   0.60, "GER40":  0.50,
}

# Round-number avoidance: nudge SL if within this fraction of ATR from round
ROUND_NUDGE_ATR = 0.15


@dataclass
class ExecutionLevels:
    sl_price: float
    tp_price: float
    trail_atr_mult: float
    partial_pct: float          # fraction to close at TP1
    tp1_price: float            # first partial close level
    risk_r: float               # distance entry→sl in price
    regime_label: str = ""
    notes: str = ""


def calculate_levels(
    df,
    direction: str,
    entry_price: float,
    pair: str,
    regime_state=None,
    lookback: int = 20,
    base_sl_atr: float = 1.0,
    base_tp_rrr: float = 4.0,
    base_trail: float  = 2.5,
    base_partial: float = 0.25,
) -> ExecutionLevels:
    """
    Calculate regime-aware SL, TP, trailing, and partial levels.

    Args:
        df: OHLCV DataFrame (at least lookback bars)
        direction: "long" | "short"
        entry_price: entry price
        pair: instrument name
        regime_state: RegimeState from market_regime_engine (optional)
        base_*: fallback values from params
    """
    if not _HAS_NP or df is None or len(df) < 10:
        # Fallback: pure ATR-based
        atr = float(df["close"].diff().abs().mean()) if df is not None else entry_price * 0.01
        sl_dist = base_sl_atr * atr
        tp_dist = base_tp_rrr * sl_dist
        sl = entry_price - sl_dist if direction == "long" else entry_price + sl_dist
        tp = entry_price + tp_dist if direction == "long" else entry_price - tp_dist
        return ExecutionLevels(
            sl_price=sl, tp_price=tp,
            trail_atr_mult=base_trail, partial_pct=base_partial,
            tp1_price=entry_price + sl_dist if direction=="long" else entry_price - sl_dist,
            risk_r=sl_dist, notes="fallback ATR"
        )

    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    n     = len(close)
    lb    = min(lookback, n-1)

    # ── ATR ───────────────────────────────────────────────────────────────────
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr  = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = float(np.mean(tr[-lb:]))

    # ── Swing structure SL ────────────────────────────────────────────────────
    if direction == "long":
        swing_sl = _find_swing_low(low[-lb:], entry_price, atr)
    else:
        swing_sl = _find_swing_high(high[-lb:], entry_price, atr)

    # Apply noise buffer
    noise_mult = PAIR_NOISE.get(pair, 0.35)
    if direction == "long":
        sl_price = swing_sl - noise_mult * atr
    else:
        sl_price = swing_sl + noise_mult * atr

    # Avoid round numbers
    sl_price = _avoid_round_number(sl_price, atr, direction)

    # Enforce minimum distance from entry (1×ATR)
    min_dist = max(base_sl_atr * atr, atr * 0.8)
    if direction == "long":
        sl_price = min(sl_price, entry_price - min_dist)
    else:
        sl_price = max(sl_price, entry_price + min_dist)

    # Cap at 3×ATR from entry
    max_dist = 3.0 * atr
    if direction == "long":
        sl_price = max(sl_price, entry_price - max_dist)
    else:
        sl_price = min(sl_price, entry_price + max_dist)

    risk_r = abs(entry_price - sl_price)

    # ── Apply regime multipliers ──────────────────────────────────────────────
    sl_mult     = 1.0
    tp_mult     = 1.0
    trail_mult  = base_trail
    partial_pct = base_partial
    regime_lbl  = "neutral"

    if regime_state is not None:
        sl_mult    = getattr(regime_state, "sl_atr_mult", 1.0)
        tp_mult    = getattr(regime_state, "tp_rr_mult",  1.0)
        trail_mult = base_trail * getattr(regime_state, "trail_mult", 1.0)
        regime_lbl = getattr(regime_state, "regime", "neutral")

        # Runner mode: disable partials, widen trail
        if getattr(regime_state, "allow_runner", False):
            partial_pct = min(partial_pct, 0.10)
            trail_mult  = max(trail_mult, 3.0)

        # Range mode: tighter everything
        if regime_lbl == "range":
            partial_pct = 0.50   # exit half early
            trail_mult  = max(trail_mult, 1.0)

    tp_rrr = base_tp_rrr * tp_mult
    tp_dist = tp_rrr * risk_r

    if direction == "long":
        sl_price  = max(sl_price, entry_price - risk_r * sl_mult)  # don't over-widen
        tp_price  = entry_price + tp_dist
        tp1_price = entry_price + risk_r   # 1:1 partial
    else:
        sl_price  = min(sl_price, entry_price + risk_r * sl_mult)
        tp_price  = entry_price - tp_dist
        tp1_price = entry_price - risk_r

    return ExecutionLevels(
        sl_price=round(sl_price, 5),
        tp_price=round(tp_price, 5),
        trail_atr_mult=round(trail_mult, 2),
        partial_pct=round(partial_pct, 2),
        tp1_price=round(tp1_price, 5),
        risk_r=round(risk_r, 5),
        regime_label=regime_lbl,
        notes=f"structure+{noise_mult}ATR buffer",
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_swing_low(lows, entry_price: float, atr: float) -> float:
    import numpy as np
    # Find significant swing low: lower than both neighbors
    swings = []
    for i in range(1, len(lows)-1):
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            if lows[i] < entry_price:   # must be below entry
                swings.append(lows[i])
    if swings:
        return max(swings)  # most recent significant swing low
    return float(np.min(lows))


def _find_swing_high(highs, entry_price: float, atr: float) -> float:
    import numpy as np
    swings = []
    for i in range(1, len(highs)-1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            if highs[i] > entry_price:
                swings.append(highs[i])
    if swings:
        return min(swings)
    return float(np.max(highs))


def _avoid_round_number(price: float, atr: float, direction: str) -> float:
    """Nudge SL past round numbers so it's less obvious."""
    threshold = ROUND_NUDGE_ATR * atr
    # Check if price is within threshold of a round 10 or 50 or 100
    for round_to in [100, 50, 10, 5, 1]:
        rounded = round(price / round_to) * round_to
        if abs(price - rounded) < threshold:
            if direction == "long":
                return rounded - threshold   # push further below
            else:
                return rounded + threshold   # push further above
    return price
