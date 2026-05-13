"""
OMEGA Evolution Engine

Standalone evolution loop with expectancy-first acceptance.
Wraps WalkForwardBacktester + Monte Carlo + acceptance rules.

Acceptance criteria (all must pass):
  1. WR >= 90% of best_WR[pair]
  2. RRR >= 1.0
  3. Max drawdown < 8%
  4. Profit Factor > 1.3
  5. No overfit (train/test gap < 15%)
  6. Expectancy > best_expectancy[pair]
  7. Monte Carlo survival > 65%

Partial exits: 25% at 1R, 25% at 2R, 50% runner with adaptive trailing.
Strong momentum: skip early exits, allow 5R-10R+ runners.
"""

from __future__ import annotations

import copy
import gc
import json
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

STATE_FILE = Path(__file__).parent.parent / "local_db" / "evolution_state.json"

# ── Acceptance thresholds ─────────────────────────────────────────────────────
MIN_TRADES        = 15
WR_FLOOR_RATIO    = 0.90   # WR >= 90% of best
RRR_FLOOR         = 1.0
MAX_DD            = 0.08
MIN_PF            = 1.3
MC_SURVIVAL       = 0.65
MAX_OVERFIT_GAP   = 0.15   # train/test WR gap

# ── Partial exit profile ──────────────────────────────────────────────────────
PARTIAL_1R  = 0.25   # close 25% at 1R
PARTIAL_2R  = 0.25   # close 25% at 2R
RUNNER_PCT  = 0.50   # 50% runner with adaptive trailing


@dataclass
class EvolutionResult:
    pair: str
    accepted: bool
    rejection_reason: str = ""

    win_rate: float = 0.0
    avg_rrr: float  = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    mc_survival: float = 0.0
    trades: int = 0
    overfit_gap: float = 0.0

    prev_expectancy: float = 0.0
    improvement: float = 0.0   # new_E - prev_E

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PairEvolutionState:
    pair: str
    best_wr: float = 0.0
    best_rrr: float = 0.0
    best_expectancy: float = 0.0
    best_score: float = 0.0
    no_improve: int = 0
    total_accepted: int = 0
    last_accepted: str = ""
    best_params: dict = field(default_factory=dict)


class EvolutionEngine:
    """
    Standalone evolution engine.
    Can be used independently or integrated with run_forever.py.

    Usage:
        engine = EvolutionEngine()
        result = engine.evaluate(pair, backtest_result_dict)
        if result.accepted:
            engine.accept(pair, params, backtest_result_dict)
    """

    def __init__(self):
        self._state: Dict[str, PairEvolutionState] = {}
        self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(self, pair: str, result: dict) -> EvolutionResult:
        """Run all 7 acceptance checks. Return EvolutionResult."""
        wr     = result.get("win_rate", 0)
        rrr    = result.get("avg_rrr", 0)
        trades = result.get("trades", 0)
        dd     = result.get("max_drawdown", 0)
        pf     = result.get("profit_factor", 0)
        mc     = result.get("monte_carlo_pass_rate", 1.0)
        overfit = result.get("overfitting", False)
        train_wr = result.get("train_wr", wr)

        state = self._state.get(pair, PairEvolutionState(pair=pair))
        prev_e = state.best_expectancy

        er = EvolutionResult(
            pair=pair, accepted=False,
            win_rate=round(wr, 4), avg_rrr=round(rrr, 3),
            profit_factor=round(pf, 3), max_drawdown=round(dd, 4),
            mc_survival=round(mc, 3), trades=trades,
            overfit_gap=round(abs(train_wr - wr), 4),
            prev_expectancy=round(prev_e, 4),
        )

        # Run checks
        reason = self._check_all(pair, wr, rrr, trades, dd, pf, mc, overfit, train_wr, state)
        if reason:
            er.rejection_reason = reason
            return er

        # Compute expectancy
        expectancy = wr * rrr - (1 - wr)
        er.expectancy = round(expectancy, 4)
        er.improvement = round(expectancy - prev_e, 4)

        # Must beat current best expectancy
        cur_score = state.best_score
        new_score = expectancy + max(0.0, rrr - 1.5) * 0.05
        if new_score <= cur_score:
            er.rejection_reason = f"score={new_score:.4f} <= best={cur_score:.4f}"
            return er

        er.accepted = True
        return er

    def accept(self, pair: str, params: dict, result: dict, ev_result: EvolutionResult = None):
        """Record accepted strategy for pair."""
        wr  = result.get("win_rate", 0)
        rrr = result.get("avg_rrr", 0)
        exp = wr * rrr - (1 - wr)
        score = exp + max(0.0, rrr - 1.5) * 0.05

        state = self._state.get(pair, PairEvolutionState(pair=pair))
        state.best_wr          = max(state.best_wr, wr)
        state.best_rrr         = max(state.best_rrr, rrr)
        state.best_expectancy  = max(state.best_expectancy, exp)
        state.best_score       = score
        state.no_improve       = 0
        state.total_accepted  += 1
        state.last_accepted    = datetime.now(timezone.utc).isoformat()
        state.best_params      = copy.deepcopy(params)
        self._state[pair] = state
        self._save_state()

    def increment_no_improve(self, pair: str) -> int:
        state = self._state.get(pair, PairEvolutionState(pair=pair))
        state.no_improve += 1
        self._state[pair] = state
        return state.no_improve

    def get_best_expectancy(self, pair: str) -> float:
        return self._state.get(pair, PairEvolutionState(pair=pair)).best_expectancy

    def get_best_wr(self, pair: str) -> float:
        return self._state.get(pair, PairEvolutionState(pair=pair)).best_wr

    def generate_report(self) -> str:
        """Text report of all pairs sorted by expectancy."""
        lines = ["=== EVOLUTION REPORT ===",
                 f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC", ""]
        items = sorted(self._state.values(), key=lambda s: s.best_expectancy, reverse=True)
        for s in items:
            icon = "✅" if s.best_expectancy >= 0.5 else ("⚠️" if s.best_expectancy > 0 else "❌")
            lines.append(f"{icon} {s.pair}: E={s.best_expectancy:.3f} "
                         f"WR={s.best_wr:.1%} RRR={s.best_rrr:.2f} "
                         f"accepted={s.total_accepted} no_improve={s.no_improve}")
        return "\n".join(lines)

    # ── Checks ────────────────────────────────────────────────────────────────

    def _check_all(
        self,
        pair: str, wr: float, rrr: float, trades: int,
        dd: float, pf: float, mc: float, overfit: bool,
        train_wr: float, state: PairEvolutionState,
    ) -> str:
        """Return rejection reason string, or "" if all pass."""

        if trades < MIN_TRADES:
            return f"trades={trades} < {MIN_TRADES}"

        # 1. WR floor: >= 90% of pair's best WR
        wr_floor = state.best_wr * WR_FLOOR_RATIO if state.best_wr > 0 else 0.50
        if wr < wr_floor:
            return f"wr={wr:.1%} < floor={wr_floor:.1%}"

        # 2. RRR floor
        if rrr < RRR_FLOOR:
            return f"rrr={rrr:.3f} < {RRR_FLOOR}"

        # 3. Drawdown
        if dd > MAX_DD:
            return f"dd={dd:.1%} > {MAX_DD:.0%}"

        # 4. Profit Factor
        if pf > 0 and pf < MIN_PF:
            return f"pf={pf:.2f} < {MIN_PF}"

        # 5. Overfit: train/test WR gap
        gap = abs(train_wr - wr)
        if gap > MAX_OVERFIT_GAP and state.best_wr > 0:
            return f"overfit_gap={gap:.2f} > {MAX_OVERFIT_GAP}"
        if overfit:
            return "overfit_flag=True"

        # 6. Expectancy > 0
        exp = wr * rrr - (1 - wr)
        if exp <= 0:
            return f"expectancy={exp:.4f} <= 0"

        # 7. Monte Carlo
        if mc < MC_SURVIVAL:
            return f"mc_survival={mc:.2f} < {MC_SURVIVAL}"

        return ""  # all passed

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    d = json.load(f)
                for pair, data in d.get("pairs", {}).items():
                    fields = {k: v for k, v in data.items()
                              if k in PairEvolutionState.__dataclass_fields__}
                    self._state[pair] = PairEvolutionState(**fields)
        except Exception as e:
            logger.debug(f"[EVO] load state: {e}")

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "pairs": {p: asdict(s) for p, s in self._state.items()},
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"[EVO] save state: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def compute_expectancy(win_rate: float, avg_win_r: float, avg_loss_r: float = 1.0) -> float:
    """E = (WR * avg_win) - ((1-WR) * avg_loss)"""
    return win_rate * avg_win_r - (1 - win_rate) * avg_loss_r


def compute_partial_exit_rrr(
    tp_full_rrr: float,
    p1r: float = PARTIAL_1R,
    p2r: float = PARTIAL_2R,
    runner_exit_rrr: float = None,
) -> float:
    """
    Compute blended realized RRR from partial exit profile.
    Default: 25%@1R + 25%@2R + 50%@runner
    """
    runner_pct = 1.0 - p1r - p2r
    runner_exit = runner_exit_rrr if runner_exit_rrr else tp_full_rrr
    return p1r * 1.0 + p2r * 2.0 + runner_pct * runner_exit


# Module-level singleton
_engine: Optional[EvolutionEngine] = None

def get_engine() -> EvolutionEngine:
    global _engine
    if _engine is None:
        _engine = EvolutionEngine()
    return _engine
