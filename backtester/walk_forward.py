"""
Walk-forward backtester for HighConfluenceTrend strategy.

Runs bar-by-bar simulation on daily (D1) data, one trade at a time.
Enforces:
  - 70 / 30 train / test split
  - Minimum hold time (skip ultra-fast SL hits)
  - 6-bar cooldown after stop-loss
  - Minimum trade count guard before declaring result valid
  - Per-pair and aggregate statistics
  - Spread + slippage + commission deduction (realistic WR)
  - Partial close at 1:1 + trailing stop
  - News blackout filter
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from loguru import logger

from strategy.trend_engine import HighConfluenceTrend, TrendParams, TradeSignal
from backtester.costs import (
    get_round_trip_cost_fraction, adjust_entry_for_costs, adjust_exit_for_costs
)
from strategy.news_filter import is_news_blackout

try:
    from strategy.ict_advanced import ICTAdvancedScorer
    _ICT_SCORER = ICTAdvancedScorer()
    _ICT_AVAILABLE = True
except Exception:
    _ICT_AVAILABLE = False
    _ICT_SCORER = None


@dataclass
class WFTrade:
    pair: str
    direction: str
    entry_date: object
    exit_date: object
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    rrr_target: float
    rrr_achieved: float
    outcome: str          # 'win' | 'loss'  (raw, before costs)
    pnl_pct: float        # % of account (raw)
    confluence: int
    pattern: str
    regime: str
    hold_bars: int
    split: str            # 'train' | 'test'
    cost_fraction: float  = 0.0   # round-trip cost as fraction of risk
    realistic_outcome: str = ""   # 'win' | 'loss' | 'scratch' after cost adjustment
    realistic_pnl_pct: float = 0.0  # pnl after costs


@dataclass
class WFResult:
    params_version: int
    pair: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    # Test-set metrics (primary)
    test_win_rate: float           # raw WR before costs
    test_win_rate_realistic: float = 0.0  # WR after spread/slip/commission
    test_trades: int = 0
    test_return_pct: float = 0.0
    test_max_dd_pct: float = 0.0
    test_sharpe: float = 0.0
    test_profit_factor: float = 0.0
    test_avg_rrr: float = 0.0
    test_avg_rrr_realistic: float = 0.0
    # Train-set metrics (for overfitting check)
    train_win_rate: float = 0.0
    train_trades: int = 0
    # All trades
    trades: List[WFTrade] = field(default_factory=list)
    overfitting_flag: bool = False
    small_sample_flag: bool = False
    strategy_name: str = "HighConfluenceTrend"

    def composite_score(self) -> float:
        """
        Single scalar used by evolution. Uses REALISTIC WR (after costs).
        Penalises DD and overfitting.
        """
        if self.test_trades < 15:
            return 0.0
        wr = self.test_win_rate_realistic if self.test_win_rate_realistic > 0 \
             else self.test_win_rate
        reliability = min(1.0, self.test_trades / 100)
        dd_penalty  = max(0.0, (self.test_max_dd_pct - 5.0) / 100.0)
        pf_bonus    = min(0.3, (self.test_profit_factor - 1.0) * 0.1)
        overfit_pen = 0.1 if self.overfitting_flag else 0.0
        rrr_bonus   = min(0.1, max(0.0, (self.test_avg_rrr_realistic - 1.3) * 0.05))
        return (wr * reliability + pf_bonus + rrr_bonus) - dd_penalty - overfit_pen

    def summary(self) -> Dict:
        return {
            "version":                 self.params_version,
            "pair":                    self.pair,
            "test_win_rate":           round(self.test_win_rate, 4),
            "test_win_rate_realistic": round(self.test_win_rate_realistic, 4),
            "test_trades":             self.test_trades,
            "test_return_pct":         round(self.test_return_pct, 2),
            "test_max_dd_pct":         round(self.test_max_dd_pct, 2),
            "test_sharpe":             round(self.test_sharpe, 2),
            "test_profit_factor":      round(self.test_profit_factor, 2),
            "test_avg_rrr":            round(self.test_avg_rrr, 2),
            "test_avg_rrr_realistic":  round(self.test_avg_rrr_realistic, 2),
            "train_win_rate":          round(self.train_win_rate, 4),
            "train_trades":            self.train_trades,
            "composite_score":         round(self.composite_score(), 4),
            "overfitting":             self.overfitting_flag,
            "small_sample":            self.small_sample_flag,
        }


class WalkForwardBacktester:
    """
    Runs a walk-forward backtest on one pair with a given TrendParams.
    Includes: spread+slippage+commission costs, partial close at 1:1 with
    trailing stop, news blackout filter, realistic WR reporting.
    """

    INITIAL_CAPITAL = 10_000.0
    MIN_TEST_TRADES = 30
    MIN_TOTAL_TRADES = 80
    COOLDOWN_BARS = 3

    def __init__(self, params: TrendParams):
        self.params = params
        self.engine = HighConfluenceTrend(params)

    # ─── ICT signal filter ────────────────────────────────────────────────────

    def _apply_ict_filter(
        self,
        signals: List[TradeSignal],
        full_df: pd.DataFrame,
        pair: str,
    ) -> List[TradeSignal]:
        """Filter XAUUSD signals by ICT advanced score. Other pairs pass through."""
        if not _ICT_AVAILABLE or _ICT_SCORER is None:
            return signals
        if pair not in ("XAUUSD", "GC=F"):
            return signals
        p = self.params
        min_score = getattr(p, "ict_min_score", 40)
        filtered = []
        for sig in signals:
            try:
                bar_pos = full_df.index.get_loc(sig.date)
                context_df = full_df.iloc[max(0, bar_pos - 100): bar_pos + 1]
                # ICT scorer uses bullish/bearish; signals use long/short
                ict_dir = "bullish" if sig.direction == "long" else "bearish"
                result = _ICT_SCORER.score(context_df, ict_dir, sig.entry)
                if result.get("total_score", result.get("total", 0)) >= min_score:
                    filtered.append(sig)
            except Exception:
                filtered.append(sig)  # on error keep the signal
        return filtered

    # ─── Core simulation ───────────────────────────────────────────────────────

    def _simulate(
        self,
        daily_df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame],
        signals: List[TradeSignal],
        pair: str,
        split_label: str,
    ) -> Tuple[List[WFTrade], List[float]]:
        """
        Bar-by-bar simulation with:
        - News blackout filter (skip entry on high-impact news days)
        - Realistic entry/exit with spread + slippage cost deduction
        - Partial close at 1:1 RR → SL to breakeven → trailing by 1 ATR
        - Commission deducted as cost_fraction per trade
        """
        trades: List[WFTrade] = []
        capital = self.INITIAL_CAPITAL
        equity  = [capital]

        sig_map: Dict = {}
        for s in signals:
            if s.date not in sig_map:
                sig_map[s.date] = s

        dates = daily_df.index
        n     = len(dates)
        has_atr = "atr" in daily_df.columns

        cooldown_until = 0
        in_trade       = False
        active: Optional[TradeSignal] = None
        active_entry_i = -1

        # Partial-close tracking per trade
        partial_closed      = False
        partial_close_pnl   = 0.0    # pnl_pct from the 50% closed at TP1
        trailing_sl         = 0.0
        effective_entry     = 0.0

        for i, bar_date in enumerate(dates):
            bar    = daily_df.iloc[i]
            high_i = float(bar["high"])
            low_i  = float(bar["low"])
            close_i = float(bar["close"])
            atr_i  = float(bar["atr"]) if has_atr and not pd.isna(bar.get("atr", float("nan"))) else 0.0

            # ── Manage open trade ────────────────────────────────────────────
            if in_trade and active is not None:
                hold_bars = i - active_entry_i
                risk_dist = abs(effective_entry - active.sl)

                # TP1 = breakeven level from effective_entry (1:1 from entry)
                if active.direction == "long":
                    tp1 = effective_entry + risk_dist
                    tp_full = active.tp
                else:
                    tp1 = effective_entry - risk_dist
                    tp_full = active.tp

                # ── Partial close at 1:1 ─────────────────────────────────────
                if not partial_closed:
                    reached_tp1 = (active.direction == "long"  and high_i >= tp1) or \
                                  (active.direction == "short" and low_i  <= tp1)
                    if reached_tp1:
                        partial_closed    = True
                        # 50% closed at 1:1 — capture pnl for that half
                        partial_close_pnl = 0.5 * 1.0   # 0.5 * 1RR win
                        # Move SL to breakeven
                        trailing_sl = effective_entry

                # ── Update trailing stop (after partial close) ───────────────
                if partial_closed and atr_i > 0:
                    if active.direction == "long":
                        new_trail = close_i - atr_i
                        trailing_sl = max(trailing_sl, new_trail)
                    else:
                        new_trail = close_i + atr_i
                        trailing_sl = min(trailing_sl, new_trail)

                current_sl = trailing_sl if partial_closed else active.sl

                sl_hit = (active.direction == "long"  and low_i  <= current_sl) or \
                         (active.direction == "short" and high_i >= current_sl)
                tp_hit = (active.direction == "long"  and high_i >= tp_full) or \
                         (active.direction == "short" and low_i  <= tp_full)

                if sl_hit or tp_hit:
                    if tp_hit and not sl_hit:
                        outcome = "win"
                        exit_p  = tp_full
                    else:
                        outcome = "loss" if not partial_closed else "win"  # partial = breakeven+
                        exit_p  = current_sl

                    # Skip ultra-short losses (data artefacts)
                    if outcome == "loss" and hold_bars < self.params.min_hold_bars:
                        in_trade = False
                        active   = None
                        partial_closed = False
                        cooldown_until = i + self.COOLDOWN_BARS
                        continue

                    # ── Compute raw PnL ───────────────────────────────────────
                    pnl_pips = (exit_p - effective_entry) if active.direction == "long" \
                               else (effective_entry - exit_p)
                    if risk_dist > 0:
                        second_half_rrr = abs(pnl_pips) / risk_dist
                        if pnl_pips < 0:
                            second_half_rrr = -second_half_rrr
                    else:
                        second_half_rrr = 0

                    if partial_closed:
                        # 50% closed at 1:1 + 50% at current exit
                        raw_rrr = partial_close_pnl + 0.5 * second_half_rrr
                    else:
                        raw_rrr = second_half_rrr

                    # Determine final raw outcome
                    if raw_rrr > 0:
                        outcome = "win"
                    else:
                        outcome = "loss"

                    raw_pnl_pct = raw_rrr   # in units of 1R

                    # ── Apply costs ───────────────────────────────────────────
                    cost_frac = get_round_trip_cost_fraction(pair, risk_dist, hold_bars)
                    realistic_pnl = raw_pnl_pct - cost_frac
                    realistic_outcome = "win" if realistic_pnl > 0 else \
                                        ("scratch" if realistic_pnl == 0 else "loss")

                    # Update capital (use realistic PnL)
                    risk_amount = capital * 0.01   # risk 1% per trade
                    capital_change = risk_amount * realistic_pnl
                    capital += capital_change
                    equity.append(capital)

                    rrr_ach = abs(raw_rrr)

                    trades.append(WFTrade(
                        pair=pair, direction=active.direction,
                        entry_date=active.date, exit_date=bar_date,
                        entry_price=active.entry, exit_price=exit_p,
                        sl=active.sl, tp=active.tp,
                        rrr_target=active.rrr, rrr_achieved=round(rrr_ach, 3),
                        outcome=outcome,
                        pnl_pct=round(raw_pnl_pct, 4),
                        confluence=active.confluence, pattern=active.pattern,
                        regime=active.regime, hold_bars=hold_bars, split=split_label,
                        cost_fraction=round(cost_frac, 4),
                        realistic_outcome=realistic_outcome,
                        realistic_pnl_pct=round(realistic_pnl, 4),
                    ))
                    in_trade = False
                    active   = None
                    partial_closed = False
                    if outcome == "loss":
                        cooldown_until = i + self.COOLDOWN_BARS
                continue

            # ── Look for new signal ──────────────────────────────────────────
            if in_trade or i < cooldown_until:
                continue

            if bar_date not in sig_map:
                continue

            # News blackout check
            if is_news_blackout(bar_date):
                continue

            sig = sig_map[bar_date]
            if sig.direction == "long":
                if sig.sl >= sig.entry or sig.tp <= sig.entry:
                    continue
            else:
                if sig.sl <= sig.entry or sig.tp >= sig.entry:
                    continue

            # Apply entry cost to effective entry
            eff_entry = adjust_entry_for_costs(pair, sig.entry, sig.direction)

            in_trade       = True
            active         = sig
            effective_entry = eff_entry
            active_entry_i = i
            partial_closed  = False
            trailing_sl     = sig.sl

        # Force-close any open trade at end of data
        if in_trade and active is not None:
            last_close = float(daily_df["close"].iloc[-1])
            hold_bars  = n - 1 - active_entry_i
            risk_dist  = abs(effective_entry - active.sl)
            pnl_pips   = (last_close - effective_entry) if active.direction == "long" \
                         else (effective_entry - last_close)
            raw_pnl_pct = (pnl_pips / risk_dist) if risk_dist > 0 else 0
            cost_frac   = get_round_trip_cost_fraction(pair, risk_dist, hold_bars)
            realistic_pnl = raw_pnl_pct - cost_frac
            outcome = "win" if raw_pnl_pct > 0 else "loss"
            realistic_outcome = "win" if realistic_pnl > 0 else "loss"

            trades.append(WFTrade(
                pair=pair, direction=active.direction,
                entry_date=active.date, exit_date=daily_df.index[-1],
                entry_price=active.entry, exit_price=last_close,
                sl=active.sl, tp=active.tp,
                rrr_target=active.rrr, rrr_achieved=round(abs(raw_pnl_pct), 3),
                outcome=outcome, pnl_pct=round(raw_pnl_pct, 4),
                confluence=active.confluence, pattern=active.pattern,
                regime=active.regime, hold_bars=hold_bars, split=split_label,
                cost_fraction=round(cost_frac, 4),
                realistic_outcome=realistic_outcome,
                realistic_pnl_pct=round(realistic_pnl, 4),
            ))

        return trades, equity

    # ─── Statistics ───────────────────────────────────────────────────────────

    def _stats(self, trades: List[WFTrade], capital: float) -> Dict:
        wins   = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        total  = len(trades)
        if total == 0:
            return dict(win_rate=0, win_rate_realistic=0, return_pct=0,
                        max_dd=0, sharpe=0, profit_factor=0,
                        avg_rrr=0, avg_rrr_realistic=0)

        win_rate = len(wins) / total
        ret_pct  = (capital - self.INITIAL_CAPITAL) / self.INITIAL_CAPITAL * 100

        gross_p = sum(abs(t.pnl_pct) for t in wins)
        gross_l = sum(abs(t.pnl_pct) for t in losses)
        pf = gross_p / gross_l if gross_l > 0 else (float("inf") if gross_p > 0 else 0)

        avg_rrr = float(np.mean([t.rrr_achieved for t in trades])) if trades else 0

        # Realistic WR (after spread/slip/commission)
        r_wins   = [t for t in trades if t.realistic_outcome == "win"]
        r_losses = [t for t in trades if t.realistic_outcome == "loss"]
        r_total  = len(r_wins) + len(r_losses)
        win_rate_realistic = len(r_wins) / r_total if r_total > 0 else win_rate

        # Realistic avg RRR: mean of positive realistic pnl among winners
        r_rrrs = [abs(t.realistic_pnl_pct) for t in r_wins if t.realistic_pnl_pct > 0]
        avg_rrr_realistic = float(np.mean(r_rrrs)) if r_rrrs else avg_rrr

        # Max drawdown from realistic equity curve
        rets = np.array([t.realistic_pnl_pct / 100 if t.realistic_pnl_pct != 0
                         else t.pnl_pct / 100 for t in trades])
        cum  = np.cumprod(1 + rets)
        roll_max = np.maximum.accumulate(cum)
        dd   = (roll_max - cum) / np.where(roll_max > 0, roll_max, 1)
        max_dd = float(np.max(dd)) * 100 if len(dd) > 0 else 0

        sharpe = (float(np.mean(rets)) / float(np.std(rets)) * np.sqrt(252)) \
                 if np.std(rets) > 1e-9 else 0

        return dict(win_rate=win_rate, win_rate_realistic=win_rate_realistic,
                    return_pct=ret_pct, max_dd=max_dd, sharpe=sharpe,
                    profit_factor=pf, avg_rrr=avg_rrr,
                    avg_rrr_realistic=avg_rrr_realistic)

    # ─── Public API ───────────────────────────────────────────────────────────

    def _compound_capital(self, trades: List[WFTrade]) -> float:
        """Compound capital from trades using realistic PnL (after costs)."""
        capital = self.INITIAL_CAPITAL
        for t in trades:
            pnl = t.realistic_pnl_pct if t.realistic_pnl_pct != 0 else t.pnl_pct
            capital += capital * 0.01 * pnl
            capital = max(capital, 1.0)
        return capital

    def run(
        self,
        daily_df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame] = None,
        pair: str = "XAUUSD",
        train_pct: float = 0.70,
        n_folds: int = 5,
    ) -> WFResult:
        """
        5-fold expanding window walk-forward (n_folds > 1) or single 70/30 split.
        Expanding window: each fold uses all prior data as training, fold slice as test.
        Combines test trades across all folds for robust out-of-sample statistics.
        """
        n = len(daily_df)
        # Minimum training bars: enough for EMA200 + signal warmup
        MIN_TRAIN = max(self.params.ema_long + 50, 250)

        if n_folds <= 1 or n < MIN_TRAIN + 50:
            return self._run_single_fold(daily_df, weekly_df, pair, train_pct)

        usable = n - MIN_TRAIN
        if usable < 50:
            return self._run_single_fold(daily_df, weekly_df, pair, train_pct)

        fold_size = max(20, usable // n_folds)

        logger.info(f"WF backtest: {pair} v{self.params.version} "
                    f"| {n_folds}-fold expanding | {n} bars | fold={fold_size}")

        # Precompute indicators once on the full dataset — slices inherit them
        full_df = self.engine._add_indicators(daily_df)

        all_test_trades: List[WFTrade] = []
        all_train_trades: List[WFTrade] = []

        for fold in range(n_folds):
            test_start = MIN_TRAIN + fold * fold_size
            test_end   = min(n, test_start + fold_size) if fold < n_folds - 1 else n
            if test_start >= n - 10:
                break

            # Slices of pre-computed df — _add_indicators is a no-op for these
            train_df = full_df.iloc[:test_start]
            test_df  = full_df.iloc[test_start:test_end]
            if len(train_df) < MIN_TRAIN or len(test_df) < 10:
                continue

            tr_sigs  = self.engine.generate_signals(train_df, weekly_df, pair)
            te_sigs  = self.engine.generate_signals(test_df,  weekly_df, pair)
            # Apply ICT quality filter for XAUUSD when enabled
            use_ict = getattr(self.params, "use_ict_filter", False)
            if use_ict:
                tr_sigs = self._apply_ict_filter(tr_sigs, train_df, pair)
                te_sigs = self._apply_ict_filter(te_sigs, test_df, pair)
            tr_t, _  = self._simulate(train_df, weekly_df, tr_sigs, pair, "train")
            te_t, _  = self._simulate(test_df,  weekly_df, te_sigs, pair, "test")
            all_test_trades.extend(te_t)
            all_train_trades.extend(tr_t)

        if not all_test_trades:
            return self._run_single_fold(daily_df, weekly_df, pair, train_pct)

        test_cap = self._compound_capital(all_test_trades)
        train_cap = self._compound_capital(all_train_trades) if all_train_trades else self.INITIAL_CAPITAL
        te_stats = self._stats(all_test_trades, test_cap)
        tr_stats = self._stats(all_train_trades, train_cap) if all_train_trades \
                   else dict(win_rate=0, return_pct=0, max_dd=0, sharpe=0, profit_factor=0, avg_rrr=0)

        overfit = (tr_stats["win_rate"] - te_stats["win_rate"] > 0.20
                   and tr_stats["win_rate"] > 0.70)
        small   = len(all_test_trades) < self.MIN_TEST_TRADES

        if small:
            logger.warning(f"{pair}: small test sample ({len(all_test_trades)} trades across {n_folds} folds)")
        if overfit:
            logger.warning(f"{pair}: possible overfitting "
                           f"(train WR {tr_stats['win_rate']:.1%} vs "
                           f"test WR {te_stats['win_rate']:.1%})")

        return WFResult(
            params_version=self.params.version,
            pair=pair,
            train_start=str(daily_df.index[0].date()),
            train_end=str(daily_df.index[MIN_TRAIN - 1].date()),
            test_start=str(daily_df.index[MIN_TRAIN].date()),
            test_end=str(daily_df.index[-1].date()),
            test_win_rate=te_stats["win_rate"],
            test_win_rate_realistic=te_stats["win_rate_realistic"],
            test_trades=len(all_test_trades),
            test_return_pct=te_stats["return_pct"],
            test_max_dd_pct=te_stats["max_dd"],
            test_sharpe=te_stats["sharpe"],
            test_profit_factor=te_stats["profit_factor"],
            test_avg_rrr=te_stats["avg_rrr"],
            test_avg_rrr_realistic=te_stats["avg_rrr_realistic"],
            train_win_rate=tr_stats["win_rate"],
            train_trades=len(all_train_trades),
            trades=all_train_trades + all_test_trades,
            overfitting_flag=overfit,
            small_sample_flag=small,
            strategy_name=self.params.strategy_name,
        )

    def _run_single_fold(
        self,
        daily_df: pd.DataFrame,
        weekly_df: Optional[pd.DataFrame] = None,
        pair: str = "XAUUSD",
        train_pct: float = 0.70,
    ) -> WFResult:
        """Original 70/30 single-split walk-forward (fallback)."""
        n = len(daily_df)
        split_i = int(n * train_pct)
        train_df = daily_df.iloc[:split_i]
        test_df  = daily_df.iloc[split_i:]

        if len(train_df) < 200 or len(test_df) < 50:
            logger.warning(f"{pair}: insufficient data for walk-forward "
                           f"(train={len(train_df)}, test={len(test_df)})")

        logger.info(f"WF backtest: {pair} v{self.params.version} "
                    f"| train {len(train_df)} bars | test {len(test_df)} bars")

        train_signals = self.engine.generate_signals(train_df, weekly_df, pair)
        test_signals  = self.engine.generate_signals(test_df,  weekly_df, pair)
        use_ict = getattr(self.params, "use_ict_filter", False)
        if use_ict:
            train_signals = self._apply_ict_filter(train_signals, train_df, pair)
            test_signals  = self._apply_ict_filter(test_signals,  test_df,  pair)

        train_trades, train_eq = self._simulate(
            train_df, weekly_df, train_signals, pair, "train")
        test_trades, test_eq   = self._simulate(
            test_df,  weekly_df, test_signals,  pair, "test")

        train_cap = train_eq[-1] if train_eq else self.INITIAL_CAPITAL
        test_cap  = test_eq[-1]  if test_eq  else self.INITIAL_CAPITAL
        tr_stats  = self._stats(train_trades, train_cap)
        te_stats  = self._stats(test_trades,  test_cap)

        overfit = (tr_stats["win_rate"] - te_stats["win_rate"] > 0.20
                   and tr_stats["win_rate"] > 0.70)
        small   = len(test_trades) < self.MIN_TEST_TRADES

        if small:
            logger.warning(f"{pair}: small test sample ({len(test_trades)} trades)")
        if overfit:
            logger.warning(f"{pair}: possible overfitting "
                           f"(train WR {tr_stats['win_rate']:.1%} vs "
                           f"test WR {te_stats['win_rate']:.1%})")

        return WFResult(
            params_version=self.params.version,
            pair=pair,
            train_start=str(train_df.index[0].date()) if len(train_df) > 0 else "",
            train_end=str(train_df.index[-1].date())   if len(train_df) > 0 else "",
            test_start=str(test_df.index[0].date())    if len(test_df)  > 0 else "",
            test_end=str(test_df.index[-1].date())     if len(test_df)  > 0 else "",
            test_win_rate=te_stats["win_rate"],
            test_win_rate_realistic=te_stats["win_rate_realistic"],
            test_trades=len(test_trades),
            test_return_pct=te_stats["return_pct"],
            test_max_dd_pct=te_stats["max_dd"],
            test_sharpe=te_stats["sharpe"],
            test_profit_factor=te_stats["profit_factor"],
            test_avg_rrr=te_stats["avg_rrr"],
            test_avg_rrr_realistic=te_stats["avg_rrr_realistic"],
            train_win_rate=tr_stats["win_rate"],
            train_trades=len(train_trades),
            trades=train_trades + test_trades,
            overfitting_flag=overfit,
            small_sample_flag=small,
            strategy_name=self.params.strategy_name,
        )

    def run_multi_pair(
        self,
        pair_data: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]],
        train_pct: float = 0.70,
    ) -> Dict[str, WFResult]:
        """Run walk-forward on multiple pairs and return per-pair results."""
        results = {}
        for pair, (daily, weekly) in pair_data.items():
            try:
                results[pair] = self.run(daily, weekly, pair, train_pct)
            except Exception as e:
                logger.error(f"WF failed for {pair}: {e}")
        return results

    # XAUUSD is primary — weighted 5x; metals 1.5x; majors 1x; minors 0.8x; DXY reference only
    PAIR_WEIGHTS = {
        "XAUUSD": 5.0, "GC=F": 5.0,
        "XAGUSD": 1.5, "SI=F": 1.5, "XPTUSD": 1.0,
        "BTCUSD": 1.5, "ETHUSD": 1.0,
        "GBPUSD": 1.0, "EURUSD": 1.0, "USDJPY": 1.0,
        "USDCHF": 0.8, "AUDUSD": 0.8, "NZDUSD": 0.8, "USDCAD": 0.8,
        "DXY": 0.0,   # reference only — not included in score
    }

    def aggregate(self, results: Dict[str, WFResult]) -> Dict:
        """Aggregate multi-pair results. XAUUSD weighted 3x as primary pair."""
        all_test_trades = []
        for r in results.values():
            all_test_trades.extend([t for t in r.trades if t.split == "test"])

        total = len(all_test_trades)
        if total == 0:
            return {"total_test_trades": 0, "aggregate_win_rate": 0.0,
                    "aggregate_win_rate_realistic": 0.0,
                    "aggregate_score": 0.0, "per_pair": {}}

        wins    = sum(1 for t in all_test_trades if t.outcome == "win")
        r_wins  = sum(1 for t in all_test_trades if t.realistic_outcome == "win")
        r_valid = sum(1 for t in all_test_trades if t.realistic_outcome in ("win", "loss"))
        gross_p = sum(abs(t.pnl_pct) for t in all_test_trades if t.outcome == "win")
        gross_l = sum(abs(t.pnl_pct) for t in all_test_trades if t.outcome == "loss")
        avg_rrr = float(np.mean([t.rrr_achieved for t in all_test_trades]))

        wr = wins / total
        wr_realistic = r_wins / r_valid if r_valid > 0 else wr
        pf = gross_p / gross_l if gross_l > 0 else float("inf")

        # Weighted score: DXY excluded (weight=0), XAUUSD 3x, metals 1.5x, forex 1x
        total_weight = sum(self.PAIR_WEIGHTS.get(p, 1.0) for p in results
                          if self.PAIR_WEIGHTS.get(p, 1.0) > 0)
        weighted_score = sum(
            r.composite_score() * self.PAIR_WEIGHTS.get(p, 1.0)
            for p, r in results.items()
            if self.PAIR_WEIGHTS.get(p, 1.0) > 0
        ) / total_weight if total_weight > 0 else 0.0

        # XAUUSD primary WR (try GC=F alias too)
        xau_result = results.get("XAUUSD") or results.get("GC=F")
        xauusd_wr = xau_result.test_win_rate if xau_result else wr
        xauusd_wr_realistic = xau_result.test_win_rate_realistic if xau_result else wr_realistic
        xauusd_trades = xau_result.test_trades if xau_result else 0

        return {
            "total_test_trades":             total,
            "aggregate_win_rate":            round(wr, 4),
            "aggregate_win_rate_realistic":  round(wr_realistic, 4),
            "aggregate_profit_factor":       round(pf, 3),
            "aggregate_avg_rrr":             round(avg_rrr, 3),
            "aggregate_score":               round(weighted_score, 4),
            "xauusd_win_rate":               round(xauusd_wr, 4),
            "xauusd_win_rate_realistic":     round(xauusd_wr_realistic, 4),
            "xauusd_test_trades":            xauusd_trades,
            "per_pair":                      {p: r.summary() for p, r in results.items()},
        }
