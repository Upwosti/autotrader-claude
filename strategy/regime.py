"""
Market regime detection — trending vs. ranging vs. volatile.
Used to filter out low-quality market environments before entry.
"""

import numpy as np
import pandas as pd
from strategy.indicators import ema, atr, adx


def detect_regime(df: pd.DataFrame,
                  adx_trend_thresh: float = 22.0,
                  adx_strong_thresh: float = 35.0) -> pd.Series:
    """
    Returns Series with values:
      'strong_bull' | 'bull' | 'bear' | 'strong_bear' | 'ranging'
    """
    adx_val, plus_di, minus_di = adx(df, 14)
    ema50  = ema(df["close"], 50)
    ema200 = ema(df["close"], 200)

    trending = adx_val >= adx_trend_thresh
    strong   = adx_val >= adx_strong_thresh
    bull_di  = plus_di > minus_di
    bear_di  = minus_di > plus_di
    above200 = df["close"] > ema200
    below200 = df["close"] < ema200

    regime = pd.Series("ranging", index=df.index, dtype=object)
    regime[trending & bull_di & above200 & strong]  = "strong_bull"
    regime[trending & bull_di & above200 & ~strong] = "bull"
    regime[trending & bear_di & below200 & strong]  = "strong_bear"
    regime[trending & bear_di & below200 & ~strong] = "bear"
    return regime


def is_trending(df: pd.DataFrame, threshold: float = 22.0) -> pd.Series:
    """True when ADX >= threshold."""
    adx_val, _, _ = adx(df, 14)
    return adx_val >= threshold


def is_expansion(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    """
    True when current ATR is not shrinking vs. recent average.
    Expansion ↔ price is not in tight consolidation.
    """
    atr14 = atr(df, 14)
    atr_avg = atr14.rolling(lookback).mean()
    return atr14 >= atr_avg * 0.75


def weekly_trend(df: pd.DataFrame) -> pd.Series:
    """
    Weekly bias from a weekly OHLCV df.
    Returns 'bull' | 'bear' | 'neutral' per bar.
    """
    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    bull  = (df["close"] > ema20) & (ema20 > ema50)
    bear  = (df["close"] < ema20) & (ema20 < ema50)
    bias  = pd.Series("neutral", index=df.index, dtype=object)
    bias[bull] = "bull"
    bias[bear] = "bear"
    return bias


def daily_pullback_zone(df: pd.DataFrame, atr_mult: float = 1.5) -> pd.Series:
    """
    True when close is within atr_mult × ATR of the 21-EMA.
    Identifies pullback zones for high-probability entries.
    """
    e21  = ema(df["close"], 21)
    atr14 = atr(df, 14)
    dist = (df["close"] - e21).abs()
    return dist <= atr_mult * atr14


def ema_stack_bull(df: pd.DataFrame) -> pd.Series:
    """EMA21 > EMA50 > EMA200 — full bullish alignment."""
    e21  = ema(df["close"], 21)
    e50  = ema(df["close"], 50)
    e200 = ema(df["close"], 200)
    return (e21 > e50) & (e50 > e200)


def ema_stack_bear(df: pd.DataFrame) -> pd.Series:
    e21  = ema(df["close"], 21)
    e50  = ema(df["close"], 50)
    e200 = ema(df["close"], 200)
    return (e21 < e50) & (e50 < e200)
