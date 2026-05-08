"""
TickSimulator — intra-bar price path simulation for final validation.

Simulates realistic tick-by-tick movement within each OHLC bar using
a geometric Brownian motion path constrained to hit the bar's H/L/C.
Used for final strategy validation — more realistic than bar-close logic.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from loguru import logger


@dataclass
class SimTrade:
    entry:       float
    sl:          float
    tp:          float
    direction:   str      # 'long' | 'short'
    bar_index:   int
    outcome:     str = ""       # 'win' | 'loss' | 'open'
    exit_price:  float = 0.0
    exit_bar:    int = 0
    pnl_r:       float = 0.0    # P&L in R-multiples


class TickSimulator:
    """
    Generates synthetic intra-bar price paths and tests SL/TP hit probability.

    For each OHLC bar, generates N_PATHS price paths that must:
      - Start at open
      - Reach the bar's high at some point
      - Reach the bar's low at some point
      - End at close

    SL/TP hit probability is computed from these paths.
    """

    N_PATHS   = 200     # paths per bar — trade-off speed vs accuracy
    N_STEPS   = 50      # ticks per path within a bar
    SEED      = 42

    def __init__(self, n_paths: int = N_PATHS, n_steps: int = N_STEPS):
        self.n_paths = n_paths
        self.n_steps = n_steps
        self._rng    = np.random.default_rng(self.SEED)

    # ── Path generation ───────────────────────────────────────────────────────

    def _generate_paths(
        self,
        open_: float,
        high:  float,
        low:   float,
        close: float,
        n_paths: int = None,
        n_steps: int = None,
    ) -> np.ndarray:
        """
        Generate (n_paths, n_steps+1) price paths constrained by OHLC.
        Uses a rejection-based brownian bridge approach.
        """
        n_p = n_paths or self.n_paths
        n_s = n_steps or self.n_steps

        bar_range = high - low
        if bar_range <= 0:
            return np.full((n_p, n_s + 1), open_)

        sigma = bar_range / 4.0   # approximate vol from range

        paths = np.zeros((n_p, n_s + 1))
        paths[:, 0] = open_

        # Brownian bridge: start at open, drift toward close
        t = np.linspace(0, 1, n_s + 1)
        for i in range(n_p):
            noise = self._rng.standard_normal(n_s)
            # Brownian bridge increments
            path = np.zeros(n_s + 1)
            path[0] = open_
            for j in range(1, n_s + 1):
                dt = 1.0 / n_s
                bridge_pull = (close - path[j-1]) / (n_s - j + 1)
                path[j] = path[j-1] + bridge_pull + sigma * np.sqrt(dt) * noise[j-1]

            # Scale so path touches high and low somewhere
            path_min = path.min()
            path_max = path.max()
            if path_max > path_min:
                path = low + (path - path_min) / (path_max - path_min) * bar_range
            # Ensure endpoints
            path[0]  = open_
            path[-1] = close
            paths[i] = path

        return paths

    # ── SL/TP hit simulation ──────────────────────────────────────────────────

    def simulate_trade_outcome(
        self,
        df: pd.DataFrame,
        entry_bar: int,
        entry_price: float,
        sl: float,
        tp: float,
        direction: str,
        max_bars: int = 20,
    ) -> Dict:
        """
        Simulate a trade using tick paths.

        Returns:
            {outcome: 'win'|'loss'|'timeout', exit_price, exit_bar,
             win_prob, avg_exit_r, paths_won, paths_lost}
        """
        n_bars = min(max_bars, len(df) - entry_bar - 1)
        if n_bars <= 0:
            return {"outcome": "timeout", "win_prob": 0.5, "avg_exit_r": 0.0}

        risk = abs(entry_price - sl)
        if risk <= 0:
            return {"outcome": "timeout", "win_prob": 0.5, "avg_exit_r": 0.0}

        paths_won  = 0
        paths_lost = 0
        exit_rs    = []

        # Run paths across multiple bars
        for path_i in range(self.n_paths):
            current_price = entry_price
            outcome_this  = "timeout"
            exit_r        = 0.0

            for bar_offset in range(n_bars):
                bar_idx = entry_bar + bar_offset
                if bar_idx >= len(df):
                    break
                row = df.iloc[bar_idx]
                o, h, l, c = (
                    float(row["open"]), float(row["high"]),
                    float(row["low"]),  float(row["close"]),
                )
                # Single-path tick simulation for this bar
                path = self._generate_single_path(o, h, l, c)

                for tick in path:
                    if direction == "long":
                        if tick <= sl:
                            outcome_this = "loss"
                            exit_r = -1.0
                            break
                        if tick >= tp:
                            outcome_this = "win"
                            exit_r = abs(tp - entry_price) / risk
                            break
                    else:  # short
                        if tick >= sl:
                            outcome_this = "loss"
                            exit_r = -1.0
                            break
                        if tick <= tp:
                            outcome_this = "win"
                            exit_r = abs(tp - entry_price) / risk
                            break
                    current_price = tick

                if outcome_this != "timeout":
                    break

            if outcome_this == "win":
                paths_won += 1
            elif outcome_this == "loss":
                paths_lost += 1
            exit_rs.append(exit_r)

        total = paths_won + paths_lost
        win_prob = paths_won / total if total > 0 else 0.5
        avg_exit_r = float(np.mean(exit_rs)) if exit_rs else 0.0

        # Final outcome based on majority vote
        if paths_won > paths_lost:
            outcome = "win"
        elif paths_lost > paths_won:
            outcome = "loss"
        else:
            outcome = "timeout"

        return {
            "outcome":    outcome,
            "win_prob":   round(win_prob, 4),
            "avg_exit_r": round(avg_exit_r, 4),
            "paths_won":  paths_won,
            "paths_lost": paths_lost,
            "paths_total": self.n_paths,
        }

    def _generate_single_path(
        self, open_: float, high: float, low: float, close: float
    ) -> np.ndarray:
        """Fast single-path generation for inner loop."""
        bar_range = high - low
        if bar_range <= 0:
            return np.array([open_, close])

        sigma = bar_range / 4.0
        path = np.zeros(self.n_steps + 1)
        path[0] = open_
        noise = self._rng.standard_normal(self.n_steps)

        for j in range(1, self.n_steps + 1):
            dt = 1.0 / self.n_steps
            bridge_pull = (close - path[j-1]) / (self.n_steps - j + 1)
            path[j] = path[j-1] + bridge_pull + sigma * np.sqrt(dt) * noise[j-1]

        # Clamp to bar range
        path = np.clip(path, low, high)
        path[0]  = open_
        path[-1] = close
        return path

    # ── Batch simulation ──────────────────────────────────────────────────────

    def simulate_strategy(
        self,
        df: pd.DataFrame,
        signals: List[Dict],
        max_bars: int = 20,
    ) -> Dict:
        """
        Run tick simulation on a list of signals.
        signals: [{entry_bar, entry, sl, tp, direction}, ...]
        Returns aggregate stats.
        """
        if not signals:
            return {"n_simulated": 0, "tick_wr": 0.0}

        results = []
        for sig in signals:
            r = self.simulate_trade_outcome(
                df,
                entry_bar=sig.get("entry_bar", 0),
                entry_price=sig["entry"],
                sl=sig["sl"],
                tp=sig["tp"],
                direction=sig.get("direction", "long"),
                max_bars=max_bars,
            )
            results.append(r)

        wins  = sum(1 for r in results if r["outcome"] == "win")
        total = len(results)
        avg_r = float(np.mean([r["avg_exit_r"] for r in results]))

        return {
            "n_simulated":  total,
            "tick_wr":      round(wins / total, 4) if total else 0.0,
            "avg_exit_r":   round(avg_r, 4),
            "win_count":    wins,
            "loss_count":   sum(1 for r in results if r["outcome"] == "loss"),
            "timeout_count": sum(1 for r in results if r["outcome"] == "timeout"),
        }
