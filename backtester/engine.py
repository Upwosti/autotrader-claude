"""
Backtesting engine — bar-by-bar walk-forward simulation.
Uses real OHLCV data (yfinance cache) and proper ICT signal generation.
Key improvements:
  - Position tracking (no concurrent trades)
  - Proper risk-based position sizing
  - Multi-candle trade resolution (holds until SL/TP hit)
  - ATR-based SL when no sweep level available
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
    outcome: str          # 'win' | 'loss'
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
    trades: List[BacktestTrade] = field(default_factory=list)
    overfitting_flag: bool = False
    small_sample_flag: bool = False

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
            "overfitting_flag": self.overfitting_flag,
            "small_sample_flag": self.small_sample_flag,
        }


class BacktestEngine:
    """Runs walk-forward backtests on ICT strategy signals using real market data."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.loader = DataLoader()

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR as fallback SL distance."""
        if len(df) < period:
            return (df["high"] - df["low"]).mean()
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        tr = np.maximum(
            highs[-period:] - lows[-period:],
            np.maximum(
                np.abs(highs[-period:] - closes[-period - 1:-1]),
                np.abs(lows[-period:] - closes[-period - 1:-1]),
            )
        )
        return float(np.mean(tr))

    def run(
        self,
        pair: str = "XAUUSD",
        timeframe: str = "H4",
        initial_capital: float = BACKTEST_INITIAL_CAPITAL,
        use_synthetic: bool = False,
    ) -> BacktestResult:
        """Execute a full backtest for the given pair on real data."""
        logger.info(f"Starting backtest: {pair} {timeframe} v{self.params.version}")

        # Load real data (synthetic fallback only if use_synthetic=True)
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
        lookback = max(self.params.liquidity_sweep_lookback,
                       self.params.bos_lookback) + 10

        # Position state
        in_trade = False
        active_direction = None
        active_entry = 0.0
        active_sl = 0.0
        active_tp = 0.0
        active_entry_time = None
        active_session = ""
        active_confidence = 0.0
        active_risk_pct = self.params.risk_pct
        # Cooldown: after a SL hit, skip 6 H4 bars (~1 trading day) before next entry
        cooldown_until = 0

        for i in range(lookback, n):
            bar = h4_df.iloc[i]
            bar_time = h4_df.index[i]

            # ─── Manage open trade ───────────────────────────────────────
            if in_trade:
                high_i = float(bar["high"])
                low_i = float(bar["low"])
                close_i = float(bar["close"])

                hit_sl = (active_direction == "long" and low_i <= active_sl) or \
                         (active_direction == "short" and high_i >= active_sl)
                hit_tp = (active_direction == "long" and high_i >= active_tp) or \
                         (active_direction == "short" and low_i <= active_tp)

                if hit_sl or hit_tp:
                    if hit_tp and not hit_sl:
                        outcome = "win"
                        exit_price = active_tp
                    elif hit_sl and not hit_tp:
                        outcome = "loss"
                        exit_price = active_sl
                    else:
                        # Both hit same bar — conservative: assume SL hit first
                        outcome = "loss"
                        exit_price = active_sl

                    risk_amount = capital * (active_risk_pct / 100)
                    risk_pips = abs(active_entry - active_sl) / pip_size
                    pnl_pips = ((exit_price - active_entry) / pip_size
                                if active_direction == "long"
                                else (active_entry - exit_price) / pip_size)
                    pnl_pct_trade = (pnl_pips / risk_pips) * active_risk_pct if risk_pips > 0 else 0
                    pnl_amount = capital * (pnl_pct_trade / 100)
                    capital += pnl_amount - (capital * commission * 2)

                    peak = max(peak, capital)
                    dd = (peak - capital) / peak * 100 if capital < peak else 0.0
                    max_dd = max(max_dd, dd)
                    equity_curve.append(capital)

                    rrr = abs(pnl_pips / risk_pips) if risk_pips > 0 else 0
                    trades.append(BacktestTrade(
                        pair=pair,
                        direction=active_direction,
                        entry_time=active_entry_time,
                        exit_time=bar_time,
                        entry_price=active_entry,
                        exit_price=exit_price,
                        stop_loss=active_sl,
                        take_profit=active_tp,
                        risk_pct=active_risk_pct,
                        rrr_achieved=round(rrr, 3),
                        pnl_pips=round(pnl_pips, 2),
                        pnl_pct=round(pnl_pct_trade, 4),
                        outcome=outcome,
                        session=active_session,
                        confidence_score=active_confidence,
                    ))
                    in_trade = False
                    if outcome == "loss":
                        cooldown_until = i + 6   # 6 H4 bars (~1 trading day) cooldown
                continue  # Never enter a new trade while managing an open one

            # ─── Look for new signal ──────────────────────────────────────
            if i >= n - 1:
                continue  # No next candle to enter on
            if i < cooldown_until:
                continue  # In cooldown after a SL hit

            window_h4 = h4_df.iloc[:i]
            # Date-based slicing: D1/W1 cover different time ranges than H4
            # Normalize all indices to tz-naive date for comparison
            bt_date = bar_time.date() if hasattr(bar_time, "date") else bar_time
            d1_dates = daily_df.index.normalize().date if hasattr(daily_df.index, "normalize") else [d.date() for d in daily_df.index]
            w1_dates = weekly_df.index.normalize().date if hasattr(weekly_df.index, "normalize") else [d.date() for d in weekly_df.index]
            # Use positional slice up to the bar matching this date
            d1_mask = [d <= bt_date for d in d1_dates]
            w1_mask = [d <= bt_date for d in w1_dates]
            window_d1 = daily_df.iloc[[j for j, m in enumerate(d1_mask) if m]]
            window_w1 = weekly_df.iloc[[j for j, m in enumerate(w1_mask) if m]]
            # Ensure minimum bars for bias calculation
            if len(window_d1) < 5:
                window_d1 = daily_df.iloc[:max(5, len(daily_df) // 4)]
            if len(window_w1) < 3:
                window_w1 = weekly_df.iloc[:max(3, len(weekly_df) // 4)]
            current_time = bar_time.to_pydatetime() if hasattr(bar_time, "to_pydatetime") else bar_time

            signal: TradeSignal = engine.generate_signal(
                h4_df=window_h4,
                daily_df=window_d1,
                weekly_df=window_w1,
                current_time=current_time,
                spread_pips=0.3,
                news_clear=True,
            )

            if not signal.valid:
                continue

            # Enter on open of next candle + slippage
            next_bar = h4_df.iloc[i + 1] if i + 1 < n else None
            if next_bar is None:
                continue

            entry = float(next_bar["open"]) + slippage * (1 if signal.direction == "long" else -1)
            sl = signal.stop_loss

            # Recalculate TP from actual entry (signal TP was based on FVG midpoint entry)
            actual_risk = abs(entry - sl)
            min_sl_distance = pip_size * 20   # min 20 pips SL distance
            if actual_risk < min_sl_distance:  # SL too close — likely bad setup
                continue
            if signal.direction == "long":
                tp = entry + actual_risk * self.params.min_rrr
            else:
                tp = entry - actual_risk * self.params.min_rrr

            # Validate SL/TP make sense
            if signal.direction == "long":
                if sl >= entry or tp <= entry:
                    continue
            else:
                if sl <= entry or tp >= entry:
                    continue

            in_trade = True
            active_direction = signal.direction
            active_entry = entry
            active_sl = sl
            active_tp = tp
            active_entry_time = h4_df.index[i + 1]
            active_session = signal.session
            active_confidence = signal.confidence.total
            active_risk_pct = self.params.risk_pct

        # ─── Force-close any still-open trade at last bar ─────────────────
        if in_trade:
            last_close = float(h4_df["close"].iloc[-1])
            risk_amount = capital * (active_risk_pct / 100)
            risk_pips = abs(active_entry - active_sl) / pip_size
            pnl_pips = ((last_close - active_entry) / pip_size
                        if active_direction == "long"
                        else (active_entry - last_close) / pip_size)
            pnl_pct_trade = (pnl_pips / risk_pips) * active_risk_pct if risk_pips > 0 else 0
            outcome = "win" if pnl_pips > 0 else "loss"
            capital += capital * (pnl_pct_trade / 100)
            rrr = abs(pnl_pips / risk_pips) if risk_pips > 0 else 0
            trades.append(BacktestTrade(
                pair=pair, direction=active_direction,
                entry_time=active_entry_time,
                exit_time=h4_df.index[-1],
                entry_price=active_entry, exit_price=last_close,
                stop_loss=active_sl, take_profit=active_tp,
                risk_pct=active_risk_pct, rrr_achieved=round(rrr, 3),
                pnl_pips=round(pnl_pips, 2),
                pnl_pct=round(pnl_pct_trade, 4),
                outcome=outcome, session=active_session,
                confidence_score=active_confidence,
            ))

        # ─── Statistics ───────────────────────────────────────────────────
        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        total = len(trades)

        win_rate = len(wins) / total if total > 0 else 0.0
        avg_rrr = float(np.mean([t.rrr_achieved for t in trades if t.rrr_achieved is not None])) if trades else 0.0
        total_return = (capital - initial_capital) / initial_capital * 100

        gross_profit = sum(abs(t.pnl_pips or 0) for t in wins)
        gross_loss = sum(abs(t.pnl_pips or 0) for t in losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        equity_arr = np.array(equity_curve)
        if len(equity_arr) > 1:
            rets = np.diff(equity_arr) / np.where(equity_arr[:-1] > 0, equity_arr[:-1], 1)
            sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(252 * 6)) if np.std(rets) > 1e-9 else 0.0
        else:
            sharpe = 0.0

        small_sample = total < 30
        overfitting = win_rate > 0.75 and total < 50

        if small_sample:
            logger.warning(f"Small sample: {total} trades — results may not be reliable")
        if overfitting:
            logger.warning(f"Overfitting warning: {win_rate:.1%} win rate on {total} trades")

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
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_rrr=avg_rrr,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            trades=trades,
            overfitting_flag=overfitting,
            small_sample_flag=small_sample,
        )
