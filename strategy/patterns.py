"""
Candlestick pattern detection — vectorised over full DataFrames.
All detect_* functions return a boolean Series aligned to df.index.
"""

import numpy as np
import pandas as pd


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()

def _upper_wick(df: pd.DataFrame) -> pd.Series:
    return df["high"] - df["close"].combine(df["open"], max)

def _lower_wick(df: pd.DataFrame) -> pd.Series:
    return df["close"].combine(df["open"], min) - df["low"]

def _candle_range(df: pd.DataFrame) -> pd.Series:
    return (df["high"] - df["low"]).replace(0, np.nan)

def _is_bullish(df: pd.DataFrame) -> pd.Series:
    return df["close"] > df["open"]

def _is_bearish(df: pd.DataFrame) -> pd.Series:
    return df["close"] < df["open"]


# ─── Single-bar patterns ──────────────────────────────────────────────────────

def bullish_hammer(df: pd.DataFrame,
                   body_pct: float = 0.35,
                   wick_mult: float = 2.0) -> pd.Series:
    """Lower wick >= wick_mult × body, small upper wick, body <= body_pct of range."""
    body = _body(df)
    cr = _candle_range(df)
    lw = _lower_wick(df)
    uw = _upper_wick(df)
    return (
        (body / cr <= body_pct) &
        (lw >= wick_mult * body.replace(0, np.nan)) &
        (uw <= body * 1.1)
    ).fillna(False)


def bearish_shooting_star(df: pd.DataFrame,
                          body_pct: float = 0.35,
                          wick_mult: float = 2.0) -> pd.Series:
    body = _body(df)
    cr = _candle_range(df)
    uw = _upper_wick(df)
    lw = _lower_wick(df)
    return (
        (body / cr <= body_pct) &
        (uw >= wick_mult * body.replace(0, np.nan)) &
        (lw <= body * 1.1)
    ).fillna(False)


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Current candle bullish and body fully engulfs previous bearish body."""
    curr_bull = _is_bullish(df)
    prev_bear = _is_bearish(df.shift(1))
    engulf = (df["open"] <= df["close"].shift(1)) & (df["close"] >= df["open"].shift(1))
    return (curr_bull & prev_bear & engulf).fillna(False)


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    curr_bear = _is_bearish(df)
    prev_bull = _is_bullish(df.shift(1))
    engulf = (df["open"] >= df["close"].shift(1)) & (df["close"] <= df["open"].shift(1))
    return (curr_bear & prev_bull & engulf).fillna(False)


def inside_bar(df: pd.DataFrame) -> pd.Series:
    """Current bar fully inside previous bar (consolidation)."""
    return (
        (df["high"] < df["high"].shift(1)) &
        (df["low"] > df["low"].shift(1))
    ).fillna(False)


def pin_bar_bullish(df: pd.DataFrame, nose_pct: float = 0.33) -> pd.Series:
    """Long lower wick (pin), small nose. Bullish reversal."""
    lw = _lower_wick(df)
    cr = _candle_range(df)
    nose = df["high"] - (df["low"] + cr * (1 - nose_pct))
    return ((lw / cr.replace(0, np.nan)) >= 0.6).fillna(False)


def pin_bar_bearish(df: pd.DataFrame, nose_pct: float = 0.33) -> pd.Series:
    uw = _upper_wick(df)
    cr = _candle_range(df)
    return ((uw / cr.replace(0, np.nan)) >= 0.6).fillna(False)


# ─── Multi-bar patterns ───────────────────────────────────────────────────────

def three_white_soldiers(df: pd.DataFrame) -> pd.Series:
    """Three consecutive bullish candles, each closing higher."""
    b1 = _is_bullish(df)
    b2 = _is_bullish(df.shift(1))
    b3 = _is_bullish(df.shift(2))
    h1 = df["close"] > df["close"].shift(1)
    h2 = df["close"].shift(1) > df["close"].shift(2)
    return (b1 & b2 & b3 & h1 & h2).fillna(False)


def three_black_crows(df: pd.DataFrame) -> pd.Series:
    b1 = _is_bearish(df)
    b2 = _is_bearish(df.shift(1))
    b3 = _is_bearish(df.shift(2))
    l1 = df["close"] < df["close"].shift(1)
    l2 = df["close"].shift(1) < df["close"].shift(2)
    return (b1 & b2 & b3 & l1 & l2).fillna(False)


# ─── Composite signals ────────────────────────────────────────────────────────

def bullish_reversal(df: pd.DataFrame) -> pd.Series:
    """Any bullish reversal pattern present."""
    return (
        bullish_hammer(df) |
        bullish_engulfing(df) |
        pin_bar_bullish(df)
    )


def bearish_reversal(df: pd.DataFrame) -> pd.Series:
    """Any bearish reversal pattern present."""
    return (
        bearish_shooting_star(df) |
        bearish_engulfing(df) |
        pin_bar_bearish(df)
    )


def continuation_bull(df: pd.DataFrame) -> pd.Series:
    """Bullish continuation: strong close, not an inside bar."""
    strong_close = df["close"] > (df["high"] + df["low"]) / 2
    not_inside = ~inside_bar(df)
    bull = _is_bullish(df)
    return (bull & strong_close & not_inside).fillna(False)


def continuation_bear(df: pd.DataFrame) -> pd.Series:
    strong_close = df["close"] < (df["high"] + df["low"]) / 2
    not_inside = ~inside_bar(df)
    bear = _is_bearish(df)
    return (bear & strong_close & not_inside).fillna(False)
