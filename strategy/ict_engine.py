"""
ICT Engine — orchestrates liquidity, BOS, FVG, and confidence scoring
to generate trade signals on H4 with weekly/daily bias.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple
import pytz

from config import StrategyParams, LONDON_KILL_ZONE, NY_KILL_ZONE
from strategy.liquidity import LiquidityDetector, LiquiditySweep
from strategy.bos import BOSDetector, BOS
from strategy.fvg import FVGDetector, FVG
from strategy.confidence import ConfidenceScorer, SetupScore


@dataclass
class TradeSignal:
    pair: str
    direction: str          # 'long' | 'short'
    entry_price: float
    stop_loss: float
    take_profit: float
    rrr: float
    confidence: SetupScore
    sweep: Optional[LiquiditySweep]
    bos: Optional[BOS]
    fvg: Optional[FVG]
    session: str
    timestamp: datetime
    valid: bool
    reason: str


class ICTEngine:
    """Main strategy engine combining all ICT concepts."""

    def __init__(self, params: StrategyParams, pair: str = "XAUUSD"):
        self.params = params
        self.pair = pair
        self.pip_size = self._get_pip_size(pair)
        self.liquidity = LiquidityDetector(params)
        self.bos = BOSDetector(params)
        self.fvg = FVGDetector(params, pip_size=self.pip_size)
        self.scorer = ConfidenceScorer(params)

    def _get_pip_size(self, pair: str) -> float:
        # XAUUSD: 1 pip = $0.10 (gold at ~4700, realistic move scale)
        # BTCUSD: 1 pip = $1.00
        # Forex pairs: 1 pip = 0.0001
        sizes = {
            "XAUUSD": 0.1,
            "BTCUSD": 1.0,
            "GBPUSD": 0.0001,
            "EURUSD": 0.0001,
        }
        return sizes.get(pair, 0.0001)

    def _in_kill_zone(self, dt: datetime, bar_hours: int = 4) -> Tuple[bool, str]:
        """
        Check if an H4 bar overlaps with a kill zone.
        Checks all hours covered by the bar (bar_hours window).
        """
        utc = pytz.utc
        if dt.tzinfo is None:
            dt = utc.localize(dt)
        bar_open_hour = dt.astimezone(utc).hour
        bar_hours_range = range(bar_open_hour, bar_open_hour + bar_hours)

        london_hours = range(self.params.london_start, self.params.london_end)
        ny_hours     = range(self.params.ny_start, self.params.ny_end)

        in_london = any(h in london_hours for h in bar_hours_range)
        in_ny     = any(h in ny_hours     for h in bar_hours_range)

        if in_london and self.params.use_london:
            return True, "london"
        if in_ny and self.params.use_ny:
            return True, "ny"
        return False, "off_session"

    def _get_htf_bias(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> str:
        """
        Determine HTF bias from daily/weekly structure.
        Requires at least 20 bars; falls back to price-slope comparison.
        """
        detector = BOSDetector(self.params)
        daily_bias  = "neutral"
        weekly_bias = "neutral"

        if daily_df is not None and len(daily_df) >= 20:
            daily_bias = detector.get_bias(daily_df)
        if weekly_df is not None and len(weekly_df) >= 10:
            weekly_bias = detector.get_bias(weekly_df)

        # If both agree → use that
        if daily_bias == weekly_bias and daily_bias != "neutral":
            return daily_bias
        # Daily takes precedence if weekly neutral
        if daily_bias != "neutral":
            return daily_bias
        # Fallback: simple 20-bar slope on daily
        if daily_df is not None and len(daily_df) >= 20:
            closes = daily_df["close"].values
            if closes[-1] > closes[-20]:
                return "bullish"
            if closes[-1] < closes[-20]:
                return "bearish"
        return weekly_bias if weekly_bias != "neutral" else "neutral"

    def _calc_stop_loss(self, direction: str, sweep: Optional[LiquiditySweep],
                        current_price: float) -> float:
        """Place SL beyond the swept level + buffer."""
        buffer = self.pip_size * 5
        if sweep:
            if direction == "long":
                return sweep.level.price - buffer
            else:
                return sweep.level.price + buffer
        # Fallback: ATR-based stop
        if direction == "long":
            return current_price - self.pip_size * 50
        return current_price + self.pip_size * 50

    def _calc_take_profit(self, direction: str, entry: float, stop: float) -> float:
        """Calculate TP based on minimum RRR."""
        risk = abs(entry - stop)
        reward = risk * self.params.min_rrr
        if direction == "long":
            return entry + reward
        return entry - reward

    def scan(self, df: pd.DataFrame, pair: str = None) -> dict:
        """
        Live scanner — wraps generate_signal() for use in trade_executor.
        Returns a setup dict if valid, else None.
        """
        if pair:
            self.pair = pair
            self.pip_size = self._get_pip_size(pair)
        if df is None or len(df) < 30:
            return None
        # Use same df for all TFs (simplified for live scan)
        signal = self.generate_signal(
            h4_df=df, daily_df=df, weekly_df=df,
            current_time=datetime.now(timezone.utc),
        )
        if not signal.valid:
            return None
        return {
            "symbol":     self.pair,
            "direction":  "buy" if signal.direction == "long" else "sell",
            "entry":      signal.entry_price,
            "sl":         signal.stop_loss,
            "tp":         signal.take_profit,
            "rrr":        signal.rrr,
            "confidence": int(signal.confidence.total),
            "session":    signal.session,
            "version":    self.params.version,
        }

    def generate_signal(
        self,
        h4_df: pd.DataFrame,
        daily_df: pd.DataFrame,
        weekly_df: pd.DataFrame,
        current_time: Optional[datetime] = None,
        spread_pips: float = 0.0,
        news_clear: bool = True,
        dxy_conflict: bool = False,
        max_spread: float = 1.0,
    ) -> TradeSignal:
        """
        Generate a trade signal from the current market data.
        Returns a TradeSignal — check signal.valid and signal.confidence.passed.
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        in_kz, session = self._in_kill_zone(current_time)
        htf_bias = self._get_htf_bias(daily_df, weekly_df)

        sweep = self.liquidity.get_latest_sweep(h4_df)
        bos = self.bos.get_latest_bos(h4_df)

        # Determine trade direction: sweep sets primary direction, BOS confirms
        direction = None
        if sweep and bos:
            # Full ICT confluence: sweep + BOS alignment
            if sweep.direction == "bullish_sweep" and bos.direction == "bullish_bos":
                direction = "long"
            elif sweep.direction == "bearish_sweep" and bos.direction == "bearish_bos":
                direction = "short"
            # CHoCH reversal: sweep against trend but BOS confirms reversal
            elif sweep.direction == "bullish_sweep" and bos.is_choch and bos.direction == "bullish_bos":
                direction = "long"
            elif sweep.direction == "bearish_sweep" and bos.is_choch and bos.direction == "bearish_bos":
                direction = "short"
        elif sweep and not bos:
            # Sweep alone with HTF bias alignment (lower-confidence setup)
            if sweep.direction == "bullish_sweep" and htf_bias in ("bullish", "neutral"):
                direction = "long"
            elif sweep.direction == "bearish_sweep" and htf_bias in ("bearish", "neutral"):
                direction = "short"

        # Hard HTF filter: block counter-trend trades when bias is clear
        # A CHoCH reversal is the only valid exception to this rule
        choch_reversal = bos is not None and bos.is_choch
        if direction == "short" and htf_bias == "bullish" and not choch_reversal:
            direction = None
        elif direction == "long" and htf_bias == "bearish" and not choch_reversal:
            direction = None

        current_price = h4_df["close"].iloc[-1]
        fvg = self.fvg.nearest_fvg(h4_df, current_price, direction or "long")

        displacement = bos.displacement if bos else False
        spread_ok = spread_pips <= max_spread
        htf_aligned = (
            (direction == "long" and htf_bias == "bullish") or
            (direction == "short" and htf_bias == "bearish")
        )

        score = self.scorer.score(
            sweep=sweep,
            bos=bos,
            fvg=fvg,
            in_kill_zone=in_kz,
            higher_tf_bias_aligned=htf_aligned,
            displacement_present=displacement,
            spread_ok=spread_ok,
            news_clear=news_clear,
            dxy_conflict=dxy_conflict,
            pair=self.pair,
        )

        if direction is None or not score.passed:
            return TradeSignal(
                pair=self.pair, direction=direction or "none",
                entry_price=current_price, stop_loss=0, take_profit=0,
                rrr=0, confidence=score, sweep=sweep, bos=bos, fvg=fvg,
                session=session, timestamp=current_time,
                valid=False, reason=score.reason,
            )

        entry = fvg.midpoint if fvg and fvg.valid else current_price
        sl = self._calc_stop_loss(direction, sweep, entry)
        tp = self._calc_take_profit(direction, entry, sl)
        rrr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        valid = rrr >= self.params.min_rrr
        reason = score.reason + f" | RRR={rrr:.2f}"

        return TradeSignal(
            pair=self.pair, direction=direction, entry_price=entry,
            stop_loss=sl, take_profit=tp, rrr=rrr, confidence=score,
            sweep=sweep, bos=bos, fvg=fvg, session=session,
            timestamp=current_time, valid=valid, reason=reason,
        )
