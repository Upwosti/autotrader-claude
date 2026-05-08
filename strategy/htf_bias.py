"""
HTF Bias — determines H4/D1 directional bias using structure and liquidity.
"""

import pandas as pd
from loguru import logger


def get_htf_bias(df: pd.DataFrame, lookback: int = 20) -> str:
    """
    Returns 'bullish', 'bearish', or 'neutral' based on H4 structure.
    Uses swing highs/lows and recent price action.
    """
    if df is None or len(df) < lookback + 2:
        return "neutral"

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    recent = closes[-lookback:]
    swing_highs = [highs[i] for i in range(1, len(df) - 1)
                   if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]]
    swing_lows  = [lows[i] for i in range(1, len(df) - 1)
                   if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]]

    if not swing_highs or not swing_lows:
        return "neutral"

    last_sh = swing_highs[-1] if swing_highs else None
    last_sl = swing_lows[-1]  if swing_lows  else None
    prev_sh = swing_highs[-2] if len(swing_highs) >= 2 else None
    prev_sl = swing_lows[-2]  if len(swing_lows) >= 2  else None

    bullish = (prev_sh and last_sh and last_sh > prev_sh and
               prev_sl and last_sl and last_sl > prev_sl)
    bearish = (prev_sh and last_sh and last_sh < prev_sh and
               prev_sl and last_sl and last_sl < prev_sl)

    # Also check 50-bar simple trend
    if len(recent) >= 10:
        sma_fast = recent[-5:].mean()
        sma_slow = recent[:5].mean()
        if sma_fast > sma_slow:
            bullish = True
        elif sma_fast < sma_slow:
            bearish = True

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "neutral"


def get_dxy_bias(dxy_df: pd.DataFrame) -> str:
    """DXY trend — inverse relationship with XAUUSD/GBPUSD/EURUSD."""
    if dxy_df is None or len(dxy_df) < 10:
        return "neutral"
    closes = dxy_df["close"].values
    fast = closes[-5:].mean()
    slow = closes[-20:].mean() if len(closes) >= 20 else closes.mean()
    if fast > slow * 1.001:
        return "bullish"
    if fast < slow * 0.999:
        return "bearish"
    return "neutral"
