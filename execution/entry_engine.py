"""
Entry Engine — executes all ICT entry plans simultaneously.
Every bar is evaluated against all 4 plans; any matching plan fires.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Tuple
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'autotrader_claude'))
from config import StrategyParams


@dataclass
class EntrySetup:
    plan: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rrr: float
    score: float
    reason: str
    timestamp: datetime
    valid: bool


@dataclass
class EntryResult:
    setups: List[EntrySetup]
    best: Optional[EntrySetup]
    all_valid: List[EntrySetup]
    market_condition: str


class EntryEngine:
    """
    Evaluates all four ICT entry plans on every bar.
    Multiple plans can fire simultaneously; the caller decides which to execute.
    """

    MIN_BARS = 30

    def __init__(self, params: StrategyParams, pair: str = "XAUUSD"):
        self.params = params
        self.pair = pair
        self.pip_size = self._pip_size(pair)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _pip_size(self, pair: str) -> float:
        sizes = {
            "XAUUSD": 0.01,
            "BTCUSD": 1.0,
            "GBPUSD": 0.0001,
            "EURUSD": 0.0001,
        }
        return sizes.get(pair, 0.0001)

    def _atr(self, df: pd.DataFrame, period: int = 20) -> float:
        """Compute ATR using Wilder's method and return the last value."""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        n = len(df)
        if n < 2:
            return float(high[-1] - low[-1]) if n == 1 else 0.0

        tr = np.empty(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        if n < period:
            return float(np.mean(tr))

        # Wilder smoothing
        atr = np.mean(tr[:period])
        alpha = 1.0 / period
        for i in range(period, n):
            atr = atr * (1.0 - alpha) + tr[i] * alpha

        return float(atr)

    def _swing_high(self, df: pd.DataFrame, lookback: int = 20) -> float:
        """Return the highest high over the last `lookback` bars."""
        window = df["high"].values[-lookback:]
        return float(np.max(window))

    def _swing_low(self, df: pd.DataFrame, lookback: int = 20) -> float:
        """Return the lowest low over the last `lookback` bars."""
        window = df["low"].values[-lookback:]
        return float(np.min(window))

    def _htf_bias(self, df: pd.DataFrame) -> str:
        """
        Determine directional bias from the slope of the last 20 closes.
        Returns 'bullish', 'bearish', or 'neutral'.
        """
        closes = df["close"].values
        window = closes[-20:] if len(closes) >= 20 else closes
        if len(window) < 2:
            return "neutral"

        x = np.arange(len(window), dtype=np.float64)
        slope = float(np.polyfit(x, window.astype(np.float64), 1)[0])
        atr = self._atr(df, period=min(20, len(df)))

        # Normalise slope by ATR to avoid currency-scale sensitivity
        if atr == 0.0:
            return "neutral"

        normalised = slope / atr
        if normalised > 0.01:
            return "bullish"
        if normalised < -0.01:
            return "bearish"
        return "neutral"

    def _has_sweep(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Detect whether the last 5 bars swept a recent swing high or low.

        A bullish sweep means price dipped below the prior swing low then
        closed above it (i.e. a wick sweep of sell-side liquidity).
        A bearish sweep is the mirror.

        Returns (found, direction) where direction is 'bullish', 'bearish',
        or 'none'.
        """
        if len(df) < 10:
            return False, "none"

        reference = df.iloc[:-5]
        recent = df.iloc[-5:]

        ref_high = float(reference["high"].max())
        ref_low = float(reference["low"].min())

        swept_low = False
        swept_high = False

        for _, bar in recent.iterrows():
            # Wick below swing low but close above it → bullish sweep
            if float(bar["low"]) < ref_low and float(bar["close"]) > ref_low:
                swept_low = True
            # Wick above swing high but close below it → bearish sweep
            if float(bar["high"]) > ref_high and float(bar["close"]) < ref_high:
                swept_high = True

        if swept_low and swept_high:
            # Double sweep — resolve by last occurrence
            last_low_idx = -1
            last_high_idx = -1
            for i, (_, bar) in enumerate(recent.iterrows()):
                if float(bar["low"]) < ref_low and float(bar["close"]) > ref_low:
                    last_low_idx = i
                if float(bar["high"]) > ref_high and float(bar["close"]) < ref_high:
                    last_high_idx = i
            if last_low_idx >= last_high_idx:
                return True, "bullish"
            return True, "bearish"

        if swept_low:
            return True, "bullish"
        if swept_high:
            return True, "bearish"
        return False, "none"

    def _has_bos(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Detect break of structure in the last 3 bars.

        A bullish BOS: last bar closes above the prior swing high (reference
        window = bars[-4:-1]).
        A bearish BOS: last bar closes below the prior swing low.

        Returns (found, direction).
        """
        if len(df) < 5:
            return False, "none"

        reference = df.iloc[-4:-1]
        last_bar = df.iloc[-1]

        prior_high = float(reference["high"].max())
        prior_low = float(reference["low"].min())
        last_close = float(last_bar["close"])

        if last_close > prior_high:
            return True, "bullish"
        if last_close < prior_low:
            return True, "bearish"
        return False, "none"

    def _has_double_purge(
        self, df: pd.DataFrame, lookback: int = 30
    ) -> Tuple[bool, str]:
        """
        Detect whether both buy-side liquidity (BSL) and sell-side liquidity
        (SSL) were swept within the last `lookback` bars.

        BSL sweep: a wick above a prior swing high that closes back below it.
        SSL sweep: a wick below a prior swing low that closes back above it.

        When both occurred, the *expected* move is in the direction of the
        most-recent sweep (whichever happened last).

        Returns (found, direction_of_expected_move).
        """
        if len(df) < lookback + 5:
            return False, "none"

        window = df.iloc[-lookback:]
        reference = df.iloc[-(lookback + 5):-lookback]

        ref_high = float(reference["high"].max())
        ref_low = float(reference["low"].min())

        bsl_sweep_idx: Optional[int] = None
        ssl_sweep_idx: Optional[int] = None

        for i, (_, bar) in enumerate(window.iterrows()):
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            if h > ref_high and c < ref_high:
                bsl_sweep_idx = i
            if l < ref_low and c > ref_low:
                ssl_sweep_idx = i

        if bsl_sweep_idx is None or ssl_sweep_idx is None:
            return False, "none"

        # Direction is toward the side swept last
        if ssl_sweep_idx > bsl_sweep_idx:
            return True, "bullish"
        return True, "bearish"

    def _nearest_pd_array(
        self, df: pd.DataFrame, price: float, direction: str
    ) -> Tuple[Optional[float], Optional[float], str]:
        """
        Find the nearest Fair Value Gap (FVG) acting as a PD array.

        An FVG exists between bar[i] and bar[i+2] when:
            bullish FVG: bar[i+2].low > bar[i].high  (gap in bullish leg)
            bearish FVG: bar[i+2].high < bar[i].low  (gap in bearish leg)

        Gap must be larger than 2 × pip_size to count.

        For a long entry: look for a bullish FVG below current price.
        For a short entry: look for a bearish FVG above current price.

        Returns (entry_price, stop_price, kind) where:
            entry_price = midpoint of gap
            stop_price  = bottom/top edge of gap (beyond the array)
            kind        = "fvg_bull" | "fvg_bear" | "none"
        """
        min_gap = 2.0 * self.pip_size
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)

        best_entry: Optional[float] = None
        best_stop: Optional[float] = None
        best_kind = "none"
        best_dist = np.inf

        for i in range(n - 2):
            gap_low = lows[i + 2]
            gap_high = highs[i]
            gap_low2 = lows[i]
            gap_high2 = highs[i + 2]

            # Bullish FVG — gap is between bar[i].high and bar[i+2].low
            if direction == "bullish":
                bull_gap_bot = highs[i]
                bull_gap_top = lows[i + 2]
                if bull_gap_top > bull_gap_bot and (bull_gap_top - bull_gap_bot) >= min_gap:
                    midpoint = (bull_gap_bot + bull_gap_top) / 2.0
                    if midpoint <= price:
                        dist = price - midpoint
                        if dist < best_dist:
                            best_dist = dist
                            best_entry = float(midpoint)
                            best_stop = float(bull_gap_bot)
                            best_kind = "fvg_bull"

            # Bearish FVG — gap is between bar[i+2].high and bar[i].low
            elif direction == "bearish":
                bear_gap_bot = gap_high2
                bear_gap_top = gap_low2
                if bear_gap_top > bear_gap_bot and (bear_gap_top - bear_gap_bot) >= min_gap:
                    midpoint = (bear_gap_bot + bear_gap_top) / 2.0
                    if midpoint >= price:
                        dist = midpoint - price
                        if dist < best_dist:
                            best_dist = dist
                            best_entry = float(midpoint)
                            best_stop = float(bear_gap_top)
                            best_kind = "fvg_bear"

        if best_entry is None:
            return price, None, "none"

        return best_entry, best_stop, best_kind

    def _kill_zone_bonus(self, timestamp: datetime) -> float:
        """
        Return +1.5 if timestamp falls in London (02:00–10:00 UTC) or
        New York AM session (13:00–17:00 UTC), else 0.0.
        """
        if timestamp.tzinfo is None:
            ts_utc = timestamp.replace(tzinfo=timezone.utc)
        else:
            ts_utc = timestamp.astimezone(timezone.utc)

        hour = ts_utc.hour
        in_london = 2 <= hour < 10
        in_ny_am = 13 <= hour < 17

        if in_london or in_ny_am:
            return 1.5
        return 0.0

    # ── Entry Plans ───────────────────────────────────────────────────────────

    def eval_plan_a(
        self, df: pd.DataFrame, timestamp: datetime
    ) -> Optional[EntrySetup]:
        """
        Plan A — Sweep + BOS.

        Requirements:
            - Liquidity sweep detected in last 5 bars
            - Break of structure in last 3 bars (same direction)

        Scoring:
            sweep  = 2.0
            bos    = 2.0
            kz     = 0.0 – 1.5
            min    = 5.0

        Entry  : nearest PD array or current price
        SL     : beyond the sweep level + 5 pips
        TP     : 2.5 × risk distance
        """
        swept, sweep_dir = self._has_sweep(df)
        if not swept:
            return None

        bos_found, bos_dir = self._has_bos(df)
        if not bos_found:
            return None

        # Directions must agree
        if sweep_dir != bos_dir:
            return None

        direction = sweep_dir  # 'bullish' or 'bearish'
        current_price = float(df["close"].iloc[-1])
        kz = self._kill_zone_bonus(timestamp)
        score = 2.0 + 2.0 + kz

        if score < 5.0:
            return None

        entry_price, pd_stop, pd_kind = self._nearest_pd_array(df, current_price, direction)
        if entry_price is None:
            entry_price = current_price

        pip5 = 5.0 * self.pip_size

        if direction == "bullish":
            sweep_level = self._swing_low(df, lookback=10)
            stop_loss = sweep_level - pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price + 2.5 * risk
        else:
            sweep_level = self._swing_high(df, lookback=10)
            stop_loss = sweep_level + pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price - 2.5 * risk

        rrr = 2.5
        reason = (
            f"Plan A: {direction} sweep + BOS confirmed"
            f" | PD={pd_kind} | KZ bonus={kz:.1f}"
        )

        return EntrySetup(
            plan="A",
            direction=direction,
            entry_price=float(entry_price),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            rrr=rrr,
            score=float(score),
            reason=reason,
            timestamp=timestamp,
            valid=True,
        )

    def eval_plan_b(
        self, df: pd.DataFrame, timestamp: datetime
    ) -> Optional[EntrySetup]:
        """
        Plan B — Double Purge.

        Requirements:
            - Both BSL and SSL swept within last 30 bars

        Scoring:
            double_purge = 3.0
            kz           = 0.0 – 1.5
            min          = 4.5

        Entry  : current price
        SL     : beyond last swing + 5 pips
        TP     : 2.5 × risk
        """
        purge_found, purge_dir = self._has_double_purge(
            df, lookback=self.params.double_purge_lookback
        )
        if not purge_found:
            return None

        direction = purge_dir
        current_price = float(df["close"].iloc[-1])
        kz = self._kill_zone_bonus(timestamp)
        score = 3.0 + kz

        if score < 4.5:
            return None

        pip5 = 5.0 * self.pip_size

        if direction == "bullish":
            swing_low = self._swing_low(df, lookback=20)
            stop_loss = swing_low - pip5
            risk = abs(current_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = current_price + 2.5 * risk
        else:
            swing_high = self._swing_high(df, lookback=20)
            stop_loss = swing_high + pip5
            risk = abs(current_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = current_price - 2.5 * risk

        rrr = 2.5
        reason = (
            f"Plan B: double purge detected → {direction} expected"
            f" | KZ bonus={kz:.1f}"
        )

        return EntrySetup(
            plan="B",
            direction=direction,
            entry_price=float(current_price),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            rrr=rrr,
            score=float(score),
            reason=reason,
            timestamp=timestamp,
            valid=True,
        )

    def eval_plan_c(
        self, df: pd.DataFrame, timestamp: datetime
    ) -> Optional[EntrySetup]:
        """
        Plan C — BOS only (no sweep required).

        Requirements:
            - Break of structure in last 3 bars

        Scoring:
            bos  = 2.5
            kz   = 0.0 – 1.5
            min  = 4.0

        Entry  : nearest PD array (required; falls back to current price)
        SL     : beyond last swing + 5 pips
        TP     : 2.0 × risk
        """
        bos_found, bos_dir = self._has_bos(df)
        if not bos_found:
            return None

        direction = bos_dir
        current_price = float(df["close"].iloc[-1])
        kz = self._kill_zone_bonus(timestamp)
        score = 2.5 + kz

        if score < 4.0:
            return None

        entry_price, pd_stop, pd_kind = self._nearest_pd_array(df, current_price, direction)
        if entry_price is None:
            entry_price = current_price

        pip5 = 5.0 * self.pip_size

        if direction == "bullish":
            swing_low = self._swing_low(df, lookback=20)
            stop_loss = swing_low - pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price + 2.0 * risk
        else:
            swing_high = self._swing_high(df, lookback=20)
            stop_loss = swing_high + pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price - 2.0 * risk

        rrr = 2.0
        reason = (
            f"Plan C: {direction} BOS (no sweep)"
            f" | PD={pd_kind} | KZ bonus={kz:.1f}"
        )

        return EntrySetup(
            plan="C",
            direction=direction,
            entry_price=float(entry_price),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            rrr=rrr,
            score=float(score),
            reason=reason,
            timestamp=timestamp,
            valid=True,
        )

    def eval_plan_d(
        self, df: pd.DataFrame, timestamp: datetime
    ) -> Optional[EntrySetup]:
        """
        Plan D — HTF Bias + PD Array Zone.

        Requirements:
            - Non-neutral HTF bias
            - Price currently within a PD array zone (FVG found near price)

        Scoring:
            htf_bias = 2.0
            pd_array = 1.5  (only if pd_kind != "none")
            kz       = 0.0 – 1.5
            min      = 3.5

        Entry  : PD array midpoint
        SL     : below/above PD array edge + 5 pips
        TP     : 2.0 × risk
        """
        bias = self._htf_bias(df)
        if bias == "neutral":
            return None

        direction = bias
        current_price = float(df["close"].iloc[-1])

        entry_price, pd_stop, pd_kind = self._nearest_pd_array(df, current_price, direction)

        # Plan D requires being in a PD array zone
        if pd_kind == "none":
            return None

        kz = self._kill_zone_bonus(timestamp)
        score = 2.0 + 1.5 + kz  # htf_bias + pd_array presence + kz

        if score < 3.5:
            return None

        pip5 = 5.0 * self.pip_size

        if direction == "bullish":
            # Stop below the PD array bottom (pd_stop is the bottom edge)
            stop_ref = pd_stop if pd_stop is not None else self._swing_low(df, lookback=20)
            stop_loss = stop_ref - pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price + 2.0 * risk
        else:
            # Stop above the PD array top
            stop_ref = pd_stop if pd_stop is not None else self._swing_high(df, lookback=20)
            stop_loss = stop_ref + pip5
            risk = abs(entry_price - stop_loss)
            if risk == 0.0:
                return None
            take_profit = entry_price - 2.0 * risk

        rrr = 2.0
        reason = (
            f"Plan D: HTF bias={direction} + PD array zone ({pd_kind})"
            f" | KZ bonus={kz:.1f}"
        )

        return EntrySetup(
            plan="D",
            direction=direction,
            entry_price=float(entry_price),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            rrr=rrr,
            score=float(score),
            reason=reason,
            timestamp=timestamp,
            valid=True,
        )

    # ── Main Evaluation ───────────────────────────────────────────────────────

    def evaluate(
        self, df: pd.DataFrame, timestamp: Optional[datetime] = None
    ) -> EntryResult:
        """
        Run all four entry plans against the current bar.

        All plans are evaluated independently; any or all may fire.
        Returns an EntryResult containing every setup found, the best
        (highest score), and a market_condition description.
        """
        if timestamp is None:
            timestamp = datetime.now(tz=timezone.utc)

        empty_result = EntryResult(
            setups=[],
            best=None,
            all_valid=[],
            market_condition="insufficient_data",
        )

        if len(df) < self.MIN_BARS:
            return empty_result

        # Validate required columns
        required_cols = {"open", "high", "low", "close"}
        if not required_cols.issubset(df.columns):
            return empty_result

        # Determine market condition from HTF bias
        bias = self._htf_bias(df)
        swept, sweep_dir = self._has_sweep(df)
        bos_found, bos_dir = self._has_bos(df)

        if swept and bos_found:
            market_condition = f"sweep_bos_{sweep_dir}"
        elif bos_found:
            market_condition = f"bos_{bos_dir}"
        elif swept:
            market_condition = f"sweep_{sweep_dir}"
        elif bias != "neutral":
            market_condition = f"htf_bias_{bias}"
        else:
            market_condition = "ranging"

        # Evaluate all four plans independently
        setups: List[EntrySetup] = []

        plan_a = self.eval_plan_a(df, timestamp)
        if plan_a is not None:
            setups.append(plan_a)

        plan_b = self.eval_plan_b(df, timestamp)
        if plan_b is not None:
            setups.append(plan_b)

        plan_c = self.eval_plan_c(df, timestamp)
        if plan_c is not None:
            setups.append(plan_c)

        plan_d = self.eval_plan_d(df, timestamp)
        if plan_d is not None:
            setups.append(plan_d)

        all_valid = [s for s in setups if s.valid]
        best: Optional[EntrySetup] = None
        if all_valid:
            best = max(all_valid, key=lambda s: s.score)

        return EntryResult(
            setups=setups,
            best=best,
            all_valid=all_valid,
            market_condition=market_condition,
        )
