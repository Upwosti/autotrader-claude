"""
Liquidity sweep detection.
Identifies equal highs/lows (liquidity pools) and detects when price sweeps them.
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


class LiquidityDetector:
    """Detects liquidity pools and sweeps using ICT concepts."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def find_levels(self, df: pd.DataFrame) -> List[LiquidityLevel]:
        """Find equal highs and equal lows that form liquidity pools."""
        levels: List[LiquidityLevel] = []
        lookback = self.params.liquidity_sweep_lookback
        tolerance = 0.0005  # 0.05% tolerance for "equal" levels

        highs = df["high"].values
        lows = df["low"].values
        n = len(df)

        # Find swing highs
        for i in range(lookback, n - 1):
            pivot_high = highs[i]
            window = highs[max(0, i - lookback): i]
            similar = np.sum(np.abs(window - pivot_high) / pivot_high < tolerance)
            if similar >= self.params.liquidity_min_touches - 1:
                levels.append(LiquidityLevel(
                    price=pivot_high,
                    index=i,
                    level_type="high",
                    touches=int(similar) + 1,
                ))

        # Find swing lows
        for i in range(lookback, n - 1):
            pivot_low = lows[i]
            window = lows[max(0, i - lookback): i]
            similar = np.sum(np.abs(window - pivot_low) / pivot_low < tolerance)
            if similar >= self.params.liquidity_min_touches - 1:
                levels.append(LiquidityLevel(
                    price=pivot_low,
                    index=i,
                    level_type="low",
                    touches=int(similar) + 1,
                ))

        return levels

    def detect_sweeps(self, df: pd.DataFrame, levels: List[LiquidityLevel]) -> List[LiquiditySweep]:
        """Detect which levels have been swept and evaluate quality of sweep."""
        sweeps: List[LiquiditySweep] = []
        highs = df["high"].values
        lows = df["low"].values
        opens = df["open"].values
        closes = df["close"].values
        n = len(df)

        for level in levels:
            start = level.index + 1
            for i in range(start, n):
                candle_range = highs[i] - lows[i]
                if candle_range == 0:
                    continue

                if level.level_type == "high":
                    if highs[i] > level.price:
                        # Swept a high — potential bearish reversal
                        wick_above = highs[i] - max(opens[i], closes[i])
                        wick_pct = wick_above / candle_range
                        level.swept = True
                        level.sweep_index = i
                        level.sweep_price = highs[i]
                        level.wick_pct = wick_pct
                        confirmed = wick_pct >= self.params.liquidity_sweep_wick_pct
                        sweeps.append(LiquiditySweep(
                            level=level,
                            sweep_candle_index=i,
                            direction="bearish_sweep",
                            wick_pct=wick_pct,
                            confirmed=confirmed,
                        ))
                        break

                elif level.level_type == "low":
                    if lows[i] < level.price:
                        # Swept a low — potential bullish reversal
                        wick_below = min(opens[i], closes[i]) - lows[i]
                        wick_pct = wick_below / candle_range
                        level.swept = True
                        level.sweep_index = i
                        level.sweep_price = lows[i]
                        level.wick_pct = wick_pct
                        confirmed = wick_pct >= self.params.liquidity_sweep_wick_pct
                        sweeps.append(LiquiditySweep(
                            level=level,
                            sweep_candle_index=i,
                            direction="bullish_sweep",
                            wick_pct=wick_pct,
                            confirmed=confirmed,
                        ))
                        break

        return sweeps

    def get_latest_sweep(self, df: pd.DataFrame) -> Optional[LiquiditySweep]:
        """Return the most recent confirmed liquidity sweep."""
        levels = self.find_levels(df)
        sweeps = self.detect_sweeps(df, levels)
        confirmed = [s for s in sweeps if s.confirmed]
        if not confirmed:
            return None
        return max(confirmed, key=lambda s: s.sweep_candle_index)
