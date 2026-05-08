"""
Strategy Evolver — mutates parameters, compares results, keeps improvements.
"""

import random
import copy
from typing import Tuple
from config import StrategyParams, PARAM_RANGES
from loguru import logger


class StrategyEvolver:
    """Manages parameter mutation and version selection."""

    def __init__(self):
        self.current_params = StrategyParams()
        self.best_params = copy.deepcopy(self.current_params)
        self.best_win_rate: float = 0.0
        self.iteration: int = 1

    def mutate(self, params: StrategyParams) -> Tuple[StrategyParams, str, str, str]:
        """
        Randomly mutate one parameter, guaranteeing new_val != old_val.
        Returns: (new_params, param_name, old_value, new_value)
        """
        new_params = copy.deepcopy(params)
        param_keys = list(PARAM_RANGES.keys())
        random.shuffle(param_keys)

        for param_name in param_keys:
            old_value = str(getattr(new_params, param_name))
            rng = PARAM_RANGES[param_name]

            if isinstance(rng, list):
                candidates = [v for v in rng if str(v) != old_value]
                if not candidates:
                    continue
                new_val = random.choice(candidates)
            else:
                lo, hi, step = rng
                steps = int(round((hi - lo) / step))
                candidates = [lo + j * step for j in range(steps + 1)
                              if str(round(lo + j * step, 10)) != old_value]
                if isinstance(lo, int):
                    candidates = [int(v) for v in candidates]
                if not candidates:
                    continue
                new_val = random.choice(candidates)
                new_val = round(float(new_val), 10) if not isinstance(new_val, int) else new_val

            setattr(new_params, param_name, new_val)
            new_params.version = params.version + 1
            new_params.notes = f"Mutated {param_name}: {old_value} -> {new_val}"
            logger.info(f"Mutation: {param_name} {old_value} -> {new_val}")
            return new_params, param_name, old_value, str(new_val)

        # Fallback (should never reach here): mutate version only
        new_params.version = params.version + 1
        return new_params, "none", "none", "none"

    def _composite_score(
        self,
        win_rate: float,
        trade_count: int,
        profit_factor: float = 1.0,
        max_drawdown_pct: float = 10.0,
    ) -> float:
        """
        Weighted composite score. Win rate is unreliable with few trades,
        so we weight by sample reliability and penalise high drawdown.
        """
        reliability = min(1.0, trade_count / 30)   # full weight at 30+ trades
        dd_penalty = max(0.0, (max_drawdown_pct - 5.0) / 100.0)  # penalise DD > 5%
        pf_bonus = min(0.5, (profit_factor - 1.0) * 0.1) if profit_factor > 1 else 0.0
        return (win_rate * reliability + pf_bonus) - dd_penalty

    def evaluate(
        self,
        new_win_rate: float,
        old_win_rate: float,
        new_params: StrategyParams,
        new_trade_count: int = 0,
        min_trades: int = 10,
        new_profit_factor: float = 1.0,
        old_profit_factor: float = 1.0,
        new_max_dd: float = 10.0,
        old_max_dd: float = 10.0,
    ) -> Tuple[bool, str]:
        """
        Compare new result vs current using composite score.
        Returns (kept: bool, decision: str).
        """
        if new_trade_count < min_trades:
            logger.info(f"Insufficient trades ({new_trade_count} < {min_trades}) — reverting")
            return False, "reverted"

        new_score = self._composite_score(new_win_rate, new_trade_count, new_profit_factor, new_max_dd)
        old_score = self._composite_score(
            old_win_rate, max(new_trade_count, 10), old_profit_factor, old_max_dd
        )

        if new_score > old_score:
            logger.info(f"Improvement: score {old_score:.3f} -> {new_score:.3f} "
                        f"(WR {old_win_rate:.1%} -> {new_win_rate:.1%}) — keeping")
            if new_win_rate > self.best_win_rate:
                self.best_win_rate = new_win_rate
                self.best_params = copy.deepcopy(new_params)
            return True, "kept"
        else:
            logger.info(f"No improvement: score {new_score:.3f} vs {old_score:.3f} — reverting")
            return False, "reverted"

    def next_iteration(
        self,
        current_win_rate: float,
        current_trade_count: int,
        new_win_rate: float = None,
        new_trade_count: int = None,
    ) -> Tuple[StrategyParams, str, str, str, bool]:
        """
        Generate next mutated params and evaluate if new result was provided.

        Call pattern in optimizer:
          1. new_params, param, old, new, _ = evolver.next_iteration(...)  # get mutated params
          2. new_result = run_backtest(new_params)
          3. kept = evolver.accept(new_result.win_rate, current_win_rate, new_params, new_result.total_trades)
        """
        new_params, param, old, new = self.mutate(self.current_params)
        self.iteration += 1
        # Return False for kept — optimizer calls accept() after running the backtest
        return new_params, param, old, new, False

    def accept(
        self,
        new_win_rate: float,
        old_win_rate: float,
        new_params: StrategyParams,
        new_trade_count: int,
        min_trades: int = 10,
        new_profit_factor: float = 1.0,
        old_profit_factor: float = 1.0,
        new_max_dd: float = 10.0,
        old_max_dd: float = 10.0,
    ) -> bool:
        """
        Called by optimizer AFTER running the new backtest.
        Returns True if mutation is kept, False if reverted.
        """
        kept, _ = self.evaluate(
            new_win_rate=new_win_rate,
            old_win_rate=old_win_rate,
            new_params=new_params,
            new_trade_count=new_trade_count,
            min_trades=min_trades,
            new_profit_factor=new_profit_factor,
            old_profit_factor=old_profit_factor,
            new_max_dd=new_max_dd,
            old_max_dd=old_max_dd,
        )
        if kept:
            self.current_params = new_params
        return kept

    def get_best(self) -> StrategyParams:
        return copy.deepcopy(self.best_params)
