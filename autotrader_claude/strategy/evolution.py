"""
Strategy Evolver — mutates parameters, compares results, keeps improvements.
"""

import random
import copy
from typing import Optional, Tuple
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
        Randomly mutate one parameter.
        Returns: (new_params, param_name, old_value, new_value)
        """
        new_params = copy.deepcopy(params)
        param_name = random.choice(list(PARAM_RANGES.keys()))
        old_value = str(getattr(new_params, param_name))
        rng = PARAM_RANGES[param_name]

        if isinstance(rng, list):
            new_val = random.choice(rng)
        else:
            lo, hi, step = rng
            steps = int((hi - lo) / step)
            new_val = lo + random.randint(0, steps) * step
            if isinstance(lo, int):
                new_val = int(new_val)

        setattr(new_params, param_name, new_val)
        new_params.version = params.version + 1
        new_params.notes = f"Mutated {param_name}: {old_value} → {new_val}"

        logger.info(f"Mutation: {param_name} {old_value} → {new_val}")
        return new_params, param_name, old_value, str(new_val)

    def evaluate(
        self,
        new_win_rate: float,
        old_win_rate: float,
        new_params: StrategyParams,
        min_trades: int = 100,
        trade_count: int = 0,
    ) -> Tuple[bool, str]:
        """
        Compare new vs old win rate.
        Returns (kept: bool, decision: str).
        """
        if trade_count < min_trades:
            return False, f"insufficient_data ({trade_count} trades)"

        if new_win_rate > old_win_rate:
            logger.info(f"Improvement: {old_win_rate:.1%} → {new_win_rate:.1%} — keeping")
            if new_win_rate > self.best_win_rate:
                self.best_win_rate = new_win_rate
                self.best_params = copy.deepcopy(new_params)
            return True, "kept_improvement"
        else:
            logger.info(f"No improvement: {old_win_rate:.1%} vs {new_win_rate:.1%} — reverting")
            return False, "reverted"

    def next_iteration(
        self,
        current_win_rate: float,
        current_trade_count: int,
    ) -> Tuple[StrategyParams, str, str, str, bool]:
        """
        Generate next iteration params.
        Returns: (new_params, param_name, old_val, new_val, is_improvement)
        """
        new_params, param, old, new = self.mutate(self.current_params)
        kept, decision = self.evaluate(
            new_win_rate=current_win_rate,
            old_win_rate=self.best_win_rate,
            new_params=new_params,
            trade_count=current_trade_count,
        )
        if kept:
            self.current_params = new_params
        self.iteration += 1
        return new_params, param, old, new, kept

    def get_best(self) -> StrategyParams:
        return copy.deepcopy(self.best_params)
