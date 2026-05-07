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
    """Runs walk-forward backtests on ICT strategy signals."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.loader = DataLoader()

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

        for i in range(lookback, n - 1):
            window_h4 = h4_df.iloc[:i]
            window_d1 = daily_df.iloc[:max(1, i // 4)]
            window_w1 = weekly_df.iloc[:max(1, i // 20)]
            current_time = h4_df.index[i]

            signal: TradeSignal = engine.generate_signal(
                h4_df=window_h4,
                daily_df=window_d1,
                weekly_df=window_w1,
                current_time=current_time.to_pydatetime() if hasattr(current_time, "to_pydatetime") else current_time,
                spread_pips=0.3,
                news_clear=True,
            )

            if not signal.valid:
                continue

            # Simulate trade outcome on next candle
            next_candle = h4_df.iloc[i + 1]
            entry = signal.entry_price + slippage * (1 if signal.direction == "long" else -1)
            sl = signal.stop_loss
            tp = signal.take_profit

            # Determine outcome
            if signal.direction == "long":
                if next_candle["low"] <= sl:
                    outcome = "loss"
                    exit_price = sl
                elif next_candle["high"] >= tp:
                    outcome = "win"
                    exit_price = tp
                else:
                    outcome = "open"
                    exit_price = next_candle["close"]
            else:
                if next_candle["high"] >= sl:
                    outcome = "loss"
                    exit_price = sl
                elif next_candle["low"] <= tp:
                    outcome = "win"
                    exit_price = tp
                else:
                    outcome = "open"
                    exit_price = next_candle["close"]

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
            trades=trades,
            overfitting_flag=overfitting,
            small_sample_flag=small_sample,
        )
