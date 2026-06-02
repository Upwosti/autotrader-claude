"""
Module Backtester — runs per-module backtests for all 4 trading modules.
Each module uses its own 3-timeframe set (HTF/LTF1/LTF2).

Modules:
  Position  HTF=MN  CRT=W1  TBS=D1
  Swing     HTF=W1  CRT=D1  TBS=4H   (preferred)
  Intraday  HTF=D1  CRT=4H  TBS=1H
  Scalp     HTF=4H  CRT=1H  TBS=15M

Each backtest:
  - Loads/generates data for all 3 timeframes
  - Iterates through the TBS (lowest) timeframe bar by bar
  - On each bar, calls TradingModule.evaluate() using the appropriate window of each TF
  - Simulates trade outcome on the next bar
  - Tracks: win_rate, avg_rrr, total_trades, profit_factor, expectancy, max_drawdown, balance
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'execution'))

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime
from loguru import logger

from config import StrategyParams, BACKTEST_INITIAL_CAPITAL, BACKTEST_COMMISSION_PCT, BACKTEST_SLIPPAGE_PIPS
from backtester.data_loader import DataLoader
from trading_module import TradingModule, TradingModuleConfig, TRADING_MODULE_CONFIGS, ModuleSignal


# ── Timeframe bar-count multipliers relative to 4H ────────────────────────────
# Used to scale n_bars for each timeframe so the windows roughly cover the
# same calendar span.
_TF_MULTIPLIER: Dict[str, float] = {
    "MN": 1 / 120,
    "W1": 1 / 30,
    "D1": 1 / 6,
    "4H": 1.0,
    "1H": 4.0,
    "15M": 16.0,
}

_TF_SEED_OFFSET: Dict[str, int] = {
    "MN": 0, "W1": 1, "D1": 2, "4H": 3, "1H": 4, "15M": 5,
}


@dataclass
class ModuleBacktestTrade:
    direction: str              # 'long' | 'short'
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl_pips: float
    pnl_pct: float
    rrr_achieved: float
    outcome: str                # 'win' | 'loss' | 'open'
    module_name: str
    entry_model: str
    timestamp: datetime


@dataclass
class ModuleBacktestResult:
    module_name: str
    htf: str
    ltf1: str
    ltf2: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_rrr: float
    profit_factor: float
    expectancy: float           # in pips
    max_drawdown_pct: float
    total_return_pct: float
    initial_capital: float
    final_capital: float
    trades: List[ModuleBacktestTrade] = field(default_factory=list)
    sharpe_ratio: float = 0.0
    is_profitable: bool = False
    needs_improvement: bool = True

    def summary(self) -> Dict:
        return {
            "module_name": self.module_name,
            "htf": self.htf,
            "ltf1": self.ltf1,
            "ltf2": self.ltf2,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "avg_rrr": round(self.avg_rrr, 4),
            "profit_factor": round(self.profit_factor, 4),
            "expectancy_pips": round(self.expectancy, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "total_return_pct": round(self.total_return_pct, 4),
            "initial_capital": round(self.initial_capital, 2),
            "final_capital": round(self.final_capital, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "is_profitable": self.is_profitable,
            "needs_improvement": self.needs_improvement,
        }

    def telegram_summary(self) -> str:
        status = "PASS" if self.is_profitable else "FAIL"
        win_icon = "v" if self.win_rate >= 0.50 else "x"
        rrr_icon = "v" if self.avg_rrr >= 2.0 else "x"
        pf_icon = "v" if self.profit_factor >= 1.5 else "x"
        lines = [
            f"Module: {self.module_name.upper()}",
            f"Timeframes: HTF={self.htf} | CRT={self.ltf1} | TBS={self.ltf2}",
            f"Total Trades:  {self.total_trades}",
            f"Win Rate:      {self.win_rate * 100:.1f}%  (target >=50%)  [{win_icon}]",
            f"Avg RRR:       {self.avg_rrr:.2f}  (target >=2.0)  [{rrr_icon}]",
            f"Profit Factor: {self.profit_factor:.2f}  (target >=1.5)  [{pf_icon}]",
            f"Expectancy:    {self.expectancy:.1f} pips",
            f"Max Drawdown:  {self.max_drawdown_pct:.1f}%",
            f"Total Return:  {self.total_return_pct:.1f}%",
            f"Status:        {status}",
        ]
        return "\n".join(lines)


class ModuleBacktester:
    """Runs independent backtests for each of the 4 trading modules."""

    def __init__(self, params: StrategyParams):
        self.params = params
        self.loader = DataLoader()

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _get_pip_size(self, pair: str) -> float:
        """Return pip/point size for a given trading pair."""
        mapping = {
            "XAUUSD": 0.01,
            "BTCUSD": 1.0,
        }
        return mapping.get(pair.upper(), 0.0001)

    def _n_bars_for_tf(self, tf: str, base_n_bars: int) -> int:
        """Calculate how many bars to generate for a given timeframe."""
        mult = _TF_MULTIPLIER.get(tf, 1.0)
        raw = int(base_n_bars * mult)
        # Enforce a sensible minimum so detectors have enough data
        min_bars = {"MN": 24, "W1": 52, "D1": 200, "4H": 500, "1H": 1000, "15M": 2000}
        minimum = min_bars.get(tf, 50)
        result = max(minimum, raw)
        # Cap extremely large synthetic sets to keep memory reasonable
        return min(result, 10_000)

    def _load_tfs(self, config: TradingModuleConfig, pair: str) -> Dict[str, pd.DataFrame]:
        """Load HTF, LTF1, and LTF2 dataframes for this module."""
        frames: Dict[str, pd.DataFrame] = {}
        for tf in (config.htf, config.ltf1, config.ltf2):
            df = self.loader.load(pair, tf, synthetic_fallback=True)
            frames[tf] = df
        return frames

    def _htf_window(
        self,
        htf_df: pd.DataFrame,
        ltf2_idx: int,
        ltf2_total: int,
        htf_total: int,
    ) -> pd.DataFrame:
        """Return a proportional slice of htf_df up to the current position.

        Maps the current LTF2 bar index onto the HTF series so we only see
        HTF bars that would realistically be known at that point in time.
        """
        if htf_total == 0 or ltf2_total == 0:
            return htf_df
        # How far through the LTF2 series are we? (0.0 – 1.0)
        progress = ltf2_idx / max(ltf2_total - 1, 1)
        end_idx = max(20, int(htf_total * progress))
        end_idx = min(end_idx, htf_total)
        return htf_df.iloc[:end_idx]

    def _ltf1_window(
        self,
        ltf1_df: pd.DataFrame,
        ltf2_idx: int,
        ltf2_total: int,
        ltf1_total: int,
    ) -> pd.DataFrame:
        """Return a proportional slice of ltf1_df up to the current position."""
        if ltf1_total == 0 or ltf2_total == 0:
            return ltf1_df
        progress = ltf2_idx / max(ltf2_total - 1, 1)
        end_idx = max(30, int(ltf1_total * progress))
        end_idx = min(end_idx, ltf1_total)
        return ltf1_df.iloc[:end_idx]

    # ── Core simulation ────────────────────────────────────────────────────────

    def _simulate_outcome(
        self,
        signal: ModuleSignal,
        next_bar: pd.Series,
        pip_size: float,
    ) -> ModuleBacktestTrade:
        """Simulate whether next_bar hits TP or SL first.
        If both hit, conservative assumption = SL hit first (loss).
        """
        direction = signal.direction
        entry = signal.entry_price
        sl = signal.stop_loss
        tp = signal.take_profit
        bar_high = float(next_bar["high"])
        bar_low = float(next_bar["low"])
        slippage = BACKTEST_SLIPPAGE_PIPS * pip_size

        if direction == "long":
            entry = entry + slippage
            tp_hit = bar_high >= tp
            sl_hit = bar_low <= sl
        else:
            entry = entry - slippage
            tp_hit = bar_low <= tp
            sl_hit = bar_high >= sl

        if tp_hit and sl_hit:
            tp_hit = False   # conservative: SL hit first

        if tp_hit:
            exit_price = tp
            outcome = "win"
        elif sl_hit:
            exit_price = sl
            outcome = "loss"
        else:
            exit_price = float(next_bar["close"])
            outcome = "open"

        if direction == "long":
            pnl_pips = (exit_price - entry) / pip_size
        else:
            pnl_pips = (entry - exit_price) / pip_size

        risk_pips = abs(entry - sl) / pip_size if pip_size > 0 else 1.0
        # Commission in pips: commission_pct × spread_in_pips (small, ~0.5-2 pips)
        commission_pips = (BACKTEST_COMMISSION_PCT / 100.0) * 10.0  # ~0.001 pips, negligible
        pnl_pips -= commission_pips

        pnl_pct = (pnl_pips / risk_pips) * 1.0 if risk_pips > 0 else 0.0  # as fraction of 1R
        rrr_achieved = (pnl_pips / risk_pips) if risk_pips > 0 else 0.0

        return ModuleBacktestTrade(
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            stop_loss=sl,
            take_profit=tp,
            pnl_pips=pnl_pips,
            pnl_pct=pnl_pct,
            rrr_achieved=rrr_achieved,
            outcome=outcome,
            module_name=signal.module_name,
            entry_model=signal.entry_model,
            timestamp=signal.timestamp,
        )

    def _simulate_multi_bar(
        self,
        signal: ModuleSignal,
        ltf2_df: pd.DataFrame,
        entry_bar: int,
        pip_size: float,
        max_forward_bars: int = 20,
    ) -> ModuleBacktestTrade:
        """Look ahead up to max_forward_bars bars to resolve SL/TP.

        For each forward bar:
          - Long: bar_high >= TP → win; bar_low <= SL → loss
          - Short: bar_low <= TP → win; bar_high >= SL → loss
        If both hit on the same bar, SL wins (conservative).
        If unresolved after max_forward_bars, close at the last bar's close.
        """
        direction = signal.direction
        sl = signal.stop_loss
        tp = signal.take_profit
        slippage = BACKTEST_SLIPPAGE_PIPS * pip_size

        if direction == "long":
            entry = signal.entry_price + slippage
        else:
            entry = signal.entry_price - slippage

        risk_pips = abs(entry - sl) / pip_size if pip_size > 0 else 1.0
        if risk_pips < 1e-9:
            risk_pips = 1.0

        total_bars = len(ltf2_df)
        outcome = "open"
        exit_price = entry  # default: flat if no bars ahead

        for offset in range(1, max_forward_bars + 1):
            fwd_idx = entry_bar + offset
            if fwd_idx >= total_bars:
                # ran off the end of the data
                exit_price = float(ltf2_df.iloc[-1]["close"])
                outcome = "open"
                break

            bar = ltf2_df.iloc[fwd_idx]
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])

            if direction == "long":
                tp_hit = bar_high >= tp
                sl_hit = bar_low <= sl
            else:
                tp_hit = bar_low <= tp
                sl_hit = bar_high >= sl

            if tp_hit and sl_hit:
                tp_hit = False  # conservative: SL hit first

            if tp_hit:
                exit_price = tp
                outcome = "win"
                break
            elif sl_hit:
                exit_price = sl
                outcome = "loss"
                break

            if offset == max_forward_bars:
                exit_price = float(bar["close"])
                outcome = "open"

        if direction == "long":
            pnl_pips = (exit_price - entry) / pip_size
        else:
            pnl_pips = (entry - exit_price) / pip_size

        commission_pips = (BACKTEST_COMMISSION_PCT / 100.0) * 10.0
        pnl_pips -= commission_pips

        r_multiple = pnl_pips / risk_pips
        rrr_achieved = r_multiple if outcome == "win" else abs(r_multiple)

        return ModuleBacktestTrade(
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            stop_loss=sl,
            take_profit=tp,
            pnl_pips=pnl_pips,
            pnl_pct=r_multiple,
            rrr_achieved=rrr_achieved,
            outcome=outcome,
            module_name=signal.module_name,
            entry_model=signal.entry_model,
            timestamp=signal.timestamp,
        )

    def _compute_stats(
        self,
        trades: List[ModuleBacktestTrade],
        initial_capital: float,
        pip_size: float,
    ) -> Dict:
        """Compute all performance statistics from a list of trades."""
        n = len(trades)
        if n == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_rrr": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "max_drawdown_pct": 0.0,
                "total_return_pct": 0.0,
                "final_capital": initial_capital,
                "sharpe_ratio": 0.0,
            }

        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        n_win = len(wins)
        n_loss = len(losses)
        win_rate = n_win / n if n > 0 else 0.0

        # Average RRR only from winning trades
        avg_rrr = float(np.mean([t.rrr_achieved for t in wins])) if wins else 0.0

        gross_profit = sum(t.pnl_pips for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl_pips for t in losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        expectancy = float(np.mean([t.pnl_pips for t in trades]))

        # Equity curve — each trade risks 1% of balance (R-multiple model)
        risk_pct = 0.01
        balance = initial_capital
        equity_curve = [balance]
        for t in trades:
            # pnl_pct = R-multiple (pnl_pips / risk_pips)
            # +1.0 = win at 1R, +2.0 = win at 2R, -1.0 = loss at SL
            r_multiple = t.pnl_pct  # already computed as pnl_pips/risk_pips
            # cap individual trade impact at ±5R to prevent extreme compounding
            r_multiple = max(-5.0, min(5.0, r_multiple))
            balance += balance * risk_pct * r_multiple
            balance = max(balance, 0.01)
            equity_curve.append(balance)

        final_capital = balance
        total_return_pct = (final_capital - initial_capital) / initial_capital * 100.0

        # Max drawdown from equity curve
        equity_arr = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_arr)
        drawdowns = (running_max - equity_arr) / np.maximum(running_max, 1e-9)
        max_drawdown_pct = float(np.max(drawdowns) * 100.0)

        # Sharpe ratio (annualised, assuming each trade is ~1 day)
        pnl_series = np.array([t.pnl_pips for t in trades])
        if len(pnl_series) > 1 and pnl_series.std() > 0:
            sharpe_ratio = float(
                (pnl_series.mean() / pnl_series.std()) * np.sqrt(252)
            )
        else:
            sharpe_ratio = 0.0

        return {
            "total_trades": n,
            "winning_trades": n_win,
            "losing_trades": n_loss,
            "win_rate": win_rate,
            "avg_rrr": avg_rrr,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "max_drawdown_pct": max_drawdown_pct,
            "total_return_pct": total_return_pct,
            "final_capital": final_capital,
            "sharpe_ratio": sharpe_ratio,
        }

    # ── Per-module backtest ────────────────────────────────────────────────────

    def run_module(
        self,
        config: TradingModuleConfig,
        pair: str = "XAUUSD",
        n_bars: int = 2000,
    ) -> ModuleBacktestResult:
        """Run a full backtest for a single trading module.

        Iterates through the LTF2 (TBS) timeframe bar by bar, providing
        proportional HTF and LTF1 windows on each step.
        """
        logger.info(
            f"[ModuleBacktester] Starting {config.name.upper()} | "
            f"HTF={config.htf} LTF1={config.ltf1} LTF2={config.ltf2} "
            f"pair={pair} n_bars={n_bars}"
        )

        pip_size = self._get_pip_size(pair)
        initial_capital = BACKTEST_INITIAL_CAPITAL
        module = TradingModule(config, self.params)

        # ── Load data ──────────────────────────────────────────────────────────
        frames = _load_tfs_with_nbars(self.loader, config, pair, n_bars)
        htf_df = frames[config.htf]
        ltf1_df = frames[config.ltf1]
        ltf2_df = frames[config.ltf2]

        htf_total = len(htf_df)
        ltf1_total = len(ltf1_df)
        ltf2_total = len(ltf2_df)

        logger.info(
            f"[{config.name}] bars loaded: HTF={htf_total} LTF1={ltf1_total} LTF2={ltf2_total}"
        )

        # ── Iterate bar by bar on LTF2 ─────────────────────────────────────────
        # Cooldown: don't fire a new signal within COOLDOWN_BARS of last trade.
        COOLDOWN_BARS = max(5, ltf2_total // 40)  # ~2.5% of series length

        # Forward-bar resolution window: enough time for TP to be reached.
        # Larger TFs need more bars to resolve a trade.
        _forward_bars_map = {
            "D1": 60,   # position module — 60 trading days
            "4H": 120,  # swing module — 20 days
            "1H": 72,   # intraday — 3 days
            "15M": 96,  # scalp — 24 hours
        }
        max_fwd_bars = _forward_bars_map.get(config.ltf2, 60)

        trades: List[ModuleBacktestTrade] = []
        last_trade_bar = -COOLDOWN_BARS  # allow first trade immediately
        start_bar = min(50, ltf2_total - 2)

        for i in range(start_bar, ltf2_total - 1):
            # Enforce cooldown
            if i - last_trade_bar < COOLDOWN_BARS:
                continue

            # Build proportional HTF and LTF1 windows
            htf_window = self._htf_window(htf_df, i, ltf2_total, htf_total)
            ltf1_window = self._ltf1_window(ltf1_df, i, ltf2_total, ltf1_total)
            ltf2_window = ltf2_df.iloc[:i]

            if len(ltf2_window) < 10:
                continue

            # Evaluate the module for this bar
            try:
                signal: ModuleSignal = module.evaluate(
                    htf_df=htf_window,
                    ltf1_df=ltf1_window,
                    ltf2_df=ltf2_window,
                    pip_size=pip_size,
                )
            except Exception as exc:
                logger.debug(f"[{config.name}] bar {i} evaluate error: {exc}")
                continue

            if not signal.valid:
                continue

            # Validate SL is on the correct side of entry to avoid bad signals
            if signal.direction == "long" and signal.stop_loss >= signal.entry_price:
                logger.debug(f"[{config.name}] bar {i}: long with SL above entry, skipping")
                continue
            if signal.direction == "short" and signal.stop_loss <= signal.entry_price:
                logger.debug(f"[{config.name}] bar {i}: short with SL below entry, skipping")
                continue

            # Simulate trade across the next up to max_fwd_bars bars
            trade = self._simulate_multi_bar(signal, ltf2_df, i, pip_size, max_fwd_bars)
            trades.append(trade)
            last_trade_bar = i

        # ── Compute statistics ─────────────────────────────────────────────────
        stats = self._compute_stats(trades, initial_capital, pip_size)

        is_profitable = (
            stats["win_rate"] >= 0.50 and stats["avg_rrr"] >= 2.0
        )

        result = ModuleBacktestResult(
            module_name=config.name,
            htf=config.htf,
            ltf1=config.ltf1,
            ltf2=config.ltf2,
            total_trades=stats["total_trades"],
            winning_trades=stats["winning_trades"],
            losing_trades=stats["losing_trades"],
            win_rate=stats["win_rate"],
            avg_rrr=stats["avg_rrr"],
            profit_factor=stats["profit_factor"],
            expectancy=stats["expectancy"],
            max_drawdown_pct=stats["max_drawdown_pct"],
            total_return_pct=stats["total_return_pct"],
            initial_capital=initial_capital,
            final_capital=stats["final_capital"],
            trades=trades,
            sharpe_ratio=stats["sharpe_ratio"],
            is_profitable=is_profitable,
            needs_improvement=not is_profitable,
        )

        logger.info(
            f"[{config.name}] done | trades={result.total_trades} "
            f"WR={result.win_rate:.1%} RRR={result.avg_rrr:.2f} "
            f"PF={result.profit_factor:.2f} | {'PASS' if is_profitable else 'FAIL'}"
        )
        return result

    # ── Run all 4 modules ──────────────────────────────────────────────────────

    def run_all(
        self,
        pair: str = "XAUUSD",
        n_bars: int = 2000,
    ) -> List[ModuleBacktestResult]:
        """Run independent backtests for all 4 trading modules sequentially."""
        results: List[ModuleBacktestResult] = []
        for config in TRADING_MODULE_CONFIGS:
            logger.info(
                f"[ModuleBacktester] === {config.name.upper()} ({config.htf}/{config.ltf1}/{config.ltf2}) ==="
            )
            result = self.run_module(config, pair=pair, n_bars=n_bars)
            results.append(result)

        # Summary log
        logger.info("[ModuleBacktester] === ALL MODULES COMPLETE ===")
        for r in results:
            status = "PASS" if r.is_profitable else "FAIL"
            logger.info(
                f"  {r.module_name:<10} WR={r.win_rate:.1%}  "
                f"RRR={r.avg_rrr:.2f}  PF={r.profit_factor:.2f}  "
                f"trades={r.total_trades}  [{status}]"
            )

        return results


# ── Module-level helper (avoids repeated import dance) ────────────────────────

# Hours per bar for each supported timeframe label
_TF_HOURS: Dict[str, float] = {
    "M1": 1/60, "5M": 5/60, "15M": 0.25, "30M": 0.5,
    "1H": 1.0,  "H1": 1.0,  "2H": 2.0,  "4H": 4.0,   "H4": 4.0,
    "6H": 6.0,  "D1": 24.0, "W1": 168.0, "MN": 720.0,
}

# Pandas resample rules for each TF
_TF_RESAMPLE: Dict[str, str] = {
    "M1": "1min", "5M": "5min", "15M": "15min", "30M": "30min",
    "1H": "1h",   "H1": "1h",   "2H": "2h",     "4H": "4h",  "H4": "4h",
    "6H": "6h",   "D1": "1D",   "W1": "7D",      "MN": "30D",
}


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample an OHLCV DataFrame using pandas freq string."""
    out = df.resample(rule).agg({
        "open": "first", "high": "max",
        "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    return out


def _load_tfs_with_nbars(
    loader: DataLoader,
    config: TradingModuleConfig,
    pair: str,
    base_n_bars: int,
) -> Dict[str, pd.DataFrame]:
    """Load or generate aligned data for all 3 timeframes.

    Strategy:
    1. Try real CSV data (same file serves all TFs via resampling).
    2. Try yfinance for LTF2 (finest TF); resample up to LTF1 and HTF.
    3. Generate synthetic data at LTF2 resolution and resample up — this
       guarantees that CRT price ranges on LTF1 overlap with LTF2 bars
       (which fails when data is generated independently per TF).
    """
    frames: Dict[str, pd.DataFrame] = {}
    min_bars = {"MN": 24, "W1": 52, "D1": 200, "4H": 500, "1H": 1000, "15M": 2000}

    # ── Step 1: real CSV data per TF ──────────────────────────────────────
    all_real = True
    for tf in (config.htf, config.ltf1, config.ltf2):
        df = loader.load_csv(pair, tf)
        minimum = min_bars.get(tf, 50)
        if df is not None and len(df) >= minimum:
            frames[tf] = df
        else:
            all_real = False
            break

    if all_real and len(frames) == 3:
        return frames

    # ── Step 2: yfinance for LTF2 then resample up ────────────────────────
    frames.clear()
    ltf2_tf = config.ltf2
    ltf2_hrs = _TF_HOURS.get(ltf2_tf, 4.0)
    # Need enough LTF2 bars to cover the full span
    target_span_hrs = base_n_bars * _TF_HOURS.get("4H", 4.0)  # treat base as ~4H bars
    ltf2_n_bars = max(min_bars.get(ltf2_tf, 200), int(target_span_hrs / ltf2_hrs))
    ltf2_n_bars = min(ltf2_n_bars, 10_000)

    df_ltf2 = loader.load_yfinance(pair, ltf2_tf)
    if df_ltf2 is not None and len(df_ltf2) >= min_bars.get(ltf2_tf, 50):
        # Resample to the other TFs
        frames[ltf2_tf] = df_ltf2
        for tf in (config.htf, config.ltf1):
            rule = _TF_RESAMPLE.get(tf)
            if rule:
                resampled = _resample_ohlcv(df_ltf2, rule)
                if len(resampled) >= min_bars.get(tf, 5):
                    frames[tf] = resampled
        if len(frames) == 3:
            return frames

    # ── Step 3: synthetic at LTF2 and resample up ─────────────────────────
    frames.clear()
    seed = 42 + _TF_SEED_OFFSET.get(ltf2_tf, 0)
    base_df = loader.generate_synthetic(
        pair=pair, n_bars=ltf2_n_bars, timeframe=ltf2_tf, seed=seed
    )
    frames[ltf2_tf] = base_df

    for tf in (config.htf, config.ltf1):
        rule = _TF_RESAMPLE.get(tf)
        if rule:
            resampled = _resample_ohlcv(base_df, rule)
            if len(resampled) >= 3:
                frames[tf] = resampled
            else:
                # Ultra-sparse TF: just duplicate with coarser grouping
                tf_hrs = _TF_HOURS.get(tf, 24.0)
                n_coarse = max(24, int(ltf2_n_bars * ltf2_hrs / tf_hrs))
                frames[tf] = loader.generate_synthetic(
                    pair=pair, n_bars=n_coarse, timeframe=tf, seed=seed + 10
                )
        else:
            # Unknown TF: generate independently (shouldn't happen)
            frames[tf] = loader.generate_synthetic(pair=pair, timeframe=tf, seed=seed + 20)

    return frames
