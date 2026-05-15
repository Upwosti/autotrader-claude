"""
Monte Carlo validation — shuffles trade sequence 1000 times to eliminate lucky-streak strategies.
Strategy passes only if WR and drawdown hold across most shuffled sequences.
"""

import random
import numpy as np
from typing import List, Dict


def run_monte_carlo(
    trades: list,
    n_sims: int = 1000,
    wr_threshold: float = 0.60,   # 60% of raw WR in worst-case sim
    dd_threshold: float = 0.10,   # max 10% drawdown in any sim
    pass_rate_required: float = 0.90,  # 90% of sims must pass
) -> Dict:
    """
    Shuffle trade outcome sequence n_sims times.
    Returns dict with:
      pass_rate   — fraction of sims where WR ≥ wr_threshold AND max_dd ≤ dd_threshold
      min_wr      — worst WR across all sims
      avg_wr      — average WR across sims
      max_dd_p95  — 95th-percentile max drawdown
      passed      — bool
    """
    if len(trades) < 20:
        return {
            "passed": False, "pass_rate": 0.0,
            "min_wr": 0.0, "avg_wr": 0.0,
            "max_dd_p95": 1.0, "n_sims": n_sims,
            "reason": "insufficient trades",
        }

    # Extract outcomes and pnl_pcts as simple lists
    outcomes = [t.outcome for t in trades]
    pnls     = [t.pnl_pct for t in trades]
    n = len(outcomes)

    sim_wrs: List[float]  = []
    sim_dds: List[float]  = []
    pass_count = 0

    for _ in range(n_sims):
        # Shuffle in place using indices
        idx = list(range(n))
        random.shuffle(idx)
        shuffled_outcomes = [outcomes[i] for i in idx]
        shuffled_pnls     = [pnls[i]     for i in idx]

        wins = sum(1 for o in shuffled_outcomes if o == "win")
        wr   = wins / n
        sim_wrs.append(wr)

        # Max drawdown from equity curve
        rets = np.array([p / 100 for p in shuffled_pnls])
        cum  = np.cumprod(1.0 + rets)
        roll_max = np.maximum.accumulate(cum)
        dd = float(np.max((roll_max - cum) / np.where(roll_max > 0, roll_max, 1.0)))
        sim_dds.append(dd)

        if wr >= wr_threshold and dd <= dd_threshold:
            pass_count += 1

    pass_rate = pass_count / n_sims
    return {
        "passed":      pass_rate >= pass_rate_required,
        "pass_rate":   round(pass_rate, 4),
        "min_wr":      round(float(min(sim_wrs)), 4),
        "avg_wr":      round(float(np.mean(sim_wrs)), 4),
        "max_dd_p95":  round(float(np.percentile(sim_dds, 95)), 4),
        "n_sims":      n_sims,
        "reason":      "ok" if pass_rate >= pass_rate_required else "failed Monte Carlo",
    }
