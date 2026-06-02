"""
Trading Module System — 4 independent modules operating simultaneously.

Entry Module ≠ Trading Module:
  - Entry Module  (entry_engine.py): detects WHEN to enter (signal detection)
  - Trading Module (this file):     defines HOW to trade (TF context, CRT/TBS,
                                    position sizing, SL/TP rules, management)

4 Modules from ICT Forex & Crypto Timeframes:
  ┌──────────────┬──────┬───────┬───────┐
  │ Module       │ HTF  │ LTF1  │ LTF2  │
  ├──────────────┼──────┼───────┼───────┤
  │ Position     │  MN  │  W1   │  D1   │ Monthly CRT → Daily TBS
  │ Swing        │  W1  │  D1   │  4H   │ Weekly CRT  → 4H TBS   ✓ preferred
  │ Intraday     │  D1  │  4H   │  1H   │ Daily CRT   → 1H TBS
  │ Scalp        │  4H  │  1H   │  15M  │ 4H CRT      → 15M TBS
  └──────────────┴──────┴───────┴───────┘

CRT+TBS preferred pairs (from ICT material):
  4H  → 5M  ✓
  W1  → 4H  ✓

All 4 modules run simultaneously. Any module that fires a valid signal
creates a separate trade opportunity. Signals do NOT block each other.
"""

import sys
import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'autotrader_claude'))
from config import StrategyParams
from strategy.crt_tbs import CRTTBSDetector, CRT, TBS, MultiTFCRTResult


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class TradingModuleConfig:
    name: str          # 'position' | 'swing' | 'intraday' | 'scalp'
    htf: str           # Higher timeframe — bias frame
    ltf1: str          # CRT reference candle timeframe
    ltf2: str          # TBS entry timeframe
    min_rrr: float     # Minimum acceptable RRR
    max_risk_pct: float  # Maximum risk per trade as % of account
    preferred_crt_tbs: bool  # Whether this uses a preferred CRT+TBS pair
    description: str


TRADING_MODULE_CONFIGS = [
    TradingModuleConfig(
        name="position",
        htf="MN", ltf1="W1", ltf2="D1",
        min_rrr=3.0, max_risk_pct=0.5,
        preferred_crt_tbs=False,
        description="Position: Monthly HTF bias | Weekly CRT | Daily TBS",
    ),
    TradingModuleConfig(
        name="swing",
        htf="W1", ltf1="D1", ltf2="4H",
        min_rrr=2.5, max_risk_pct=0.75,
        preferred_crt_tbs=True,   # W1→4H is a preferred ICT pair ✓
        description="Swing: Weekly HTF bias | Daily CRT | 4H TBS  ✓ preferred",
    ),
    TradingModuleConfig(
        name="intraday",
        htf="D1", ltf1="4H", ltf2="1H",
        min_rrr=2.0, max_risk_pct=1.0,
        preferred_crt_tbs=False,
        description="Intraday: Daily HTF bias | 4H CRT | 1H TBS",
    ),
    TradingModuleConfig(
        name="scalp",
        htf="4H", ltf1="1H", ltf2="15M",
        min_rrr=2.0, max_risk_pct=1.0,
        preferred_crt_tbs=False,
        description="Scalp: 4H HTF bias | 1H CRT | 15M TBS",
    ),
]


# ── Signal dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModuleSignal:
    module_name: str
    direction: str          # 'long' | 'short' | 'none'
    entry_model: str        # 'ob' | 'fvg' | 'bos' | 'none'
    entry_price: float
    stop_loss: float
    take_profit: float
    rrr: float
    htf: str
    ltf1: str
    ltf2: str
    htf_bias: str           # 'bullish' | 'bearish' | 'neutral'
    crt: Optional[CRT]
    tbs: Optional[TBS]
    valid: bool
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    preferred_pair: bool = False


@dataclass
class ModuleManagerResult:
    signals: List[ModuleSignal]
    valid_signals: List[ModuleSignal]
    best: Optional[ModuleSignal]  # highest-confidence valid signal
    total_modules_fired: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Single Trading Module ─────────────────────────────────────────────────────

class TradingModule:
    """One trading module: uses 3 timeframes (HTF/LTF1/LTF2) for a complete
    multi-timeframe analysis. Entry is always taken on LTF2 after:
      1. HTF bias established
      2. CRT reference candle found on LTF1
      3. TBS confirmed on LTF2 (one side purged, ≥2 bodies inside, entry model)
    """

    def __init__(self, config: TradingModuleConfig, params: StrategyParams):
        self.config = config
        self.params = params
        self.pip_size = 0.01   # default, overridden by evaluate()

    def _htf_bias(self, htf_df: pd.DataFrame) -> str:
        """Determine bias from the HTF dataframe using last 20 bar slope."""
        if len(htf_df) < 5:
            return "neutral"
        c = htf_df["close"].to_numpy(dtype=float)
        n = min(20, len(c))
        slope = (c[-1] - c[-n]) / n
        atr = float(np.mean(np.abs(np.diff(c[-n:]))))
        if atr == 0:
            return "neutral"
        if slope > atr * 0.1:
            return "bullish"
        if slope < -atr * 0.1:
            return "bearish"
        return "neutral"

    def _calc_take_profit(self, direction: str, entry: float, sl: float) -> float:
        risk = abs(entry - sl)
        reward = risk * self.config.min_rrr
        return entry + reward if direction == "long" else entry - reward

    def evaluate(
        self,
        htf_df: pd.DataFrame,
        ltf1_df: pd.DataFrame,
        ltf2_df: pd.DataFrame,
        pip_size: float = 0.01,
        timestamp: Optional[datetime] = None,
    ) -> ModuleSignal:
        """Evaluate this trading module and return a ModuleSignal.

        Flow:
          1. Get HTF bias
          2. Detect CRT on LTF1
          3. Detect TBS on LTF2 using CRT price range
          4. Check entry model (OB / FVG / BOS)
          5. Validate direction aligns with HTF bias
          6. Compute entry, SL, TP, RRR
        """
        self.pip_size = pip_size
        ts = timestamp or datetime.now(timezone.utc)
        cfg = self.config

        def _skip(reason: str) -> ModuleSignal:
            return ModuleSignal(
                module_name=cfg.name, direction="none", entry_model="none",
                entry_price=0.0, stop_loss=0.0, take_profit=0.0, rrr=0.0,
                htf=cfg.htf, ltf1=cfg.ltf1, ltf2=cfg.ltf2,
                htf_bias="neutral", crt=None, tbs=None,
                valid=False, reason=reason, timestamp=ts,
                preferred_pair=cfg.preferred_crt_tbs,
            )

        if len(htf_df) < 5 or len(ltf1_df) < 5 or len(ltf2_df) < 10:
            return _skip(f"[{cfg.name}] insufficient data")

        htf_bias = self._htf_bias(htf_df)

        detector = CRTTBSDetector(self.params, pip_size=pip_size)
        result: MultiTFCRTResult = detector.detect_multi_tf(
            ltf1_df, ltf2_df, cfg.ltf1, cfg.ltf2)

        crt = result.crt
        tbs = result.tbs

        if crt is None:
            return _skip(f"[{cfg.name}] no CRT reference candle on {cfg.ltf1}")
        if not result.is_high_prob:
            return _skip(f"[{cfg.name}] TBS not high-probability on {cfg.ltf2}")

        direction = result.direction
        if direction == "none":
            return _skip(f"[{cfg.name}] TBS direction undetermined")

        # HTF bias must align (or be neutral — still tradeable but lower confidence)
        bias_aligned = (
            (direction == "long" and htf_bias == "bullish") or
            (direction == "short" and htf_bias == "bearish") or
            htf_bias == "neutral"
        )
        if not bias_aligned:
            return _skip(
                f"[{cfg.name}] direction={direction} conflicts with HTF bias={htf_bias}"
            )

        entry = result.entry_price if result.entry_price > 0 else float(ltf2_df["close"].iloc[-1])
        sl = result.stop_price
        if sl == 0.0:
            buf = pip_size * 5
            sl = (crt.low - buf) if direction == "long" else (crt.high + buf)

        tp = self._calc_take_profit(direction, entry, sl)
        rrr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0.0

        if rrr < cfg.min_rrr:
            return _skip(
                f"[{cfg.name}] RRR={rrr:.2f} < min {cfg.min_rrr}"
            )

        pref_tag = " ✓preferred" if cfg.preferred_crt_tbs else ""
        reason = (
            f"[{cfg.name}{pref_tag}] HTF={htf_bias} | CRT on {cfg.ltf1} "
            f"| TBS on {cfg.ltf2} | model={result.entry_model} "
            f"| dir={direction} | RRR={rrr:.2f}"
        )

        return ModuleSignal(
            module_name=cfg.name,
            direction=direction,
            entry_model=result.entry_model,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            rrr=rrr,
            htf=cfg.htf,
            ltf1=cfg.ltf1,
            ltf2=cfg.ltf2,
            htf_bias=htf_bias,
            crt=crt,
            tbs=tbs,
            valid=True,
            reason=reason,
            timestamp=ts,
            preferred_pair=cfg.preferred_crt_tbs,
        )


# ── Module Manager (runs all 4 simultaneously) ────────────────────────────────

class TradingModuleManager:
    """Runs all 4 trading modules simultaneously.

    Any module that fires is a valid trade opportunity — they do not block
    each other. A scalp trade can run alongside a swing trade.

    Required data keys (pass only what you have; missing TFs are skipped):
      'MN', 'W1', 'D1', '4H', '1H', '15M'
    """

    def __init__(self, params: StrategyParams):
        self.params = params
        self.modules = [
            TradingModule(cfg, params)
            for cfg in TRADING_MODULE_CONFIGS
        ]

    def evaluate_all(
        self,
        data_by_tf: Dict[str, pd.DataFrame],
        pip_size: float = 0.01,
        timestamp: Optional[datetime] = None,
    ) -> ModuleManagerResult:
        """Evaluate every module. Returns all signals and highlights best.

        data_by_tf: {'MN': df, 'W1': df, 'D1': df, '4H': df, '1H': df, '15M': df}
        Missing keys → that module emits an invalid signal (skipped).
        """
        ts = timestamp or datetime.now(timezone.utc)
        signals: List[ModuleSignal] = []

        for module in self.modules:
            cfg = module.config
            htf_df = data_by_tf.get(cfg.htf, pd.DataFrame())
            ltf1_df = data_by_tf.get(cfg.ltf1, pd.DataFrame())
            ltf2_df = data_by_tf.get(cfg.ltf2, pd.DataFrame())
            sig = module.evaluate(htf_df, ltf1_df, ltf2_df, pip_size, ts)
            signals.append(sig)

        valid = [s for s in signals if s.valid]

        best = None
        if valid:
            # Prefer preferred pairs; among those, highest RRR
            valid.sort(key=lambda s: (not s.preferred_pair, -s.rrr))
            best = valid[0]

        return ModuleManagerResult(
            signals=signals,
            valid_signals=valid,
            best=best,
            total_modules_fired=len(valid),
            timestamp=ts,
        )

    def get_module(self, name: str) -> Optional[TradingModule]:
        """Retrieve a specific module by name."""
        for m in self.modules:
            if m.config.name == name:
                return m
        return None

    def summary(self, result: ModuleManagerResult) -> str:
        lines = [f"Trading Modules — {result.total_modules_fired}/4 fired"]
        for s in result.signals:
            status = "FIRE" if s.valid else "SKIP"
            lines.append(f"  [{status}] {s.module_name:10s} | {s.reason}")
        if result.best:
            lines.append(f"  BEST → {result.best.module_name} | {result.best.direction} "
                         f"| entry={result.best.entry_price:.4f} | RRR={result.best.rrr:.2f}")
        return "\n".join(lines)
