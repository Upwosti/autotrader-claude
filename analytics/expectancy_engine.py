"""
OMEGA Expectancy Engine

Tracks, calculates, and evolves expectancy metrics per pair.
Primary metric: E = (WR × AvgWin) - (LossRate × AvgLoss)
Target: E > 0.5R per trade

Also tracks:
  - Running expectancy (last 20/50/100 trades)
  - Regime-adjusted expectancy
  - Expectancy stability (rolling std)
  - Best/worst pair by expectancy
  - Pair personality signatures
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

STATE_FILE = Path(__file__).parent.parent / "local_db" / "expectancy_state.json"
DB_PATH    = Path(__file__).parent.parent / "data" / "autotrader.db"

# OMEGA targets
TARGET_EXPECTANCY = 0.5     # R per trade minimum
TARGET_RRR        = 2.5     # avg realized RR minimum
TARGET_WR         = 0.60    # win rate target
MIN_SAMPLES       = 20      # minimum trades for reliable expectancy


@dataclass
class PairExpectancy:
    pair: str
    n_trades: int = 0
    win_rate: float = 0.0
    avg_win_r: float = 0.0      # average R gained on winners
    avg_loss_r: float = 1.0     # average R lost on losers (usually 1.0)
    expectancy: float = 0.0     # primary metric
    expectancy_20: float = 0.0  # rolling 20-trade expectancy
    expectancy_50: float = 0.0  # rolling 50-trade expectancy
    stability: float = 0.0      # lower = more stable expectancy
    best_regime: str = ""       # regime where expectancy is highest
    status: str = "insufficient_data"  # "positive" | "marginal" | "negative"
    risk_multiplier: float = 1.0


@dataclass
class ExpectancyReport:
    timestamp: str
    pairs: Dict[str, PairExpectancy] = field(default_factory=dict)
    best_pair: str = ""
    worst_pair: str = ""
    portfolio_expectancy: float = 0.0


class ExpectancyEngine:
    """
    Tracks and evolves expectancy metrics.
    Called after each backtest result.
    """

    def __init__(self):
        self._state: Dict[str, PairExpectancy] = {}
        self._load_state()

    def update_from_result(self, pair: str, result: dict) -> PairExpectancy:
        """Update expectancy from a backtest result dict."""
        wr  = result.get("win_rate", 0)
        rrr = result.get("avg_rrr", 0)
        n   = result.get("trades", 0)

        exp = self._compute_expectancy(wr, rrr)

        pe = PairExpectancy(
            pair=pair,
            n_trades=n,
            win_rate=round(wr, 4),
            avg_win_r=round(rrr, 3),
            avg_loss_r=1.0,
            expectancy=round(exp, 4),
            status=self._classify(exp, n),
            risk_multiplier=self._risk_mult(exp, n),
        )
        self._state[pair] = pe
        self._save_state()
        return pe

    def update_from_live_trades(self, pair: str, trades: list) -> PairExpectancy:
        """Update from live trade list (each trade has pnl_r field)."""
        if not trades:
            return self._state.get(pair, PairExpectancy(pair=pair))

        pnls = [t.get("pnl_r", t.get("rr_achieved", 0)) for t in trades if t]
        if not pnls:
            return self._state.get(pair, PairExpectancy(pair=pair))

        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        n = len(pnls)
        wr     = len(wins) / n if n > 0 else 0
        avg_w  = sum(wins) / len(wins) if wins else 0
        avg_l  = abs(sum(losses) / len(losses)) if losses else 1.0
        exp    = wr * avg_w - (1 - wr) * avg_l

        # Rolling windows
        exp20 = self._rolling_exp(pnls, 20)
        exp50 = self._rolling_exp(pnls, 50)

        try:
            import numpy as np
            windows = [self._rolling_exp(pnls, 20) for _ in range(max(1, n-20+1))]
            stability = float(np.std(windows)) if len(windows) > 1 else 0.0
        except Exception:
            stability = 0.0

        pe = PairExpectancy(
            pair=pair, n_trades=n, win_rate=round(wr, 4),
            avg_win_r=round(avg_w, 3), avg_loss_r=round(avg_l, 3),
            expectancy=round(exp, 4),
            expectancy_20=round(exp20, 4), expectancy_50=round(exp50, 4),
            stability=round(stability, 4),
            status=self._classify(exp, n),
            risk_multiplier=self._risk_mult(exp, n),
        )
        self._state[pair] = pe
        self._save_state()
        return pe

    def get_best_pairs(self, n: int = 5) -> List[Tuple[str, float]]:
        """Return top N pairs by expectancy."""
        items = [(p, pe.expectancy) for p, pe in self._state.items()
                 if pe.n_trades >= MIN_SAMPLES]
        return sorted(items, key=lambda x: x[1], reverse=True)[:n]

    def get_report(self) -> ExpectancyReport:
        pairs = dict(self._state)
        valid = [(p, pe) for p, pe in pairs.items() if pe.n_trades >= MIN_SAMPLES]
        if valid:
            best  = max(valid, key=lambda x: x[1].expectancy)
            worst = min(valid, key=lambda x: x[1].expectancy)
            port_exp = sum(pe.expectancy for _, pe in valid) / len(valid)
        else:
            best = worst = (None, None)
            port_exp = 0.0

        return ExpectancyReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            pairs=pairs,
            best_pair=best[0] or "",
            worst_pair=worst[0] or "",
            portfolio_expectancy=round(port_exp, 4),
        )

    def format_telegram(self) -> str:
        r = self.get_report()
        lines = [f"EXPECTANCY REPORT",
                 f"Portfolio E: {r.portfolio_expectancy:.3f}R/trade",
                 f"Best: {r.best_pair}",
                 f"Worst: {r.worst_pair}",
                 ""]
        for p, pe in sorted(r.pairs.items(), key=lambda x: x[1].expectancy, reverse=True)[:8]:
            icon = "✅" if pe.expectancy >= TARGET_EXPECTANCY else ("⚠️" if pe.expectancy > 0 else "❌")
            lines.append(f"{icon} {p}: E={pe.expectancy:.3f} WR={pe.win_rate:.1%} "
                         f"AvgWin={pe.avg_win_r:.2f}R n={pe.n_trades}")
        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_expectancy(wr: float, avg_win_r: float, avg_loss_r: float = 1.0) -> float:
        return wr * avg_win_r - (1 - wr) * avg_loss_r

    @staticmethod
    def _classify(exp: float, n: int) -> str:
        if n < MIN_SAMPLES:
            return "insufficient_data"
        if exp >= TARGET_EXPECTANCY:
            return "positive"
        if exp > 0:
            return "marginal"
        return "negative"

    @staticmethod
    def _risk_mult(exp: float, n: int) -> float:
        if n < MIN_SAMPLES:
            return 0.5
        if exp >= TARGET_EXPECTANCY:
            return 1.0
        if exp > 0:
            return 0.75
        return 0.25

    @staticmethod
    def _rolling_exp(pnls: list, window: int) -> float:
        w = pnls[-window:] if len(pnls) >= window else pnls
        if not w:
            return 0.0
        wins = [p for p in w if p > 0]
        loss = [p for p in w if p <= 0]
        wr = len(wins) / len(w)
        aw = sum(wins) / len(wins) if wins else 0
        al = abs(sum(loss) / len(loss)) if loss else 1.0
        return wr * aw - (1 - wr) * al

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    d = json.load(f)
                for pair, data in d.get("pairs", {}).items():
                    self._state[pair] = PairExpectancy(**{
                        k: v for k, v in data.items()
                        if k in PairExpectancy.__dataclass_fields__
                    })
        except Exception:
            pass

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "updated": datetime.now(timezone.utc).isoformat(),
                    "pairs": {p: asdict(pe) for p, pe in self._state.items()},
                }, f, indent=2)
        except Exception as e:
            logger.debug(f"[EXP] save: {e}")


# Module-level singleton
_engine: Optional[ExpectancyEngine] = None

def get_engine() -> ExpectancyEngine:
    global _engine
    if _engine is None:
        _engine = ExpectancyEngine()
    return _engine
