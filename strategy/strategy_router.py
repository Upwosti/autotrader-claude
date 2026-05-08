"""StrategyRouter — per-pair strategy profiles with dynamic weighting."""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-pair strategy profiles
# ---------------------------------------------------------------------------

PAIR_PROFILES: Dict[str, List[str]] = {
    "XAUUSD":  ["ict_sweep_fvg", "order_block", "displacement"],
    "XAGUSD":  ["ict_sweep_fvg", "order_block"],
    "XPTUSD":  ["trend_continuation", "order_block"],
    "GBPUSD":  ["mean_reversion", "range_fade"],
    "EURUSD":  ["mean_reversion", "range_fade"],
    "USDJPY":  ["trend_continuation", "momentum"],
    "USDCHF":  ["mean_reversion", "range_fade"],
    "AUDUSD":  ["momentum", "trend_continuation"],
    "NZDUSD":  ["momentum", "mean_reversion"],
    "USDCAD":  ["momentum", "trend_continuation"],
    "EURJPY":  ["volatility_expansion", "session_breakout"],
    "GBPJPY":  ["volatility_expansion", "session_breakout"],
    "BTCUSD":  ["momentum", "volatility_breakout"],
    "ETHUSD":  ["momentum", "volatility_breakout"],
    "NAS100":  ["momentum", "trend_continuation"],
    "US30":    ["momentum", "trend_continuation"],
    "GER40":   ["momentum", "session_breakout"],
    "GC=F":    ["ict_sweep_fvg", "order_block", "displacement"],
    "SI=F":    ["ict_sweep_fvg", "order_block"],
}

# Default weight assigned to every strategy on first run
_DEFAULT_WEIGHT = 1.0
_WEIGHT_MIN = 0.1
_WEIGHT_MAX = 3.0
_WEIGHTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "local_db",
    "strategy_weights.json",
)


# ---------------------------------------------------------------------------
# StrategyRouter
# ---------------------------------------------------------------------------

class StrategyRouter:
    """Route trading signals to per-pair strategies with dynamic weight management."""

    def __init__(self, weights_path: Optional[str] = None) -> None:
        self._weights_path = weights_path or _WEIGHTS_PATH
        self._weights: Dict[str, Dict[str, float]] = {}
        self._load_weights()

    # ------------------------------------------------------------------
    # Weight persistence
    # ------------------------------------------------------------------

    def _load_weights(self) -> None:
        """Load weights from JSON file; initialise missing pairs/strategies."""
        loaded: Dict[str, Dict[str, float]] = {}
        if os.path.isfile(self._weights_path):
            try:
                with open(self._weights_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not load strategy weights from %s: %s", self._weights_path, exc)

        # Merge loaded weights with defaults for all known pairs/strategies
        for pair, strategies in PAIR_PROFILES.items():
            pair_weights = loaded.get(pair, {})
            self._weights[pair] = {}
            for strategy in strategies:
                raw = pair_weights.get(strategy, _DEFAULT_WEIGHT)
                self._weights[pair][strategy] = float(
                    max(_WEIGHT_MIN, min(_WEIGHT_MAX, raw))
                )

        # Preserve any extra pairs that were in the file but not in PAIR_PROFILES
        for pair, strategy_map in loaded.items():
            if pair not in self._weights:
                self._weights[pair] = {
                    s: float(max(_WEIGHT_MIN, min(_WEIGHT_MAX, w)))
                    for s, w in strategy_map.items()
                }

    def _save_weights(self) -> None:
        """Persist current weights to JSON."""
        os.makedirs(os.path.dirname(self._weights_path), exist_ok=True)
        try:
            with open(self._weights_path, "w", encoding="utf-8") as fh:
                json.dump(self._weights, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to save strategy weights to %s: %s", self._weights_path, exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_strategies(self, pair: str) -> List[str]:
        """Return the list of strategy names assigned to *pair*.

        Falls back to an empty list for unknown pairs.
        """
        return PAIR_PROFILES.get(pair, [])

    def get_weight(self, pair: str, strategy: str) -> float:
        """Return the current weight for a (pair, strategy) combination.

        Returns _DEFAULT_WEIGHT if the combination is not yet tracked.
        """
        return self._weights.get(pair, {}).get(strategy, _DEFAULT_WEIGHT)

    def update_weights(self, results: Dict[str, Dict[str, Dict]]) -> None:
        """Adjust weights based on recent performance results.

        Args:
            results: Nested dict of the form::

                {
                    pair: {
                        strategy_name: {
                            "wr":     float,   # win rate 0-1
                            "trades": int,     # number of trades
                            "rrr":    float,   # realized risk-reward ratio
                        }
                    }
                }

        Rules:
            - wr > 0.65  → weight += 0.1
            - wr < 0.50  → weight -= 0.1
            - Clamped to [0.1, 3.0]
        """
        for pair, strategy_map in results.items():
            if pair not in self._weights:
                self._weights[pair] = {}
            for strategy, metrics in strategy_map.items():
                wr = float(metrics.get("wr", 0.5))
                trades = int(metrics.get("trades", 0))

                # Require a minimum number of trades before adjusting
                if trades < 10:
                    continue

                current = self._weights[pair].get(strategy, _DEFAULT_WEIGHT)
                if wr > 0.65:
                    current += 0.1
                elif wr < 0.50:
                    current -= 0.1

                self._weights[pair][strategy] = round(
                    max(_WEIGHT_MIN, min(_WEIGHT_MAX, current)), 4
                )

        self._save_weights()
        logger.info("Strategy weights updated and saved.")

    def get_pair_confidence_multiplier(self, pair: str) -> float:
        """Return the weighted-average confidence multiplier for a pair.

        This is the arithmetic mean of all strategy weights for the pair,
        normalised so that a weight of 1.0 yields a multiplier of 1.0.
        """
        strategies = self.get_strategies(pair)
        if not strategies:
            return 1.0
        weights = [self.get_weight(pair, s) for s in strategies]
        return round(sum(weights) / len(weights), 4)

    def top_strategies(self, n: int = 5) -> List[Tuple[str, str, float]]:
        """Return the top *n* (pair, strategy, weight) tuples sorted by weight descending."""
        all_entries: List[Tuple[str, str, float]] = []
        for pair, strategy_map in self._weights.items():
            for strategy, weight in strategy_map.items():
                all_entries.append((pair, strategy, weight))
        all_entries.sort(key=lambda x: x[2], reverse=True)
        return all_entries[:n]

    def log_to_supabase(self, db) -> None:
        """Persist current weights to Supabase under the state key 'strategy_weights'.

        Args:
            db: A Supabase client (or compatible) that exposes a
                `.table(name).upsert(payload).execute()` interface.
        """
        try:
            payload = {
                "key": "strategy_weights",
                "value": json.dumps(self._weights),
            }
            db.table("state").upsert(payload).execute()
            logger.info("Strategy weights pushed to Supabase.")
        except Exception as exc:
            logger.warning("Could not log weights to Supabase: %s", exc)

    def summary(self) -> Dict:
        """Return a summary dict of all current weights.

        Format::

            {
                pair: {
                    strategy: weight,
                    ...
                    "_confidence_multiplier": float
                },
                ...
            }
        """
        out: Dict = {}
        for pair, strategy_map in self._weights.items():
            out[pair] = dict(strategy_map)
            out[pair]["_confidence_multiplier"] = self.get_pair_confidence_multiplier(pair)
        return out

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        pairs = len(self._weights)
        total_strategies = sum(len(v) for v in self._weights.values())
        return (
            f"StrategyRouter(pairs={pairs}, "
            f"total_strategy_weights={total_strategies}, "
            f"weights_path={self._weights_path!r})"
        )
