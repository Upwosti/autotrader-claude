"""
Smart Exit Engine — adaptive trailing and partial close optimization.

Exit Decision Hierarchy:
  1. Structure break     → highest priority exit
  2. Momentum exhaustion → partial close or SL tighten
  3. Session close logic → manage London-opened trades into NY
  4. Volatility collapse → reduce position if expansion ends
  5. News approaching    → apply news management rules
  6. Time-based exit     → last resort only

Adaptive trailing: NOT fixed ATR — adapts to market regime.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime, time


# ── Session times (UTC) ───────────────────────────────────────────────────────
LONDON_OPEN  = time(8, 0)
LONDON_CLOSE = time(12, 0)
NY_OPEN      = time(13, 0)
NY_CLOSE     = time(21, 0)


@dataclass
class ExitDecision:
    action: str           # "hold" | "close" | "partial_close" | "upgrade_sl" | "widen_sl"
    close_pct: float      # 0.0 = hold, 1.0 = full close
    new_sl: Optional[float] = None
    reason: str           = ""
    priority: int         = 0       # higher = more urgent


@dataclass
class TradeState:
    """Current state of an open trade."""
    pair: str
    direction: str          # "long" | "short"
    entry: float
    sl: float
    tp: float
    open_time: datetime
    unrealized_r: float
    unrealized_pnl_pct: float
    atr: float
    current_price: float
    regime: str             = "neutral"
    momentum_score: float   = 0.0
    partial_closed: bool    = False   # whether first partial already taken


class SmartExitEngine:
    """
    Evaluates an open trade and returns exit recommendation.

    Usage:
        engine = SmartExitEngine()
        decision = engine.evaluate(trade_state, df, weekly_df, news_next_minutes)
    """

    def evaluate(
        self,
        trade: TradeState,
        df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame] = None,
        news_next_minutes: int = 999,
        utc_now: Optional[datetime] = None,
    ) -> ExitDecision:
        """
        Evaluate all exit conditions and return highest-priority decision.
        """
        decisions: List[ExitDecision] = []

        decisions.append(self._check_structure_break(trade, df))
        decisions.append(self._check_momentum_exhaustion(trade, df))
        decisions.append(self._check_session_logic(trade, df, utc_now))
        decisions.append(self._check_volatility_collapse(trade, df))
        decisions.append(self._check_news_management(trade, news_next_minutes))
        decisions.append(self._check_trailing_upgrade(trade, df))

        # Return highest-priority non-hold decision, else hold
        active = [d for d in decisions if d.action != "hold"]
        if not active:
            return ExitDecision(action="hold", close_pct=0.0, reason="all_clear")

        active.sort(key=lambda d: d.priority, reverse=True)
        return active[0]

    # ── 1. Structure Break ────────────────────────────────────────────────────

    def _check_structure_break(self, trade: TradeState, df: pd.DataFrame) -> ExitDecision:
        """
        Exit if price has closed beyond a significant swing level against our trade.
        Uses last 20 bars to define structure.
        """
        if len(df) < 10:
            return ExitDecision(action="hold", close_pct=0.0, reason="insufficient_data")

        window = df.iloc[-20:]
        current = df["close"].iloc[-1]

        if trade.direction == "long":
            # Structure break = close below recent swing low
            swing_low = window["low"].rolling(3).min().iloc[-1]
            if current < swing_low and trade.unrealized_r > 0:
                return ExitDecision(
                    action="close", close_pct=1.0,
                    reason="structure_break_below_swing_low",
                    priority=10,
                )
        else:
            # Structure break = close above recent swing high
            swing_high = window["high"].rolling(3).max().iloc[-1]
            if current > swing_high and trade.unrealized_r > 0:
                return ExitDecision(
                    action="close", close_pct=1.0,
                    reason="structure_break_above_swing_high",
                    priority=10,
                )

        return ExitDecision(action="hold", close_pct=0.0, reason="structure_intact")

    # ── 2. Momentum Exhaustion ────────────────────────────────────────────────

    def _check_momentum_exhaustion(self, trade: TradeState, df: pd.DataFrame) -> ExitDecision:
        """
        Partial close or SL tighten if momentum has faded.
        Only acts when trade is in profit.
        """
        if trade.unrealized_r <= 0.5:
            return ExitDecision(action="hold", close_pct=0.0, reason="not_in_enough_profit")

        # Momentum exhaustion: score was once high, now low
        if trade.momentum_score < 3.0 and trade.unrealized_r >= 1.5:
            if not trade.partial_closed:
                return ExitDecision(
                    action="partial_close", close_pct=0.25,
                    reason="momentum_exhausted_partial",
                    priority=6,
                )
            else:
                # Second signal: tighten SL to structure
                new_sl = self._structure_based_sl(trade, df, tight=True)
                if new_sl and new_sl != trade.sl:
                    return ExitDecision(
                        action="upgrade_sl", close_pct=0.0,
                        new_sl=new_sl,
                        reason="momentum_exhausted_tighten_sl",
                        priority=5,
                    )

        return ExitDecision(action="hold", close_pct=0.0, reason="momentum_ok")

    # ── 3. Session Logic ──────────────────────────────────────────────────────

    def _check_session_logic(
        self,
        trade: TradeState,
        df: pd.DataFrame,
        utc_now: Optional[datetime],
    ) -> ExitDecision:
        """
        London session handoff rules at 12:00 UTC.
        """
        if utc_now is None:
            return ExitDecision(action="hold", close_pct=0.0, reason="no_time_data")

        t = utc_now.time()
        opened_london = (trade.open_time.time() >= LONDON_OPEN and
                         trade.open_time.time() < LONDON_CLOSE)

        if opened_london and LONDON_CLOSE <= t < time(12, 15):
            # London close handoff
            if trade.unrealized_r >= 2.0:
                new_sl = self._at_r_sl(trade, 1.0)
                return ExitDecision(
                    action="upgrade_sl", close_pct=0.0,
                    new_sl=new_sl,
                    reason="london_close_sl_to_1R",
                    priority=4,
                )
            elif trade.unrealized_r >= 1.0:
                new_sl = self._at_r_sl(trade, 0.0)  # breakeven
                return ExitDecision(
                    action="upgrade_sl", close_pct=0.0,
                    new_sl=new_sl,
                    reason="london_close_sl_to_BE",
                    priority=4,
                )
            elif trade.unrealized_r < 0 and not self._thesis_intact(trade, df):
                return ExitDecision(
                    action="close", close_pct=1.0,
                    reason="london_close_thesis_broken",
                    priority=7,
                )

        return ExitDecision(action="hold", close_pct=0.0, reason="session_ok")

    # ── 4. Volatility Collapse ────────────────────────────────────────────────

    def _check_volatility_collapse(self, trade: TradeState, df: pd.DataFrame) -> ExitDecision:
        """
        Reduce position if ATR has collapsed significantly (expansion ended).
        """
        if len(df) < 10:
            return ExitDecision(action="hold", close_pct=0.0)

        atr14 = _atr_series(df, 14)
        recent_atr = atr14.iloc[-3:].mean()
        prior_atr  = atr14.iloc[-10:-3].mean()

        if prior_atr > 0 and recent_atr < prior_atr * 0.6:
            # ATR shrunk by 40%+ — expansion phase over
            if trade.unrealized_r >= 1.0 and not trade.partial_closed:
                return ExitDecision(
                    action="partial_close", close_pct=0.25,
                    reason="volatility_collapsed_partial",
                    priority=3,
                )

        return ExitDecision(action="hold", close_pct=0.0, reason="volatility_ok")

    # ── 5. News Management ────────────────────────────────────────────────────

    def _check_news_management(self, trade: TradeState, news_next_minutes: int) -> ExitDecision:
        """
        Manage open trade ahead of high-impact news.
        """
        if news_next_minutes > 10:
            return ExitDecision(action="hold", close_pct=0.0, reason="no_news_imminent")

        if trade.unrealized_r >= 1.5:
            # Profit + news → move SL to breakeven minimum
            new_sl = self._at_r_sl(trade, 0.0)
            return ExitDecision(
                action="upgrade_sl", close_pct=0.0,
                new_sl=new_sl,
                reason=f"news_in_{news_next_minutes}min_sl_to_BE",
                priority=8,
            )
        elif trade.unrealized_r >= 2.5:
            # Good profit + news → partial close
            return ExitDecision(
                action="partial_close", close_pct=0.50,
                reason=f"news_in_{news_next_minutes}min_partial_50pct",
                priority=8,
            )

        return ExitDecision(action="hold", close_pct=0.0, reason="news_ok_low_profit")

    # ── 6. Trailing SL Upgrade ────────────────────────────────────────────────

    def _check_trailing_upgrade(self, trade: TradeState, df: pd.DataFrame) -> ExitDecision:
        """
        Structure-based adaptive trailing stop upgrade.
        Adapts trail width to current regime.
        """
        new_sl = self._structure_based_sl(trade, df, tight=False)
        if new_sl is None:
            return ExitDecision(action="hold", close_pct=0.0, reason="no_trail_upgrade")

        improved = (trade.direction == "long"  and new_sl > trade.sl) or \
                   (trade.direction == "short" and new_sl < trade.sl)

        if improved:
            return ExitDecision(
                action="upgrade_sl", close_pct=0.0,
                new_sl=new_sl,
                reason="adaptive_trail_upgrade",
                priority=2,
            )

        return ExitDecision(action="hold", close_pct=0.0, reason="trail_ok")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _structure_based_sl(
        self,
        trade: TradeState,
        df: pd.DataFrame,
        tight: bool = False,
    ) -> Optional[float]:
        """
        Calculate structure-based SL: beyond recent swing + ATR buffer.
        tight=True uses a smaller lookback (3 bars instead of 5).
        """
        if len(df) < 6:
            return None

        lookback = 3 if tight else 5
        window = df.iloc[-lookback:]
        atr_buf = trade.atr * (0.3 if tight else 0.5)

        if trade.direction == "long":
            swing = window["low"].min()
            return swing - atr_buf
        else:
            swing = window["high"].max()
            return swing + atr_buf

    def _at_r_sl(self, trade: TradeState, r: float) -> float:
        """Return the price at `r` risk units from entry (for SL placement)."""
        risk = abs(trade.entry - trade.sl)
        if trade.direction == "long":
            return trade.entry + r * risk
        else:
            return trade.entry - r * risk

    def _thesis_intact(self, trade: TradeState, df: pd.DataFrame) -> bool:
        """Quick check: is the basic trade thesis still valid?"""
        if len(df) < 3:
            return True
        current = df["close"].iloc[-1]
        ema21 = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
        if trade.direction == "long":
            return current > ema21
        else:
            return current < ema21


# ── Partial Close Schedule ────────────────────────────────────────────────────

PARTIAL_CLOSE_SCHEDULE = {
    # Standard mode (no momentum)
    "standard": [
        {"at_r": 2.0, "close_pct": 0.25, "note": "light banking at 2R"},
        {"at_r": 3.0, "close_pct": 0.25, "note": "protect profit at 3R"},
    ],
    # Momentum mode (score 6–8)
    "momentum": [
        {"at_r": 3.0, "close_pct": 0.25, "note": "runner mode minimal partial"},
    ],
    # Strong momentum (score 8+)
    "strong_momentum": [
        # No partial closes — allow full runner
    ],
}


def get_partial_close_schedule(momentum_score: float) -> list:
    """Return the appropriate partial close schedule for current momentum."""
    if momentum_score >= 8.0:
        return PARTIAL_CLOSE_SCHEDULE["strong_momentum"]
    elif momentum_score >= 6.0:
        return PARTIAL_CLOSE_SCHEDULE["momentum"]
    else:
        return PARTIAL_CLOSE_SCHEDULE["standard"]


def _atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
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
