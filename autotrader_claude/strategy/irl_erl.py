"""
IRL / ERL model — Internal vs External Range Liquidity (ICT delivery cycle).

ERL = External Range Liquidity = swing highs / lows (BSL / SSL).
IRL = Internal Range Liquidity = FVGs / OBs inside the range.

Price endlessly cycles ERL -> IRL -> ERL -> IRL. The current Draw on Liquidity
(DOL) is determined by where in that cycle price currently sits.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List

from config import StrategyParams
from strategy.bos import BOSDetector
from strategy.fvg import FVGDetector
from strategy.pd_arrays import OrderBlockDetector


@dataclass
class DOLState:
    direction: str           # 'bullish' | 'bearish' | 'neutral'
    phase: str               # 'seeking_erl' | 'offering_irl'
    target_price: Optional[float]
    at_erl: bool
    at_irl: bool


class IRLERLAnalyzer:
    """Determines the current Draw on Liquidity from the ERL/IRL cycle."""

    def __init__(self, params: StrategyParams, pip_size: float = 0.0001):
        self.params = params
        self.pip_size = pip_size
        self.bos = BOSDetector(params)
        self.fvg = FVGDetector(params, pip_size=pip_size)
        self.ob = OrderBlockDetector(params, pip_size)

    # ── External range (swings) ───────────────────────────────────────────
    def _swings(self, df: pd.DataFrame):
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        half = max(1, self.params.structure_swing_lookback)
        n = len(highs)
        sh_idx, sl_idx = [], []
        for i in range(half, n - half):
            if highs[i] == np.max(highs[i - half:i + half + 1]):
                sh_idx.append(i)
            if lows[i] == np.min(lows[i - half:i + half + 1]):
                sl_idx.append(i)
        return sh_idx, sl_idx

    def is_at_erl(self, df: pd.DataFrame, price: float) -> bool:
        """True if price is near the most recent swing high or low."""
        sh, sl = self._swings(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        prox = self.params.irl_erl_proximity_pips * self.pip_size
        near = False
        if sh:
            near = near or abs(price - highs[sh[-1]]) <= prox
        if sl:
            near = near or abs(price - lows[sl[-1]]) <= prox
        return bool(near)

    def is_at_irl(self, df: pd.DataFrame, price: float) -> bool:
        """True if price sits inside a valid FVG or OB (internal liquidity)."""
        for fvg in self.fvg.get_valid_fvgs(df):
            if fvg.bottom <= price <= fvg.top:
                return True
        for ob in self.ob.detect(df):
            if ob.bottom <= price <= ob.top:
                return True
        return False

    def get_cycle_phase(self, df: pd.DataFrame) -> str:
        """If currently inside IRL we are 'offering_irl' (about to seek ERL).
        Otherwise price is travelling toward ERL = 'seeking_erl'."""
        price = float(df["close"].iloc[-1])
        if self.is_at_irl(df, price):
            return "seeking_erl"          # rebalanced internal, now draws to external
        return "offering_irl"             # away from IRL, will retrace to rebalance

    def get_current_dol(self, df: pd.DataFrame, htf_df: Optional[pd.DataFrame] = None) -> DOLState:
        """Compute the current Draw on Liquidity using HTF bias + swing targets."""
        price = float(df["close"].iloc[-1])
        bias_src = htf_df if htf_df is not None and len(htf_df) > self.params.bos_lookback * 2 else df
        direction = self.bos.get_bias(bias_src)

        sh, sl = self._swings(df)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)

        target = None
        if direction == "bullish":
            highs_above = [highs[i] for i in sh if highs[i] > price]
            target = min(highs_above) if highs_above else (float(highs[sh[-1]]) if sh else None)
        elif direction == "bearish":
            lows_below = [lows[i] for i in sl if lows[i] < price]
            target = max(lows_below) if lows_below else (float(lows[sl[-1]]) if sl else None)

        at_erl = self.is_at_erl(df, price)
        at_irl = self.is_at_irl(df, price)
        phase = self.get_cycle_phase(df)

        return DOLState(
            direction=direction,
            phase=phase,
            target_price=float(target) if target is not None else None,
            at_erl=bool(at_erl),
            at_irl=bool(at_irl),
        )
