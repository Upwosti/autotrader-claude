"""
HighConfluenceTrend — multi-timeframe swing/day trade engine.

Entry logic (D1 primary, W1 bias, H4 confirmation):
  1. Weekly bias (20/50 EMA stack) defines direction
  2. Daily trend must be intact (EMA21 > EMA50 for longs)
  3. Pullback to EMA21 or key support (within 1.5×ATR)
  4. Reversal candlestick pattern on D1
  5. ADX >= min_adx (not choppy)
  6. RSI in healthy range (not overbought/oversold into trade)
  7. HTF filter: do not trade against W1 bias unless CHoCH

This is the primary strategy the evolution loop optimises.
Parameters are stored in TrendParams (evolvable dataclass).
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime

from strategy.indicators import ema, atr, adx, rsi, volume_ratio
from strategy.patterns import (
    bullish_reversal, bearish_reversal,
    bullish_engulfing, bearish_engulfing,
    bullish_hammer, bearish_shooting_star,
    three_white_soldiers, three_black_crows,
    inside_bar,
)
from strategy.regime import (
    detect_regime, is_expansion, weekly_trend,
    daily_pullback_zone, ema_stack_bull, ema_stack_bear,
)


@dataclass
class TrendParams:
    # EMA periods
    ema_fast: int   = 21
    ema_slow: int   = 50
    ema_long: int   = 200
    ema_weekly: int = 20

    # ATR
    atr_period: int  = 14
    sl_atr_mult: float = 0.5   # SL = pattern_low - sl_atr_mult × ATR
    tp_rrr: float    = 2.5     # Take-profit risk:reward ratio

    # Trend filters
    min_adx: float   = 15.0    # Minimum ADX to enter (soft filter — adds to score)
    rsi_long_max: float  = 70.0   # Max RSI for long entry (not overbought)
    rsi_long_min: float  = 25.0   # Min RSI for long entry
    rsi_short_max: float = 75.0
    rsi_short_min: float = 30.0

    # Pullback zone
    pullback_atr_mult: float = 2.0  # How close to EMA required

    # Volume filter
    min_vol_ratio: float = 0.7   # Min volume relative to 20-bar average

    # Session / timing
    use_weekly_filter: bool = True   # Require weekly bias alignment
    use_ema_stack: bool     = False  # Require full EMA alignment (off by default — too restrictive)
    use_pattern: bool       = True   # Require candlestick pattern
    use_pullback_zone: bool = False  # Require pullback to EMA zone
    use_adx_filter: bool    = True   # ADX as score contribution (not hard block)
    use_volume_filter: bool = False  # Optional volume confirmation
    use_expansion: bool     = False  # Skip inside-bar consolidation bars
    use_killzone: bool      = False  # +2 confluence bonus for Tue/Wed/Thu bars
    use_ict_filter: bool    = False  # ICT score gate for XAUUSD signals (min score 40)
    ict_min_score: int      = 40    # Minimum ICT advanced score to allow signal

    # Exit behavior — asymmetric payoff
    trail_atr_mult: float = 2.0   # ATR multiple for trailing stop (wider = more room to run)
    partial_pct_1r: float = 0.25  # Fraction of position closed at 1:1 (0=no partial, 0.5=half)

    # Min trade duration filter (skip ultra-fast trades)
    min_hold_bars: int = 0     # min bars held before forced close

    # Confidence threshold
    min_confluence: int = 2    # Minimum conditions that must be True

    # Version / metadata
    version: int = 1
    strategy_name: str = "HighConfluenceTrend"
    notes: str = "Baseline"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TrendParams":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class TradeSignal:
    date: Any          # pandas Timestamp
    direction: str     # 'long' | 'short'
    entry: float
    sl: float
    tp: float
    rrr: float
    confluence: int    # number of conditions met
    pattern: str
    session: str = "daily"
    pair: str    = "XAUUSD"
    regime: str  = "unknown"


class HighConfluenceTrend:
    """
    Generates swing trade signals using multi-timeframe confluence.
    Designed to achieve high win rate by requiring many confirming factors.
    """

    def __init__(self, params: TrendParams):
        self.params = params

    # Sentinel set — if all present, indicators are already computed
    _INDICATOR_COLS = {"ema_fast", "ema_slow", "ema_long", "atr", "rsi",
                       "adx", "vol_ratio", "bull_pattern", "bear_pattern",
                       "inside", "pullback_zone", "ema_bull", "ema_bear",
                       "expansion", "regime"}

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._INDICATOR_COLS.issubset(df.columns):
            return df  # already computed — skip expensive recalculation
        df = df.copy()
        p = self.params
        df["ema_fast"]  = ema(df["close"], p.ema_fast)
        df["ema_slow"]  = ema(df["close"], p.ema_slow)
        df["ema_long"]  = ema(df["close"], p.ema_long)
        df["atr"]       = atr(df, p.atr_period)
        df["rsi"]       = rsi(df["close"], 14)
        df["adx"], df["plus_di"], df["minus_di"] = adx(df, 14)
        df["vol_ratio"] = volume_ratio(df, 20)

        df["bull_pattern"] = bullish_reversal(df)
        df["bear_pattern"] = bearish_reversal(df)
        df["inside"]       = inside_bar(df)
        df["pullback_zone"] = daily_pullback_zone(df, p.pullback_atr_mult)
        df["ema_bull"]     = ema_stack_bull(df)
        df["ema_bear"]     = ema_stack_bear(df)
        df["expansion"]    = is_expansion(df)
        df["regime"]       = detect_regime(df, p.min_adx)
        return df

    def _align_weekly_bias(
        self,
        daily_df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame],
    ) -> pd.Series:
        """
        Pre-compute weekly bias aligned to daily dates — O(n log n), not O(n²).
        Returns a Series indexed by daily_df.index with values 'bull'|'bear'|'neutral'.
        """
        if weekly_df is None or len(weekly_df) < 20:
            return pd.Series("neutral", index=daily_df.index, dtype=object)
        w_bias = weekly_trend(weekly_df)          # Series indexed by weekly dates
        # Reindex to daily frequency, forward-filling the weekly value
        aligned = w_bias.reindex(
            w_bias.index.union(daily_df.index)
        ).ffill().reindex(daily_df.index).fillna("neutral")
        return aligned

    def generate_signals(
        self,
        daily_df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame] = None,
        pair: str = "XAUUSD",
    ) -> List[TradeSignal]:
        """
        Vectorized signal scanner — O(n) numpy operations replace the per-bar loop.
        Only iterates over confirmed signal candidates (typically < 5% of bars).
        """
        p = self.params
        df = self._add_indicators(daily_df)
        n = len(df)
        if n < 2:
            return []

        weekly_bias_series = self._align_weekly_bias(daily_df, weekly_df)
        min_bars = max(p.ema_long, 210)

        # ── Vectorized confluence scores (pandas/numpy, no Python loop) ──────
        idx = df.index[min_bars:n - 1]
        ds  = df.loc[idx]           # scan window
        wb  = weekly_bias_series.loc[idx] if p.use_weekly_filter \
              else pd.Series("neutral", index=idx, dtype=object)

        long_sc  = pd.Series(0, index=idx, dtype=np.int8)
        short_sc = pd.Series(0, index=idx, dtype=np.int8)

        if p.use_adx_filter:
            adx_ok = (ds["adx"] >= p.min_adx).astype(np.int8)
            long_sc  += adx_ok
            short_sc += adx_ok

        if p.use_ema_stack:
            long_sc  += ds["ema_bull"].astype(np.int8)
            short_sc += ds["ema_bear"].astype(np.int8)

        if p.use_weekly_filter:
            long_sc  += (wb == "bull").astype(np.int8)
            short_sc += (wb == "bear").astype(np.int8)

        long_sc  += (ds["close"] > ds["ema_slow"]).astype(np.int8)
        short_sc += (ds["close"] < ds["ema_slow"]).astype(np.int8)

        rsi_l_ok = ((ds["rsi"] >= p.rsi_long_min)  & (ds["rsi"] <= p.rsi_long_max)).astype(np.int8)
        rsi_s_ok = ((ds["rsi"] >= p.rsi_short_min) & (ds["rsi"] <= p.rsi_short_max)).astype(np.int8)
        long_sc  += rsi_l_ok
        short_sc += rsi_s_ok

        if p.use_pullback_zone:
            pz = ds["pullback_zone"].astype(np.int8)
            long_sc  += pz
            short_sc += pz

        if p.use_pattern:
            long_sc  += ds["bull_pattern"].astype(np.int8) * 2
            short_sc += ds["bear_pattern"].astype(np.int8) * 2

        if p.use_volume_filter:
            vol_ok = (ds["vol_ratio"] >= p.min_vol_ratio).astype(np.int8)
            long_sc  += vol_ok
            short_sc += vol_ok

        if p.use_expansion:
            inside = ds["inside"].astype(bool)
            long_sc  = long_sc.copy()
            short_sc = short_sc.copy()
            long_sc[inside]  = 0
            short_sc[inside] = 0

        if p.use_killzone:
            # +2 confluence for Tuesday/Wednesday/Thursday bars (best precision days)
            # Monday = 0, Friday = 4 — avoided (gap risk, position squaring)
            try:
                dow = pd.DatetimeIndex(idx).dayofweek  # 0=Mon … 4=Fri
            except Exception:
                dow = pd.Series(2, index=idx)  # default to Wednesday (safe)
            in_kz = pd.Series(
                ((dow >= 1) & (dow <= 3)).astype(np.int8),
                index=idx, dtype=np.int8
            )
            long_sc  = long_sc.copy() + in_kz * 2
            short_sc = short_sc.copy() + in_kz * 2

        # ── Regime and weekly block masks ────────────────────────────────────
        reg = ds["regime"]
        regime_bull  = reg.isin(["bull", "strong_bull", "ranging"])
        regime_bear  = reg.isin(["bear", "strong_bear", "ranging"])
        w_blk_long   = (wb == "bear") if p.use_weekly_filter \
                       else pd.Series(False, index=idx)
        w_blk_short  = (wb == "bull") if p.use_weekly_filter \
                       else pd.Series(False, index=idx)

        long_mask  = (long_sc  >= p.min_confluence) & regime_bull  & ~w_blk_long
        # short only on bars where no long fires (mirrors original elif)
        short_mask = (short_sc >= p.min_confluence) & regime_bear  & ~w_blk_short & ~long_mask

        # ── Extract candidate positions in the full df ────────────────────────
        lows_arr   = df["low"].values
        highs_arr  = df["high"].values
        opens_arr  = df["open"].values
        atrs_arr   = df["atr"].values

        # Build fast pattern series once (only if patterns needed at signal level)
        bull_eng   = bullish_engulfing(df)
        bull_ham   = bullish_hammer(df)
        bear_eng   = bearish_engulfing(df)
        bear_star  = bearish_shooting_star(df)

        scan_positions = np.arange(min_bars, n - 1)
        long_positions  = scan_positions[long_mask.values]
        short_positions = scan_positions[short_mask.values]

        signals: List[TradeSignal] = []

        for i in long_positions:
            entry_price = float(opens_arr[i + 1])
            sl = min(float(lows_arr[i]), float(lows_arr[i - 1])) \
                 - float(atrs_arr[i]) * p.sl_atr_mult
            risk = entry_price - sl
            if risk <= 0 or risk > float(atrs_arr[i]) * 10:
                continue
            tp = entry_price + risk * p.tp_rrr
            bp = bool(ds["bull_pattern"].iat[i - min_bars])
            pattern_name = (
                "engulfing"   if bp and bool(bull_eng.iat[i])
                else "hammer" if bool(bull_ham.iat[i])
                else "reversal"
            )
            signals.append(TradeSignal(
                date=df.index[i], direction="long",
                entry=round(entry_price, 5),
                sl=round(sl, 5), tp=round(tp, 5),
                rrr=round(p.tp_rrr, 2),
                confluence=int(long_sc.iat[i - min_bars]),
                pattern=pattern_name, pair=pair,
                regime=str(reg.iat[i - min_bars]),
            ))

        for i in short_positions:
            entry_price = float(opens_arr[i + 1])
            sl = max(float(highs_arr[i]), float(highs_arr[i - 1])) \
                 + float(atrs_arr[i]) * p.sl_atr_mult
            risk = sl - entry_price
            if risk <= 0 or risk > float(atrs_arr[i]) * 10:
                continue
            tp = entry_price - risk * p.tp_rrr
            pattern_name = (
                "engulfing"      if bool(bear_eng.iat[i])
                else "shooting_star" if bool(bear_star.iat[i])
                else "reversal"
            )
            signals.append(TradeSignal(
                date=df.index[i], direction="short",
                entry=round(entry_price, 5),
                sl=round(sl, 5), tp=round(tp, 5),
                rrr=round(p.tp_rrr, 2),
                confluence=int(short_sc.iat[i - min_bars]),
                pattern=pattern_name, pair=pair,
                regime=str(reg.iat[i - min_bars]),
            ))

        # Return sorted by date (longs and shorts may interleave)
        signals.sort(key=lambda s: s.date)
        return signals

    def scan_live(self, daily_df: pd.DataFrame,
                  weekly_df: Optional[pd.DataFrame] = None,
                  pair: str = "XAUUSD") -> Optional[Dict]:
        """Live scanner — returns latest signal dict or None."""
        signals = self.generate_signals(daily_df, weekly_df, pair)
        if not signals:
            return None
        last = signals[-1]
        # Only return if signal is from last 2 bars (still actionable)
        bar_age = len(daily_df) - daily_df.index.get_loc(last.date) \
                  if last.date in daily_df.index else 999
        if bar_age > 2:
            return None
        return {
            "symbol": pair, "direction": "buy" if last.direction == "long" else "sell",
            "entry": last.entry, "sl": last.sl, "tp": last.tp,
            "rrr": last.rrr, "confluence": last.confluence,
            "pattern": last.pattern, "regime": last.regime,
        }
