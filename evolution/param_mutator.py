"""
Parameter mutator for TrendParams — drives the evolution loop.
Mutates one parameter at a time, guaranteeing a different value.
Also supports architecture-level mutations (enabling/disabling filters).
"""

import copy
import random
from typing import Tuple, List, Dict, Any

from strategy.trend_engine import TrendParams
from loguru import logger

# ─── Evolvable parameter ranges ───────────────────────────────────────────────
# Format: param_name → list of valid values OR (min, max, step) tuple

TREND_PARAM_RANGES: Dict[str, Any] = {
    # EMA periods
    "ema_fast":   [13, 17, 21, 26],
    "ema_slow":   [34, 50, 55, 89],
    "ema_long":   [150, 200, 233],
    "ema_weekly": [10, 13, 20, 26],

    # SL / TP — include lower RRR to push win rate higher
    "sl_atr_mult": [0.2, 0.3, 0.5, 0.7, 1.0, 1.5],
    "tp_rrr":      [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0],

    # Trend strength
    "min_adx":   [15.0, 18.0, 22.0, 25.0, 30.0],

    # RSI bands
    "rsi_long_max":   [60.0, 65.0, 68.0, 70.0],
    "rsi_long_min":   [25.0, 28.0, 30.0, 35.0],
    "rsi_short_max":  [65.0, 70.0, 72.0, 75.0],
    "rsi_short_min":  [30.0, 32.0, 35.0, 40.0],

    # Pullback zone
    "pullback_atr_mult": [1.0, 1.2, 1.5, 1.8, 2.0, 2.5],

    # Volume filter
    "min_vol_ratio": [0.5, 0.7, 0.8, 1.0, 1.2],

    # Boolean flags (architecture mutations)
    "use_weekly_filter": [True, False],
    "use_ema_stack":     [True, False],
    "use_pattern":       [True, False],
    "use_pullback_zone": [True, False],
    "use_volume_filter": [True, False],
    "use_expansion":     [True, False],
    "use_killzone":      [True, False],
    "use_ict_filter":    [True, False],

    # ICT score threshold when use_ict_filter=True
    "ict_min_score":     [30, 40, 50, 60],

    # Min confluence threshold
    "min_confluence": [2, 3, 4, 5],

    # Hold time
    "min_hold_bars": [0, 1, 2],
}


class TrendParamMutator:
    """Mutates TrendParams, guaranteeing new_val != old_val."""

    def __init__(self):
        self.param_weights: Dict[str, float] = {k: 1.0 for k in TREND_PARAM_RANGES}
        # Downweight boolean flags (only flip occasionally)
        for k in ("use_weekly_filter", "use_ema_stack", "use_pattern",
                  "use_pullback_zone", "use_volume_filter", "use_expansion"):
            self.param_weights[k] = 0.3
        self.param_weights["use_killzone"]   = 0.5  # higher chance — strong XAUUSD signal
        self.param_weights["use_ict_filter"] = 0.6  # priority — key to pushing XAU WR to 80%
        self.param_weights["ict_min_score"]  = 0.4  # tune threshold when filter is on

    def mutate(
        self,
        params: TrendParams,
        strategy: str = "random",
    ) -> Tuple[TrendParams, str, str, str]:
        """
        Returns (new_params, param_name, old_value_str, new_value_str).
        strategy: 'random' | 'directional'
        """
        new_params = copy.deepcopy(params)
        keys = list(TREND_PARAM_RANGES.keys())
        weights = [self.param_weights.get(k, 1.0) for k in keys]
        total_w = sum(weights)
        probs = [w / total_w for w in weights]

        # Shuffle candidates, try each until we find a valid mutation
        order = random.choices(keys, weights=probs, k=len(keys))
        seen = set()
        for param_name in order:
            if param_name in seen:
                continue
            seen.add(param_name)

            old_value = getattr(new_params, param_name)
            candidates = [v for v in TREND_PARAM_RANGES[param_name]
                          if v != old_value]
            if not candidates:
                continue

            new_val = random.choice(candidates)
            setattr(new_params, param_name, new_val)
            new_params.version = params.version + 1
            new_params.notes   = f"Mutated {param_name}: {old_value} → {new_val}"
            logger.info(f"Mutation: {param_name} {old_value} → {new_val}")
            return new_params, param_name, str(old_value), str(new_val)

        # Fallback (all params at boundary — extremely rare)
        new_params.version = params.version + 1
        return new_params, "none", "none", "none"

    def smart_mutate(
        self,
        params: TrendParams,
        blocker_params: List[str],
    ) -> Tuple[TrendParams, str, str, str]:
        """
        Prioritise mutating parameters identified as blockers.
        Uses boosted weights so blockers are actually targeted.
        """
        if not blocker_params:
            return self.mutate(params)

        # Build boosted weight map and use it for selection
        tmp = copy.deepcopy(self.param_weights)
        for k in blocker_params:
            if k in tmp:
                tmp[k] *= 8.0

        keys = list(TREND_PARAM_RANGES.keys())
        weights = [tmp.get(k, 1.0) for k in keys]
        total_w = sum(weights)
        probs = [w / total_w for w in weights]

        new_params = copy.deepcopy(params)
        order = random.choices(keys, weights=probs, k=len(keys))
        seen: set = set()
        for param_name in order:
            if param_name in seen:
                continue
            seen.add(param_name)

            old_value = getattr(new_params, param_name)
            candidates = [v for v in TREND_PARAM_RANGES[param_name] if v != old_value]
            if not candidates:
                continue

            new_val = random.choice(candidates)
            setattr(new_params, param_name, new_val)
            new_params.version = params.version + 1
            new_params.notes   = f"Smart: {param_name} {old_value} → {new_val}"
            logger.info(f"Smart mutation: {param_name} {old_value} → {new_val}")
            return new_params, param_name, str(old_value), str(new_val)

        return self.mutate(params)  # fallback

    def neighbourhood_mutate(
        self,
        params: TrendParams,
        last_param: str,
    ) -> Tuple[TrendParams, str, str, str]:
        """
        Explore nearby values of the same param that was last improved.
        """
        new_params = copy.deepcopy(params)
        if last_param not in TREND_PARAM_RANGES:
            return self.mutate(params)

        old_value  = getattr(new_params, last_param)
        candidates = [v for v in TREND_PARAM_RANGES[last_param] if v != old_value]
        if not candidates:
            return self.mutate(params)

        new_val = random.choice(candidates)
        setattr(new_params, last_param, new_val)
        new_params.version = params.version + 1
        new_params.notes   = f"Neighbourhood: {last_param} {old_value} → {new_val}"
        logger.info(f"Neighbourhood mutation: {last_param} {old_value} → {new_val}")
        return new_params, last_param, str(old_value), str(new_val)
