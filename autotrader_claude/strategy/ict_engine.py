"""
ICT Engine — orchestrates the full ICT model:
liquidity sweeps, BOS/MSS, the 12 PD arrays, key liquidity (DOL),
IRL/ERL cycle, double-purge, SMT divergence and CRT+TBS confirmation,
scored with the full confluence model.
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
from strategy.pd_arrays import PDArrayScanner, PDArrayHit
from strategy.key_liquidity import KeyLiquidityDetector, KeyLevel
from strategy.irl_erl import IRLERLAnalyzer, DOLState
from strategy.smt import SMTDivergenceDetector, SMTDivergence
from strategy.double_purge import DoublePurgeDetector, DoublePurge
from strategy.crt_tbs import CRTTBSDetector, AMDPattern


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
    pd_array: Optional[PDArrayHit] = None
    dol: Optional[DOLState] = None
    double_purge: Optional[DoublePurge] = None
    smt: Optional[SMTDivergence] = None
    key_level: Optional[KeyLevel] = None
    amd: Optional[AMDPattern] = None   # AMD 3-candle CRT pattern


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
        self.pd_scanner = PDArrayScanner(params, pip_size=self.pip_size)
        self.key_liquidity = KeyLiquidityDetector(params, pip_size=self.pip_size)
        self.irl_erl = IRLERLAnalyzer(params, pip_size=self.pip_size)
        self.smt = SMTDivergenceDetector(params)
        self.double_purge = DoublePurgeDetector(params, pip_size=self.pip_size)
        self.crt_tbs = CRTTBSDetector(params, pip_size=self.pip_size)

    def _get_pip_size(self, pair: str) -> float:
        sizes = {
            "XAUUSD": 0.01, "BTCUSD": 1.0,
            "GBPUSD": 0.0001, "EURUSD": 0.0001,
        }
        return sizes.get(pair, 0.0001)

    def _in_kill_zone(self, dt: datetime) -> Tuple[bool, str]:
        utc = pytz.utc
        if dt.tzinfo is None:
            dt = utc.localize(dt)
        hour = dt.astimezone(utc).hour
        london = self.params.london_start <= hour < self.params.london_end
        ny = self.params.ny_start <= hour < self.params.ny_end
        if london and self.params.use_london:
            return True, "london"
        if ny and self.params.use_ny:
            return True, "ny"
        return False, "off_session"

    def _get_htf_bias(self, daily_df: pd.DataFrame, weekly_df: pd.DataFrame) -> str:
        daily_bos = self.bos.get_bias(daily_df)
        weekly_bos = self.bos.get_bias(weekly_df)
        if daily_bos == weekly_bos:
            return daily_bos
        if daily_bos != "neutral":
            return daily_bos
        return weekly_bos

    # ── Consolidation filter (ADR vs ATR) ─────────────────────────────────
    def _is_consolidating(self, df: pd.DataFrame) -> bool:
        """True if the recent average daily-ish range is small relative to ATR,
        i.e. ADR < adr_atr_min_ratio * ATR(period). Skips chop."""
        period = self.params.atr_period
        n = len(df)
        if n < period + 2:
            return False
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        prev_c = np.roll(c, 1)
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        tr[0] = h[0] - l[0]
        atr = float(np.mean(tr[-period:]))
        # ADR proxy = average current-range over the last few bars
        adr = float(np.mean((h - l)[-min(period, 5):]))
        if atr <= 0:
            return False
        return (adr / atr) < self.params.adr_atr_min_ratio

    def _calc_stop_loss(self, direction: str, sweep: Optional[LiquiditySweep],
                        pd_array: Optional[PDArrayHit], entry: float) -> float:
        buffer = self.pip_size * 5
        if sweep:
            return (sweep.level.price - buffer) if direction == "long" else (sweep.level.price + buffer)
        if pd_array:
            return (pd_array.bottom - buffer) if direction == "long" else (pd_array.top + buffer)
        return (entry - self.pip_size * 50) if direction == "long" else (entry + self.pip_size * 50)

    def _calc_take_profit(self, direction: str, entry: float, stop: float,
                          key_level: Optional[KeyLevel]) -> float:
        risk = abs(entry - stop)
        reward = risk * self.params.min_rrr
        target = (entry + reward) if direction == "long" else (entry - reward)
        # if a key liquidity DOL target gives at least min RRR, prefer it
        if key_level:
            kl_reward = abs(key_level.price - entry)
            if kl_reward >= reward:
                target = key_level.price
        return target

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
        smt_partner_df: Optional[pd.DataFrame] = None,
    ) -> TradeSignal:
        """Multi-step ICT analysis producing a TradeSignal."""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        current_price = float(h4_df["close"].iloc[-1])
        in_kz, session = self._in_kill_zone(current_time)

        # Step 1 — HTF bias + IRL/ERL DOL
        htf_bias = self._get_htf_bias(daily_df, weekly_df)
        dol = self.irl_erl.get_current_dol(h4_df, daily_df)

        # Consolidation filter — skip chop early
        consolidating = self._is_consolidating(h4_df)

        # Step 3 — double purge (highest-confidence directional read)
        dp = self.double_purge.get_latest(h4_df, lookback=self.params.double_purge_lookback)

        # Liquidity sweep + BOS
        sweep = self.liquidity.get_latest_sweep(h4_df)
        bos = self.bos.get_latest_bos(h4_df)

        # AMD 3-candle pattern (detect before direction logic)
        amd = self.crt_tbs.detect_amd(h4_df)

        # Step 7 — determine direction (AMD 3-candle > sweep+BOS > dp > DOL)
        direction = None
        # AMD pattern: entry on the 3rd candle manipulation close
        if amd is not None and amd.valid:
            direction = "long" if amd.direction == "bullish" else "short"
        if direction is None and sweep and bos:
            if sweep.direction == "bullish_sweep" and bos.direction == "bullish_bos":
                direction = "long"
            elif sweep.direction == "bearish_sweep" and bos.direction == "bearish_bos":
                direction = "short"
        if direction is None and dp is not None:
            direction = "long" if dp.direction == "bullish" else "short"
        if direction is None and dol.direction in ("bullish", "bearish"):
            direction = "long" if dol.direction == "bullish" else "short"

        # Step 2 — key liquidity DOL target
        key_level = self.key_liquidity.get_nearest_target(
            h4_df, current_price, direction or "long")

        # Step 4 — best PD array in direction (priority order inside scanner)
        pd_hit = self.pd_scanner.best_entry(h4_df, current_price, direction or "long") if direction else None

        # legacy FVG for the dataclass / scoring fallback
        fvg = self.fvg.nearest_fvg(h4_df, current_price, direction or "long")

        # Step 5 — CRT + TBS confirmation (standard)
        crt = self.crt_tbs.detect_crt(h4_df)
        tbs = self.crt_tbs.detect_tbs(h4_df, crt) if crt else None
        crt_ok = self.crt_tbs.is_high_probability_tbs(crt, tbs) if (crt and tbs) else False

        # Step 6 — SMT divergence confluence
        smt_div = None
        if smt_partner_df is not None and len(smt_partner_df) > 3:
            smt_div = self.smt.detect_divergence(
                h4_df, smt_partner_df, lookback=self.params.smt_lookback)

        # confluence flags
        displacement = bos.displacement if bos else False
        spread_ok = spread_pips <= max_spread
        htf_aligned = (
            (direction == "long" and htf_bias == "bullish") or
            (direction == "short" and htf_bias == "bearish")
        )
        dol_aligned = (
            (direction == "long" and dol.direction == "bullish") or
            (direction == "short" and dol.direction == "bearish")
        )
        smt_aligned = bool(
            smt_div and (
                (direction == "long" and smt_div.direction == "bullish") or
                (direction == "short" and smt_div.direction == "bearish")
            )
        )
        dp_aligned = bool(
            dp and (
                (direction == "long" and dp.direction == "bullish") or
                (direction == "short" and dp.direction == "bearish")
            )
        )
        crt_aligned = bool(
            crt_ok and tbs and (
                (direction == "long" and tbs.direction == "bullish") or
                (direction == "short" and tbs.direction == "bearish")
            )
        )

        score = self.scorer.score(
            sweep=sweep, bos=bos, fvg=fvg,
            in_kill_zone=in_kz,
            higher_tf_bias_aligned=htf_aligned,
            displacement_present=displacement,
            spread_ok=spread_ok, news_clear=news_clear,
            dxy_conflict=dxy_conflict, pair=self.pair,
            pd_array_kind=pd_hit.kind if pd_hit else None,
            double_purge=dp_aligned,
            smt_divergence=smt_aligned,
            crt_tbs=crt_aligned,
            irl_erl_aligned=dol_aligned,
            key_liquidity_dol=key_level is not None,
        )

        if direction is None or consolidating or not score.passed:
            reason = score.reason
            if consolidating:
                reason = "[SKIP] consolidation (ADR<ATR ratio) | " + reason
            return TradeSignal(
                pair=self.pair, direction=direction or "none",
                entry_price=current_price, stop_loss=0, take_profit=0,
                rrr=0, confidence=score, sweep=sweep, bos=bos, fvg=fvg,
                session=session, timestamp=current_time, valid=False,
                reason=reason, pd_array=pd_hit, dol=dol,
                double_purge=dp, smt=smt_div, key_level=key_level, amd=amd,
            )

        # Entry priority: AMD manipulation close > PD array > FVG > current price
        if amd and amd.valid and amd.entry_price > 0:
            entry = amd.entry_price
            sl = amd.stop_price
        elif pd_hit:
            entry = pd_hit.midpoint
            sl = self._calc_stop_loss(direction, sweep, pd_hit, entry)
        elif fvg and fvg.valid:
            entry = fvg.midpoint
            sl = self._calc_stop_loss(direction, sweep, None, entry)
        else:
            entry = current_price
            sl = self._calc_stop_loss(direction, sweep, None, entry)

        tp = self._calc_take_profit(direction, entry, sl, key_level)
        rrr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        valid = rrr >= self.params.min_rrr
        amd_tag = " | AMD-3candle" if (amd and amd.valid) else ""
        reason = score.reason + f" | RRR={rrr:.2f}{amd_tag}"

        return TradeSignal(
            pair=self.pair, direction=direction, entry_price=entry,
            stop_loss=sl, take_profit=tp, rrr=rrr, confidence=score,
            sweep=sweep, bos=bos, fvg=fvg, session=session,
            timestamp=current_time, valid=valid, reason=reason,
            pd_array=pd_hit, dol=dol, double_purge=dp, smt=smt_div,
            key_level=key_level, amd=amd,
        )
