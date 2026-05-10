"""
Momentum Capture Engine — detects strong momentum and controls exit strategy.

When momentum score exceeds threshold:
  - Suppress partial closes (let winners run)
  - Widen trailing stop
  - Allow 5R–15R runners to develop
  - Override standard exits with momentum-based logic

Momentum score: 0–10 composite from velocity, ATR expansion,
multi-timeframe alignment, and trend persistence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Dict, Any


# Thresholds
MOMENTUM_ACTIVE_THRESHOLD = 6.0   # score ≥ 6 → runner mode
MOMENTUM_STRONG_THRESHOLD = 8.0   # score ≥ 8 → no partial closes until 3R
RUNNER_TRAIL_MULTIPLIER   = 2.0   # widen trail by 2× ATR in runner mode
ATR_EXPANSION_LOOKBACK    = 5     # bars to measure ATR expansion over


@dataclass
class MomentumState:
    score: float            = 0.0
    regime: str             = "neutral"   # "strong_trend" | "trend" | "range" | "neutral"
    runner_mode: bool       = False
    velocity_score: float   = 0.0
    atr_expansion_score: float = 0.0
    mtf_alignment_score: float = 0.0
    persistence_score: float   = 0.0
    recommended_trail_atr: float = 1.0
    allow_early_partial: bool  = True
    min_partial_r: float       = 2.0      # don't partial-close before this R


def score_momentum(
    df: pd.DataFrame,
    direction: str,
    weekly_df: Optional[pd.DataFrame] = None,
) -> MomentumState:
    """
    Compute momentum score for the current bar.

    Args:
        df: Daily OHLCV DataFrame (last bar = current).
        direction: 'long' or 'short'.
        weekly_df: Optional weekly OHLCV for HTF alignment check.

    Returns:
        MomentumState with composite score and regime.
    """
    if len(df) < 20:
        return MomentumState()

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]

    # ── 1. Price velocity (0–2.5 pts) ────────────────────────────────────────
    # How fast is price moving? Measure via 5-bar return vs. normal volatility.
    returns_5 = close.pct_change(5).iloc[-1]
    atr14 = _atr_series(df, 14)
    normal_5bar_move = atr14.iloc[-1] * 5 / close.iloc[-1]  # 5 ATR in % terms
    if normal_5bar_move > 0:
        velocity_ratio = abs(returns_5) / normal_5bar_move
    else:
        velocity_ratio = 0.0
    velocity_score = min(2.5, velocity_ratio * 2.5)
    # Direction check: only positive if moving in our direction
    if direction == "long" and returns_5 < 0:
        velocity_score *= 0.3
    elif direction == "short" and returns_5 > 0:
        velocity_score *= 0.3

    # ── 2. ATR expansion (0–2.5 pts) ─────────────────────────────────────────
    # Rising ATR = expanding volatility = momentum is real.
    recent_atr  = atr14.iloc[-ATR_EXPANSION_LOOKBACK:].mean()
    prior_atr   = atr14.iloc[-ATR_EXPANSION_LOOKBACK*2: -ATR_EXPANSION_LOOKBACK].mean()
    if prior_atr > 0:
        expansion_ratio = recent_atr / prior_atr
    else:
        expansion_ratio = 1.0
    atr_expansion_score = min(2.5, max(0.0, (expansion_ratio - 1.0) * 5.0))

    # ── 3. Multi-timeframe alignment (0–2.5 pts) ──────────────────────────────
    mtf_score = 0.0
    ema21  = close.ewm(span=21, adjust=False).mean()
    ema50  = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    cur_close = close.iloc[-1]

    if direction == "long":
        if cur_close > ema21.iloc[-1]:           mtf_score += 0.8
        if ema21.iloc[-1] > ema50.iloc[-1]:      mtf_score += 0.8
        if ema50.iloc[-1] > ema200.iloc[-1]:     mtf_score += 0.9
    else:
        if cur_close < ema21.iloc[-1]:           mtf_score += 0.8
        if ema21.iloc[-1] < ema50.iloc[-1]:      mtf_score += 0.8
        if ema50.iloc[-1] < ema200.iloc[-1]:     mtf_score += 0.9

    # Weekly bias bonus
    if weekly_df is not None and len(weekly_df) >= 50:
        w_close = weekly_df["close"]
        w_ema20 = w_close.ewm(span=20, adjust=False).mean()
        w_ema50 = w_close.ewm(span=50, adjust=False).mean()
        w_bull  = (w_ema20.iloc[-1] > w_ema50.iloc[-1])
        if (direction == "long" and w_bull) or (direction == "short" and not w_bull):
            mtf_score = min(2.5, mtf_score * 1.25)

    mtf_score = min(2.5, mtf_score)

    # ── 4. Trend persistence (0–2.5 pts) ─────────────────────────────────────
    # HH/HL sequence for longs, LL/LH for shorts (last 10 bars).
    window = min(10, len(df) - 1)
    highs = high.iloc[-window:].values
    lows  = low.iloc[-window:].values
    persistence_score = 0.0

    if direction == "long":
        hh_count = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
        hl_count = sum(1 for i in range(1, len(lows))  if lows[i]  > lows[i-1])
        persistence_score = min(2.5, (hh_count + hl_count) / (2 * (window - 1)) * 2.5)
    else:
        ll_count = sum(1 for i in range(1, len(lows))  if lows[i]  < lows[i-1])
        lh_count = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i-1])
        persistence_score = min(2.5, (ll_count + lh_count) / (2 * (window - 1)) * 2.5)

    # ── Composite score ───────────────────────────────────────────────────────
    total = velocity_score + atr_expansion_score + mtf_score + persistence_score

    # Determine regime
    if total >= 8.0:
        regime = "strong_trend"
    elif total >= 6.0:
        regime = "trend"
    elif total >= 3.0:
        regime = "weak_trend"
    else:
        regime = "range"

    runner_mode = total >= MOMENTUM_ACTIVE_THRESHOLD

    # Adaptive trailing: wider trail in strong momentum
    if total >= MOMENTUM_STRONG_THRESHOLD:
        trail_atr = 2.0 * RUNNER_TRAIL_MULTIPLIER
    elif total >= MOMENTUM_ACTIVE_THRESHOLD:
        trail_atr = 1.5 * RUNNER_TRAIL_MULTIPLIER
    else:
        trail_atr = 1.0

    # Partial close rules
    allow_early_partial = total < MOMENTUM_ACTIVE_THRESHOLD
    min_partial_r = 3.0 if total >= MOMENTUM_STRONG_THRESHOLD else 2.0

    return MomentumState(
        score                = round(total, 2),
        regime               = regime,
        runner_mode          = runner_mode,
        velocity_score       = round(velocity_score, 2),
        atr_expansion_score  = round(atr_expansion_score, 2),
        mtf_alignment_score  = round(mtf_score, 2),
        persistence_score    = round(persistence_score, 2),
        recommended_trail_atr= trail_atr,
        allow_early_partial  = allow_early_partial,
        min_partial_r        = min_partial_r,
    )


def runner_survival_sl(unrealized_r: float, entry: float, sl: float) -> float:
    """
    Runner mode SL management: ratchet guarantee as trade moves in our favour.

    Returns new SL price (or original if no upgrade).
    """
    risk = abs(entry - sl)
    direction = 1 if sl < entry else -1   # 1 = long, -1 = short

    if unrealized_r >= 10.0:
        # Move SL to 7R guaranteed
        guaranteed_r = 7.0
    elif unrealized_r >= 5.0:
        # Move SL to 3R guaranteed
        guaranteed_r = 3.0
    elif unrealized_r >= 3.0:
        # Move SL to 2R guaranteed
        guaranteed_r = 2.0
    else:
        return sl  # No upgrade yet

    new_sl = entry + direction * guaranteed_r * risk
    # Only upgrade (never move SL against trade)
    if direction == 1:   # long — SL must move up
        return max(sl, new_sl)
    else:                # short — SL must move down
        return min(sl, new_sl)


def apply_momentum_to_exit(
    unrealized_r: float,
    momentum: MomentumState,
    current_sl: float,
    entry: float,
    current_price: float,
    atr_value: float,
) -> Dict[str, Any]:
    """
    Given current trade state + momentum, decide exit action.

    Returns dict with:
      action:        "hold" | "partial_close" | "close" | "upgrade_sl"
      new_sl:        updated SL price (if upgrading)
      close_pct:     fraction to close (0.0–1.0)
      reason:        explanation string
    """
    direction = 1 if current_price > entry else -1
    risk = abs(entry - current_sl)

    # Runner SL upgrade check
    new_sl = runner_survival_sl(unrealized_r, entry, current_sl)
    sl_upgraded = (new_sl != current_sl)

    # Determine partial close
    close_pct = 0.0
    action = "hold"
    reason = "holding"

    if momentum.runner_mode:
        # In runner mode: no partials until min_partial_r
        if unrealized_r >= momentum.min_partial_r:
            close_pct = 0.25
            action = "partial_close"
            reason = f"runner_mode partial at {unrealized_r:.1f}R"
        else:
            action = "hold"
            reason = f"runner_mode — holding until {momentum.min_partial_r}R"
    else:
        # Standard mode: partial at 2R
        if unrealized_r >= 2.0:
            close_pct = 0.25
            action = "partial_close"
            reason = "standard partial at 2R"
        elif unrealized_r >= 1.0:
            close_pct = 0.0
            action = "hold"
            reason = "holding to 2R"

    # Momentum exhaustion check: if score dropped and trade is profitable, tighten
    if not momentum.runner_mode and unrealized_r > 0 and momentum.score < 3.0:
        if unrealized_r >= 1.5:
            action = "close"
            reason = "momentum_exhausted_in_profit"
            close_pct = 1.0

    if sl_upgraded:
        action = "upgrade_sl"
        reason = f"runner SL upgraded to {new_sl:.5f} at {unrealized_r:.1f}R"

    return {
        "action":     action,
        "new_sl":     new_sl,
        "close_pct":  close_pct,
        "reason":     reason,
        "momentum_score": momentum.score,
        "regime":     momentum.regime,
    }


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range → ATR series."""
    high = df["high"]
    low  = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()
