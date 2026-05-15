"""
Liquidity sweep detection — ICT concept.

A liquidity pool forms where multiple candles have touched the same
high or low (equal highs / equal lows). Smart money sweeps these levels
to trigger stop-losses before reversing. A valid sweep requires:
  - Price wicks beyond the level (stop hunt)
  - Candle closes back inside the range (rejection)
  - The wick constitutes a significant portion of the candle range
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional
from config import StrategyParams


@dataclass
class LiquidityLevel:
    price: float
    index: int
    level_type: str       # 'high' | 'low'
    touches: int
    strength: float       # 0–1, how closely prices clustered
    swept: bool = False
    sweep_index: Optional[int] = None
    sweep_price: Optional[float] = None
    wick_pct: float = 0.0


@dataclass
class LiquiditySweep:
    level: LiquidityLevel
    sweep_candle_index: int
    direction: str          # 'bearish_sweep' (swept highs) | 'bullish_sweep' (swept lows)
    wick_pct: float
    confirmed: bool
    # True if the candle that swept also closed back inside (strong confirmation)
    closed_back: bool = False


class LiquidityDetector:
    """Detects liquidity pools and sweeps using ICT equal highs/lows concept."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def _relative_tolerance(self, price: float) -> float:
        """
        Dynamic tolerance for 'equal' levels: 0.1% of price.
        This handles gold at 4700 (±$4.7) vs EUR/USD at 1.10 (±0.0011).
        """
        return price * 0.001

    def find_levels(self, df: pd.DataFrame) -> List[LiquidityLevel]:
        """
        Find equal highs and equal lows that form liquidity pools.
        Uses a rolling window scan — each local extreme is checked against
        prior extremes within the lookback window.
        """
        levels: List[LiquidityLevel] = []
        lookback = self.params.liquidity_sweep_lookback
        min_touches = self.params.liquidity_min_touches

        highs = df["high"].values
        lows = df["low"].values
        n = len(df)

        # Identify local swing highs (highest in ±2 bar neighbourhood)
        for i in range(2, n - 2):
            if not (highs[i] >= highs[i-1] and highs[i] >= highs[i+1]):
                continue

            pivot = highs[i]
            tol = self._relative_tolerance(pivot)
            window = highs[max(0, i - lookback): i]
            # Count prior highs within tolerance of this pivot
            similar = int(np.sum(np.abs(window - pivot) <= tol))
            if similar >= min_touches - 1:
                strength = 1.0 - (np.mean(np.abs(window[np.abs(window - pivot) <= tol] - pivot)) / tol
                                  if np.any(np.abs(window - pivot) <= tol) else 1.0)
                levels.append(LiquidityLevel(
                    price=pivot,
                    index=i,
                    level_type="high",
                    touches=similar + 1,
                    strength=min(1.0, strength),
                ))

        # Identify local swing lows
        for i in range(2, n - 2):
            if not (lows[i] <= lows[i-1] and lows[i] <= lows[i+1]):
                continue

            pivot = lows[i]
            tol = self._relative_tolerance(pivot)
            window = lows[max(0, i - lookback): i]
            similar = int(np.sum(np.abs(window - pivot) <= tol))
            if similar >= min_touches - 1:
                strength = 1.0 - (np.mean(np.abs(window[np.abs(window - pivot) <= tol] - pivot)) / tol
                                  if np.any(np.abs(window - pivot) <= tol) else 1.0)
                levels.append(LiquidityLevel(
                    price=pivot,
                    index=i,
                    level_type="low",
                    touches=similar + 1,
                    strength=min(1.0, strength),
                ))

        return levels

    def detect_sweeps(self, df: pd.DataFrame,
                      levels: List[LiquidityLevel]) -> List[LiquiditySweep]:
        """
        Detect which levels have been swept.
        A sweep requires:
          1. Price wicks beyond the level (high > level for a high, low < level for a low)
          2. Candle closes back inside (rejection confirmation)
          3. The rejection wick is >= params.liquidity_sweep_wick_pct of candle range
        """
        sweeps: List[LiquiditySweep] = []
        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values
        n = len(df)

        for level in levels:
            for i in range(level.index + 1, n):
                candle_range = highs[i] - lows[i]
                if candle_range < 1e-9:
                    continue

                if level.level_type == "high" and highs[i] > level.price:
                    # Wick above the level
                    candle_body_top = max(opens[i], closes[i])
                    wick_above = highs[i] - candle_body_top
                    wick_pct = wick_above / candle_range
                    # Must close back below the swept level
                    closed_back = closes[i] < level.price

                    level.swept = True
                    level.sweep_index = i
                    level.sweep_price = highs[i]
                    level.wick_pct = wick_pct

                    confirmed = (wick_pct >= self.params.liquidity_sweep_wick_pct
                                 and closed_back)
                    sweeps.append(LiquiditySweep(
                        level=level,
                        sweep_candle_index=i,
                        direction="bearish_sweep",
                        wick_pct=wick_pct,
                        confirmed=confirmed,
                        closed_back=closed_back,
                    ))
                    break  # one sweep per level

                elif level.level_type == "low" and lows[i] < level.price:
                    candle_body_bottom = min(opens[i], closes[i])
                    wick_below = candle_body_bottom - lows[i]
                    wick_pct = wick_below / candle_range
                    closed_back = closes[i] > level.price

                    level.swept = True
                    level.sweep_index = i
                    level.sweep_price = lows[i]
                    level.wick_pct = wick_pct

                    confirmed = (wick_pct >= self.params.liquidity_sweep_wick_pct
                                 and closed_back)
                    sweeps.append(LiquiditySweep(
                        level=level,
                        sweep_candle_index=i,
                        direction="bullish_sweep",
                        wick_pct=wick_pct,
                        confirmed=confirmed,
                        closed_back=closed_back,
                    ))
                    break

        return sweeps

    def get_latest_sweep(self, df: pd.DataFrame) -> Optional[LiquiditySweep]:
        """Return the most recent confirmed liquidity sweep."""
        levels = self.find_levels(df)
        if not levels:
            return None
        sweeps = self.detect_sweeps(df, levels)
        confirmed = [s for s in sweeps if s.confirmed]
        if not confirmed:
            return None
        return max(confirmed, key=lambda s: s.sweep_candle_index)
