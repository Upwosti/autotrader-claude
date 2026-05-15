"""
Market Regime Engine

Classifies current market regime and adjusts strategy accordingly.
Regimes: trend | range | expansion | compression | reversal |
         momentum_continuation | exhaustion | news_chaos

Controls: entries, exits, SL, TP, trailing, aggressiveness, trade duration
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import numpy as np
    import pandas as pd
    _HAS_NP = True
except ImportError:
    _HAS_NP = False


@dataclass
class RegimeState:
    regime: str = "unknown"          # primary regime label
    sub_regime: str = ""             # secondary signal

    # Strategy adjustments (multipliers on base params)
    sl_atr_mult: float = 1.0         # 1.0 = use base; 1.3 = wider SL
    tp_rr_mult: float  = 1.0         # 1.0 = use base; 2.0 = target higher RR
    trail_mult: float  = 1.0         # trailing ATR multiplier adjustment
    risk_mult: float   = 1.0         # scale risk (0.5 = half size)
    allow_entry: bool  = True        # False = no new entries
    allow_runner: bool = False       # True = let winner run to 5R+
    use_partials: bool = True        # True = take partials at 1R, 2R

    adx: float = 0.0
    atr_ratio: float = 1.0          # current ATR / 20-bar avg ATR
    confidence: float = 0.5


def classify_regime(df, pair: str = "") -> RegimeState:
    """
    Full regime classification from OHLCV DataFrame.
    Requires at least 50 bars.
    """
    if not _HAS_NP or df is None or len(df) < 50:
        return RegimeState(regime="unknown")

    state = RegimeState()
    try:
        state = _compute_regime(df)
    except Exception:
        pass
    return state


def _compute_regime(df) -> RegimeState:
    import pandas as pd
    import numpy as np

    close = df["close"].values
    high  = df["high"].values
    low   = df["low"].values
    n     = len(close)

    # ── ATR (14) ─────────────────────────────────────────────────────────────
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
    )
    atr14   = _ema(tr, 14)[-1]
    atr_avg = float(np.mean(_ema(tr, 14)[-20:]))
    atr_ratio = atr14 / atr_avg if atr_avg > 0 else 1.0

    # ── ADX (14) ──────────────────────────────────────────────────────────────
    adx = _compute_adx(high, low, close, 14)

    # ── EMA stack ────────────────────────────────────────────────────────────
    ema21  = _ema(close, 21)[-1]
    ema50  = _ema(close, 50)[-1]
    ema200 = _ema(close, min(200, n-1))[-1] if n >= 201 else _ema(close, n//2)[-1]
    c = close[-1]

    bull_stack = c > ema21 > ema50 > ema200
    bear_stack = c < ema21 < ema50 < ema200

    # ── Swing structure ───────────────────────────────────────────────────────
    last20_h = high[-20:]
    last20_l = low[-20:]
    # HH/HL count (bullish structure)
    hh = sum(1 for i in range(1, len(last20_h)) if last20_h[i] > last20_h[i-1])
    hl = sum(1 for i in range(1, len(last20_l)) if last20_l[i] > last20_l[i-1])

    # ── BB squeeze (compression) ──────────────────────────────────────────────
    rolling_std = float(np.std(close[-20:]))
    bb_width = rolling_std / c if c > 0 else 0
    squeeze = bb_width < 0.005   # very tight BB

    # ── Classify ──────────────────────────────────────────────────────────────
    state = RegimeState(adx=round(adx, 1), atr_ratio=round(atr_ratio, 2))

    if adx > 30 and atr_ratio > 1.3:
        # Strong trend + expanding volatility = momentum continuation
        state.regime     = "momentum_continuation"
        state.allow_runner = True
        state.use_partials = False
        state.sl_atr_mult  = 1.2
        state.tp_rr_mult   = 2.0    # target 5R+
        state.trail_mult   = 1.5
        state.risk_mult    = 1.0
        state.confidence   = 0.85

    elif adx > 25 and (bull_stack or bear_stack):
        # Clean trend
        state.regime     = "trend"
        state.allow_runner = True
        state.use_partials = True
        state.sl_atr_mult  = 1.1
        state.tp_rr_mult   = 1.5
        state.trail_mult   = 1.2
        state.risk_mult    = 1.0
        state.confidence   = 0.75

    elif adx > 20 and atr_ratio > 1.5:
        # Expansion — could be breakout or news
        if atr_ratio > 2.0:
            state.regime    = "expansion"
            state.allow_entry = False   # wait for candle close
            state.risk_mult = 0.5
            state.confidence = 0.4
        else:
            state.regime     = "expansion"
            state.sl_atr_mult  = 1.3
            state.tp_rr_mult   = 1.8
            state.use_partials = True
            state.confidence   = 0.65

    elif adx < 15 and squeeze:
        # Compression = breakout incoming
        state.regime     = "compression"
        state.allow_entry  = False  # wait for breakout confirmation
        state.risk_mult    = 0.75
        state.confidence   = 0.5

    elif adx < 20 and not bull_stack and not bear_stack:
        # Range
        state.regime     = "range"
        state.sl_atr_mult  = 0.8   # tighter SL (range mean-reversion)
        state.tp_rr_mult   = 0.8   # tighter TP
        state.use_partials = True
        state.allow_runner = False
        state.risk_mult    = 0.75
        state.confidence   = 0.60

    elif atr_ratio < 0.7:
        # Volatility collapse / exhaustion
        state.regime    = "exhaustion"
        state.allow_entry = False
        state.risk_mult = 0.5
        state.confidence = 0.35

    elif hh >= 3 and hl >= 3 and not bear_stack:
        # Structure reversal from bear to bull
        state.regime   = "reversal"
        state.sl_atr_mult = 1.1
        state.tp_rr_mult  = 1.3
        state.risk_mult   = 0.75
        state.confidence  = 0.60

    else:
        state.regime    = "neutral"
        state.risk_mult = 0.8
        state.confidence = 0.45

    return state


# ── Technical helpers ─────────────────────────────────────────────────────────

def _ema(series, span: int):
    import numpy as np
    alpha = 2.0 / (span + 1)
    out = np.empty_like(series, dtype=float)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = alpha * series[i] + (1 - alpha) * out[i-1]
    return out


def _compute_adx(high, low, close, period: int = 14) -> float:
    import numpy as np
    n = len(close)
    if n < period + 2:
        return 20.0

    prev_high  = np.roll(high, 1)
    prev_low   = np.roll(low, 1)
    prev_close = np.roll(close, 1)
    prev_high[0] = high[0]; prev_low[0] = low[0]; prev_close[0] = close[0]

    tr  = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    pdm = np.where((high - prev_high) > (prev_low - low), np.maximum(high - prev_high, 0), 0)
    ndm = np.where((prev_low - low) > (high - prev_high), np.maximum(prev_low - low, 0), 0)

    atr_e = _ema(tr, period)
    pdm_e = _ema(pdm, period)
    ndm_e = _ema(ndm, period)

    pdi = 100 * pdm_e / np.where(atr_e > 0, atr_e, 1)
    ndi = 100 * ndm_e / np.where(atr_e > 0, atr_e, 1)
    dx  = 100 * np.abs(pdi - ndi) / np.where((pdi + ndi) > 0, pdi + ndi, 1)
    adx = _ema(dx, period)

    return float(adx[-1])
