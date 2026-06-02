"""
Backtesting engine using vectorbt + custom ICT signal generation.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from loguru import logger

from config import (
    StrategyParams, BACKTEST_INITIAL_CAPITAL,
    BACKTEST_COMMISSION_PCT, BACKTEST_SLIPPAGE_PIPS,
)
from strategy.ict_engine import ICTEngine, TradeSignal
from strategy.confidence import PD_ARRAY_QUALITY
from backtester.data_loader import DataLoader


@dataclass
class BacktestTrade:
    pair: str
    direction: str
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    stop_loss: float
    take_profit: float
    risk_pct: float
    rrr_achieved: Optional[float]
    pnl_pips: Optional[float]
    pnl_pct: Optional[float]
    outcome: str          # 'win' | 'loss' | 'open'
    session: str
    confidence_score: float


@dataclass
class BacktestResult:
    strategy_version: int
    pair: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_rrr: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    expectancy: float = 0.0
    monte_carlo_survival: float = 1.0
    walk_forward_stability: float = 1.0
    trades: List[BacktestTrade] = field(default_factory=list)
    overfitting_flag: bool = False
    small_sample_flag: bool = False

    def composite_score(self) -> float:
        """Multi-metric score for evolution selection (higher = better)."""
        dd_penalty = max(0.0, self.max_drawdown_pct - 5.0) * 0.5
        pf_score = min(self.profit_factor, 5.0) * 0.5
        return (
            self.expectancy * 2.0
            + self.win_rate * 3.0
            + pf_score
            + self.sharpe_ratio * 0.5
            + self.walk_forward_stability * 1.5
            - dd_penalty
        )

    def summary(self) -> Dict:
        return {
            "version": self.strategy_version,
            "pair": self.pair,
            "total_return_pct": round(self.total_return_pct, 2),
            "win_rate": round(self.win_rate, 4),
            "total_trades": self.total_trades,
            "avg_rrr": round(self.avg_rrr, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 4),
            "monte_carlo_survival": round(self.monte_carlo_survival, 4),
            "walk_forward_stability": round(self.walk_forward_stability, 4),
            "composite_score": round(self.composite_score(), 4),
            "overfitting_flag": self.overfitting_flag,
            "small_sample_flag": self.small_sample_flag,
        }


class BacktestEngine:
    """Runs walk-forward backtests on ICT strategy signals."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.loader = DataLoader()

    def _precompute_signals(self, engine, h4_df, daily_df, weekly_df, lookback, n):
        """Pre-detect all PD arrays / sweeps / BOS / double-purges ONCE on the
        full series, then emit signals per evaluation bar using cheap index
        lookups.

        This replaces the old approach of re-running every detector over an
        expanding window on every bar (~75s per run). Detection cost is now O(1)
        across the whole series instead of O(bars) full re-scans.
        """
        # One-shot detection on the full H4 series.
        all_sweeps = engine.liquidity.detect_sweeps(
            h4_df, engine.liquidity.find_levels(h4_df))
        all_bos = engine.bos.detect(h4_df)
        all_pd = engine.pd_scanner.scan_all(h4_df)
        all_dp = engine.double_purge.detect(h4_df, lookback=n)

        # Sort by the bar index at which each becomes known.
        all_sweeps.sort(key=lambda s: s.sweep_candle_index)
        all_bos.sort(key=lambda b: b.break_candle_index)
        all_pd.sort(key=lambda h: h.index)
        all_dp.sort(key=lambda d: d.index)

        close = h4_df["close"].to_numpy(dtype=float)
        index = h4_df.index

        # Daily/weekly bias evolves slowly; recompute only when the HTF slice
        # actually advances to a new bar.
        signals = []
        last_d1 = -1
        last_w1 = -1
        htf_bias = "neutral"

        si = bi = pi = di = 0
        cur_sweep = cur_bos = cur_dp = None
        # Track most-recent PD hit per direction for fast nearest lookups.
        for i in range(lookback, n - 1):
            # advance sweep/bos/dp pointers to include everything known by bar i
            while si < len(all_sweeps) and all_sweeps[si].sweep_candle_index <= i:
                if all_sweeps[si].confirmed:
                    cur_sweep = all_sweeps[si]
                si += 1
            while bi < len(all_bos) and all_bos[bi].break_candle_index <= i:
                cur_bos = all_bos[bi]
                bi += 1
            while pi < len(all_pd) and all_pd[pi].index <= i:
                pi += 1
            while di < len(all_dp) and all_dp[di].index <= i:
                cur_dp = all_dp[di]
                di += 1
            known_pd = all_pd[:pi]

            d1_end = max(1, i // 4)
            w1_end = max(1, i // 20)
            if d1_end != last_d1 or w1_end != last_w1:
                htf_bias = engine._get_htf_bias(
                    daily_df.iloc[:d1_end], weekly_df.iloc[:w1_end])
                last_d1, last_w1 = d1_end, w1_end

            current_time = index[i]
            ct = current_time.to_pydatetime() if hasattr(current_time, "to_pydatetime") else current_time
            sig = self._fast_signal(
                engine, h4_df, i, float(close[i]), ct,
                cur_sweep, cur_bos, known_pd, htf_bias, cur_dp,
            )
            signals.append((i, sig))
        return signals

    @staticmethod
    def _nearest_pd(known_pd, want, price):
        """Highest-priority PD array of the wanted direction nearest to price."""
        prio = {k: idx for idx, k in enumerate(
            ["BB", "MB", "OB", "PB", "RB", "IRB", "IFVG", "VI", "LV", "BPR"])}
        cands = [h for h in known_pd if h.direction == want]
        if not cands:
            return None
        cands.sort(key=lambda h: (prio.get(h.kind, 99), abs(price - h.midpoint)))
        return cands[0]

    def _fast_signal(self, engine, h4_df, i, price, ct,
                     sweep, bos, known_pd, htf_bias, dp=None):
        """Build a TradeSignal from pre-computed events (no re-detection).

        Multiple independent entry plans are honoured (per the ICT material there
        is more than one valid way to enter). Direction is resolved by the first
        plan that fires, in order of strength:

            Plan A  sweep + BOS aligned          (classic liquidity-grab reversal)
            Plan B  double purge                 (both sides swept → run opposite)
            Plan C  BOS alone                    (market-structure shift)
            Plan D  HTF bias + matching PD array  (continuation into a PD array)
        """
        in_kz, session = engine._in_kill_zone(ct)

        direction = None
        plan = "none"
        # Plan A — liquidity sweep confirmed by a BOS in the same direction.
        if sweep and bos:
            if sweep.direction == "bullish_sweep" and bos.direction == "bullish_bos":
                direction, plan = "long", "sweep+bos"
            elif sweep.direction == "bearish_sweep" and bos.direction == "bearish_bos":
                direction, plan = "short", "sweep+bos"
        # Plan B — double purge: trade opposite the last side that was purged.
        if direction is None and dp is not None:
            direction = "long" if dp.direction == "bullish" else "short"
            plan = "double_purge"
        # Plan C — a market-structure shift on its own.
        if direction is None and bos is not None:
            direction = "long" if bos.direction == "bullish_bos" else "short"
            plan = "bos"
        # Plan D — HTF bias continuation that has a PD array to enter against.
        if direction is None and htf_bias in ("bullish", "bearish"):
            want = "bullish" if htf_bias == "bullish" else "bearish"
            if self._nearest_pd(known_pd, want, price) is not None:
                direction = "long" if htf_bias == "bullish" else "short"
                plan = "htf+pd"

        if direction is None:
            score = engine.scorer.score(
                sweep=sweep, bos=bos, fvg=None, in_kill_zone=in_kz,
                higher_tf_bias_aligned=False, displacement_present=False,
                spread_ok=True, news_clear=True, dxy_conflict=False, pair=engine.pair,
            )
            return TradeSignal(
                pair=engine.pair, direction="none", entry_price=price,
                stop_loss=0, take_profit=0, rrr=0, confidence=score,
                sweep=sweep, bos=bos, fvg=None, session=session, timestamp=ct,
                valid=False, reason="[SKIP] no entry plan matched", pd_array=None,
            )

        want = "bullish" if direction == "long" else "bearish"
        pd_hit = self._nearest_pd(known_pd, want, price)

        displacement = bos.displacement if bos else False
        htf_aligned = (
            (direction == "long" and htf_bias == "bullish") or
            (direction == "short" and htf_bias == "bearish")
        )
        dp_aligned = bool(dp and (
            (direction == "long" and dp.direction == "bullish") or
            (direction == "short" and dp.direction == "bearish")
        ))

        score = engine.scorer.score(
            sweep=sweep, bos=bos, fvg=None, in_kill_zone=in_kz,
            higher_tf_bias_aligned=htf_aligned, displacement_present=displacement,
            spread_ok=True, news_clear=True, dxy_conflict=False, pair=engine.pair,
            pd_array_kind=pd_hit.kind if pd_hit else None,
            double_purge=dp_aligned,
        )

        if not score.passed:
            return TradeSignal(
                pair=engine.pair, direction=direction,
                entry_price=price, stop_loss=0, take_profit=0, rrr=0,
                confidence=score, sweep=sweep, bos=bos, fvg=None,
                session=session, timestamp=ct, valid=False,
                reason=f"[{plan}] " + score.reason, pd_array=pd_hit,
            )

        entry = pd_hit.midpoint if pd_hit else price
        sl = engine._calc_stop_loss(direction, sweep, pd_hit, entry)
        tp = engine._calc_take_profit(direction, entry, sl, None)
        rrr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        valid = rrr >= engine.params.min_rrr
        return TradeSignal(
            pair=engine.pair, direction=direction, entry_price=entry,
            stop_loss=sl, take_profit=tp, rrr=rrr, confidence=score,
            sweep=sweep, bos=bos, fvg=None, session=session, timestamp=ct,
            valid=valid, reason=f"[{plan}] " + score.reason + f" | RRR={rrr:.2f}",
            pd_array=pd_hit,
        )

    def run(
        self,
        pair: str = "XAUUSD",
        timeframe: str = "H4",
        initial_capital: float = BACKTEST_INITIAL_CAPITAL,
        use_synthetic: bool = True,
    ) -> BacktestResult:
        """Execute a full backtest for the given pair."""
        logger.info(f"Starting backtest: {pair} {timeframe} v{self.params.version}")

        h4_df = self.loader.load(pair, "H4", synthetic_fallback=use_synthetic)
        daily_df = self.loader.load(pair, "D1", synthetic_fallback=use_synthetic)
        weekly_df = self.loader.load(pair, "W1", synthetic_fallback=use_synthetic)

        engine = ICTEngine(self.params, pair=pair)
        pip_size = engine.pip_size
        commission = BACKTEST_COMMISSION_PCT / 100
        slippage = BACKTEST_SLIPPAGE_PIPS * pip_size

        trades: List[BacktestTrade] = []
        capital = initial_capital
        equity_curve = [capital]
        peak = capital
        max_dd = 0.0
        n = len(h4_df)
        lookback = max(self.params.liquidity_sweep_lookback, self.params.bos_lookback) + 10

        # ─── PRE-COMPUTE PD arrays / sweeps / BOS ONCE on the full series ──────
        # Each detected event carries the bar index at which it becomes known.
        # In the loop we only consider events with index <= current bar, so no
        # re-detection per bar is required (the slow part of the old engine).
        signals = self._precompute_signals(
            engine, h4_df, daily_df, weekly_df, lookback, n)

        # Vectorised next-candle arrays for fast outcome simulation.
        nc_high = h4_df["high"].to_numpy(dtype=float)
        nc_low = h4_df["low"].to_numpy(dtype=float)
        nc_close = h4_df["close"].to_numpy(dtype=float)

        for i, signal in signals:
            if not signal.valid:
                continue

            # Simulate trade outcome on next candle (vectorised lookup)
            next_high = nc_high[i + 1]
            next_low = nc_low[i + 1]
            next_close = nc_close[i + 1]
            entry = signal.entry_price + slippage * (1 if signal.direction == "long" else -1)
            sl = signal.stop_loss
            tp = signal.take_profit
            current_time = h4_df.index[i]

            # Determine outcome
            if signal.direction == "long":
                if next_low <= sl:
                    outcome = "loss"
                    exit_price = sl
                elif next_high >= tp:
                    outcome = "win"
                    exit_price = tp
                else:
                    outcome = "open"
                    exit_price = next_close
            else:
                if next_high >= sl:
                    outcome = "loss"
                    exit_price = sl
                elif next_low <= tp:
                    outcome = "win"
                    exit_price = tp
                else:
                    outcome = "open"
                    exit_price = next_close

            risk_amount = capital * (self.params.risk_pct / 100 if hasattr(self.params, "risk_pct") else 0.01)
            pnl_pips = (exit_price - entry) / pip_size if signal.direction == "long" else (entry - exit_price) / pip_size
            risk_pips = abs(entry - sl) / pip_size
            pnl_pct = (pnl_pips / risk_pips) * 0.01 if risk_pips > 0 else 0
            pnl_amount = capital * pnl_pct
            capital += pnl_amount - (capital * commission * 2)

            equity_curve.append(capital)
            dd = (peak - capital) / peak * 100 if capital < peak else 0.0
            peak = max(peak, capital)
            max_dd = max(max_dd, dd)

            rrr = abs(pnl_pips / risk_pips) if risk_pips > 0 else 0

            trades.append(BacktestTrade(
                pair=pair,
                direction=signal.direction,
                entry_time=current_time,
                exit_time=h4_df.index[i + 1],
                entry_price=entry,
                exit_price=exit_price,
                stop_loss=sl,
                take_profit=tp,
                risk_pct=1.0,
                rrr_achieved=rrr,
                pnl_pips=pnl_pips,
                pnl_pct=pnl_pct * 100,
                outcome=outcome,
                session=signal.session,
                confidence_score=signal.confidence.total,
            ))

        # ─── Statistics ──────────────────────────────────────────────────
        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        win_rate = len(wins) / len(trades) if trades else 0.0
        avg_rrr = float(np.mean([t.rrr_achieved for t in trades if t.rrr_achieved])) if trades else 0.0
        total_return = (capital - initial_capital) / initial_capital * 100

        gross_profit = sum(t.pnl_pips or 0 for t in wins)
        gross_loss = abs(sum(t.pnl_pips or 0 for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity_arr = np.array(equity_curve)
        returns = np.diff(equity_arr) / equity_arr[:-1]
        sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 6)) if np.std(returns) > 0 else 0.0

        # Expectancy: (WR × avg_win_pips) - (LR × avg_loss_pips)
        avg_win = float(np.mean([t.pnl_pips for t in wins])) if wins else 0.0
        avg_loss = float(np.mean([abs(t.pnl_pips) for t in losses])) if losses else 0.0
        loss_rate = 1.0 - win_rate
        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

        # Monte Carlo survival: simulate 500 random sequences, check max DD < 20%
        mc_survival = self._monte_carlo_survival(trades, initial_capital, runs=500, max_dd_limit=20.0)

        # Walk-forward stability: split into 3 segments, compare win rates
        wf_stability = self._walk_forward_stability(trades)

        small_sample = len(trades) < 30
        overfitting = win_rate > 0.75 and len(trades) < 50

        if small_sample:
            logger.warning(f"Small sample: {len(trades)} trades — results may not be reliable")
        if overfitting:
            logger.warning(f"Overfitting warning: {win_rate:.1%} win rate on {len(trades)} trades")

        return BacktestResult(
            strategy_version=self.params.version,
            pair=pair,
            timeframe=timeframe,
            start_date=str(h4_df.index[lookback].date()),
            end_date=str(h4_df.index[-1].date()),
            initial_capital=initial_capital,
            final_capital=capital,
            total_return_pct=total_return,
            win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_rrr=avg_rrr,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            expectancy=expectancy,
            monte_carlo_survival=mc_survival,
            walk_forward_stability=wf_stability,
            trades=trades,
            overfitting_flag=overfitting,
            small_sample_flag=small_sample,
        )

    def _monte_carlo_survival(self, trades: list, initial_capital: float,
                               runs: int = 500, max_dd_limit: float = 20.0) -> float:
        """Fraction of Monte Carlo runs where max drawdown stays below max_dd_limit%."""
        if len(trades) < 10:
            return 1.0
        pnl_pcts = np.array([t.pnl_pct / 100 for t in trades if t.pnl_pct is not None])
        if len(pnl_pcts) == 0:
            return 1.0
        rng = np.random.default_rng(42)
        survived = 0
        for _ in range(runs):
            seq = rng.choice(pnl_pcts, size=len(pnl_pcts), replace=True)
            cap = initial_capital
            peak = cap
            dd = 0.0
            for r in seq:
                cap *= (1 + r)
                if cap > peak:
                    peak = cap
                dd = max(dd, (peak - cap) / peak * 100)
            if dd < max_dd_limit:
                survived += 1
        return survived / runs

    def _walk_forward_stability(self, trades: list) -> float:
        """Stability score: 1 - std(segment_win_rates) / mean(segment_win_rates).
        Higher = more consistent across time segments."""
        if len(trades) < 15:
            return 1.0
        n = len(trades)
        thirds = [trades[:n // 3], trades[n // 3: 2 * n // 3], trades[2 * n // 3:]]
        wrs = []
        for seg in thirds:
            if seg:
                wr = sum(1 for t in seg if t.outcome == "win") / len(seg)
                wrs.append(wr)
        if not wrs or np.mean(wrs) == 0:
            return 1.0
        stability = 1.0 - min(1.0, np.std(wrs) / np.mean(wrs))
        return float(stability)
