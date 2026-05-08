"""
Technical indicators — vectorised, pandas-native.
All functions accept a DataFrame with OHLCV columns (lowercase).
"""

import numpy as np
import pandas as pd
from typing import Tuple


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (ADX, +DI, -DI)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr14 = tr.ewm(span=period, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr14
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr14

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(span=period, adjust=False).mean()
    return adx_val.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (MACD line, signal line, histogram)."""
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume relative to rolling average."""
    avg = df["volume"].rolling(period).mean().replace(0, np.nan)
    return df["volume"] / avg


def stochastic(df: pd.DataFrame, k_period: int = 14,
               d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Returns (%K, %D)."""
    lowest_low = df["low"].rolling(k_period).min()
    highest_high = df["high"].rolling(k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    k = 100 * (df["close"] - lowest_low) / denom
    d = k.rolling(d_period).mean()
    return k.fillna(50), d.fillna(50)


def bb_bands(series: pd.Series, period: int = 20,
             std_mult: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper band, middle band, lower band)."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + std_mult * std, mid, mid - std_mult * std


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators to df in-place and return it."""
    df = df.copy()
    df["ema8"]   = ema(df["close"], 8)
    df["ema21"]  = ema(df["close"], 21)
    df["ema50"]  = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["atr14"]  = atr(df, 14)
    df["rsi14"]  = rsi(df["close"], 14)
    df["adx14"], df["plus_di"], df["minus_di"] = adx(df, 14)
    df["vol_ratio"] = volume_ratio(df, 20)
    return df
