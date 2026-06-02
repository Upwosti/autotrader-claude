"""
Key Liquidity detection — institutional reference levels that act as Draw on
Liquidity (DOL) targets.

Detects:
    - Previous Week High / Low      (PWH / PWL)
    - Previous Day High / Low       (PDH / PDL)
    - Session highs/lows            (London, NY AM, NY PM)
    - Equal Highs / Equal Lows      (within a pip tolerance)

The DataFrame must carry a DatetimeIndex and lowercase OHLCV columns.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import time
from typing import List, Optional, Dict

from config import StrategyParams


# Session windows in UTC (per PDF 2 key-liquidity definitions).
SESSIONS: Dict[str, Dict[str, time]] = {
    "london":  {"start": time(2, 0),  "end": time(5, 0)},
    "ny_am":   {"start": time(14, 30), "end": time(17, 0)},   # 9:30–12:00 ET ≈ 14:30–17:00 UTC
    "ny_pm":   {"start": time(18, 30), "end": time(21, 30)},  # 1:30–4:30 ET ≈ 18:30–21:30 UTC
}


@dataclass
class KeyLevel:
    price: float
    label: str               # 'PWH','PWL','PDH','PDL','london_high', ... 'EQH','EQL'
    level_type: str          # 'high' | 'low'


class KeyLiquidityDetector:
    """Finds institutional key-liquidity levels used as DOL targets."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size

    # ── Previous Week / Day ───────────────────────────────────────────────
    def previous_week_levels(self, df: pd.DataFrame) -> List[KeyLevel]:
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) == 0:
            return []
        weeks = df.groupby(df.index.to_period("W"))
        periods = list(weeks.groups.keys())
        if len(periods) < 2:
            return []
        prev = weeks.get_group(periods[-2])
        return [
            KeyLevel(float(prev["high"].max()), "PWH", "high"),
            KeyLevel(float(prev["low"].min()), "PWL", "low"),
        ]

    def previous_day_levels(self, df: pd.DataFrame) -> List[KeyLevel]:
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) == 0:
            return []
        days = df.groupby(df.index.normalize())
        keys = list(days.groups.keys())
        if len(keys) < 2:
            return []
        prev = days.get_group(keys[-2])
        return [
            KeyLevel(float(prev["high"].max()), "PDH", "high"),
            KeyLevel(float(prev["low"].min()), "PDL", "low"),
        ]

    # ── Session highs/lows (most recent completed session of each type) ───
    def session_levels(self, df: pd.DataFrame) -> List[KeyLevel]:
        if not isinstance(df.index, pd.DatetimeIndex) or len(df) == 0:
            return []
        out: List[KeyLevel] = []
        times = df.index.time
        for name, win in SESSIONS.items():
            mask = np.array([win["start"] <= t < win["end"] for t in times])
            if not mask.any():
                continue
            sub = df[mask]
            # use the most recent session day
            last_day = sub.index.normalize().max()
            day_sub = sub[sub.index.normalize() == last_day]
            if day_sub.empty:
                continue
            out.append(KeyLevel(float(day_sub["high"].max()), f"{name}_high", "high"))
            out.append(KeyLevel(float(day_sub["low"].min()), f"{name}_low", "low"))
        return out

    # ── Equal highs / lows ────────────────────────────────────────────────
    def equal_levels(self, df: pd.DataFrame) -> List[KeyLevel]:
        n = len(df)
        if n == 0:
            return []
        lb = min(self.params.key_liquidity_lookback, n)
        sub = df.iloc[-lb:]
        highs = sub["high"].to_numpy(dtype=float)
        lows = sub["low"].to_numpy(dtype=float)
        tol = self.params.equal_level_tolerance_pips * self.pip_size
        out: List[KeyLevel] = []

        out += self._cluster_equal(highs, tol, "EQH", "high")
        out += self._cluster_equal(lows, tol, "EQL", "low")
        return out

    @staticmethod
    def _cluster_equal(values: np.ndarray, tol: float, label: str, level_type: str) -> List[KeyLevel]:
        """Find values that repeat within tol (≥2 occurrences) = liquidity pool."""
        out: List[KeyLevel] = []
        used = np.zeros(len(values), dtype=bool)
        for i in range(len(values)):
            if used[i]:
                continue
            close = np.abs(values - values[i]) <= tol
            if close.sum() >= 2:
                used |= close
                out.append(KeyLevel(float(np.mean(values[close])), label, level_type))
        return out

    # ── Aggregate ─────────────────────────────────────────────────────────
    def detect_all(self, df: pd.DataFrame) -> List[KeyLevel]:
        levels: List[KeyLevel] = []
        levels += self.previous_week_levels(df)
        levels += self.previous_day_levels(df)
        levels += self.session_levels(df)
        levels += self.equal_levels(df)
        return levels

    def get_nearest_target(self, df: pd.DataFrame, price: float, direction: str) -> Optional[KeyLevel]:
        """Return the nearest key level acting as DOL in the trade direction.
        long  -> nearest high above price (target to draw toward)
        short -> nearest low below price."""
        levels = self.detect_all(df)
        if direction == "long":
            cands = [lv for lv in levels if lv.level_type == "high" and lv.price > price]
        else:
            cands = [lv for lv in levels if lv.level_type == "low" and lv.price < price]
        if not cands:
            return None
        return min(cands, key=lambda lv: abs(price - lv.price))
