"""
OMEGA Portfolio Engine

Manages correlated exposure across pairs.
Builds on live_exposure_engine.py with full portfolio intelligence.

Tracks:
  - USD exposure (long/short across all USD pairs)
  - Metals exposure (XAU, XAG, GC=F, SI=F)
  - Crypto exposure (BTC, ETH)
  - Indices (NAS100, US30, GER40)
  - Correlation matrix (dynamic + static fallback)

Rules:
  - Max 2R risk per correlated cluster
  - Max 3 simultaneous USD-direction trades
  - Block tight clusters (metals, crypto) at 1 trade
  - EURUSD long + USDCHF long = double-exposure warning
  - Total open risk ≤ 3R at any time
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger

# Re-export from live_exposure_engine for backwards compat
from portfolio.live_exposure_engine import (
    LiveExposureEngine,
    ExposureReport,
    OpenTrade,
    check_portfolio_ftmo,
    CORRELATION_CLUSTERS,
    MAX_CLUSTER_EXPOSURE_R,
)

STATE_FILE = Path(__file__).parent.parent / "local_db" / "portfolio_state.json"

# Total portfolio risk cap
MAX_TOTAL_RISK_R  = 3.0
# Max simultaneous trades
MAX_OPEN_TRADES   = 5
# Correlation threshold
CORR_THRESHOLD    = 0.70


@dataclass
class PortfolioState:
    open_trades: List[OpenTrade] = field(default_factory=list)
    total_risk_r: float = 0.0
    cluster_exposure: Dict[str, float] = field(default_factory=dict)
    daily_loss_pct: float = 0.0
    daily_pnl_r: float = 0.0
    peak_balance: float = 10000.0
    current_balance: float = 10000.0
    last_updated: str = ""


@dataclass
class AllocationDecision:
    allowed: bool = True
    reason: str = ""
    suggested_risk_r: float = 1.0    # adjusted risk size
    cluster: str = ""
    warnings: List[str] = field(default_factory=list)


class PortfolioEngine:
    """
    Full portfolio intelligence engine.
    Wraps LiveExposureEngine with additional portfolio-level checks.

    Usage:
        engine = PortfolioEngine()
        decision = engine.check_trade(pair, direction, open_trades, base_risk=1.0)
        if decision.allowed:
            place_trade(risk_r=decision.suggested_risk_r)
    """

    def __init__(self):
        self._exposure = LiveExposureEngine()
        self._state    = PortfolioState()
        self._load_state()

    def check_trade(
        self,
        pair: str,
        direction: str,
        open_trades: List[OpenTrade],
        base_risk: float = 1.0,
    ) -> AllocationDecision:
        """Full portfolio check before new trade."""
        decision = AllocationDecision(suggested_risk_r=base_risk)

        # 1. Total risk cap
        total = sum(t.risk_r for t in open_trades)
        if total + base_risk > MAX_TOTAL_RISK_R:
            available = max(0.0, MAX_TOTAL_RISK_R - total)
            if available < 0.1:
                decision.allowed = False
                decision.reason = f"Portfolio at {total:.1f}R / {MAX_TOTAL_RISK_R}R limit"
                return decision
            decision.suggested_risk_r = min(base_risk, available)
            decision.warnings.append(f"Risk reduced to {decision.suggested_risk_r:.2f}R (portfolio limit)")

        # 2. Max open trades
        if len(open_trades) >= MAX_OPEN_TRADES:
            decision.allowed = False
            decision.reason = f"{len(open_trades)} open trades at max limit"
            return decision

        # 3. Correlation + cluster checks
        report = self._exposure.check_new_trade(pair, direction, open_trades)
        if not report.allowed:
            decision.allowed = False
            decision.reason = report.reason
            return decision
        decision.warnings.extend(report.warnings)

        # 4. Determine cluster for logging
        decision.cluster = self._exposure._get_cluster(pair)

        return decision

    def get_heat_map(self, open_trades: List[OpenTrade]) -> dict:
        """Portfolio heat map with FTMO compliance check."""
        heat = self._exposure.get_portfolio_heat(open_trades)
        ftmo = check_portfolio_ftmo(open_trades, self._state.daily_loss_pct)
        return {**heat, "ftmo": ftmo}

    def update_balance(self, pnl_r: float, risk_pct: float = 0.01):
        """Update running balance and daily drawdown."""
        pnl_pct = pnl_r * risk_pct
        self._state.current_balance *= (1 + pnl_pct)
        self._state.daily_pnl_r += pnl_r
        if self._state.current_balance > self._state.peak_balance:
            self._state.peak_balance = self._state.current_balance
        dd = (self._state.peak_balance - self._state.current_balance) / self._state.peak_balance
        self._state.daily_loss_pct = max(0.0, dd)
        self._save_state()

    def reset_daily(self):
        self._state.daily_pnl_r = 0.0
        self._state.last_updated = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def format_status(self, open_trades: List[OpenTrade]) -> str:
        heat = self.get_heat_map(open_trades)
        lines = [
            f"PORTFOLIO STATUS",
            f"Open R: {heat['total_open_r']:.1f} / {MAX_TOTAL_RISK_R}R",
            f"Trades: {len(open_trades)} / {MAX_OPEN_TRADES}",
            f"Daily PnL: {self._state.daily_pnl_r:+.2f}R",
        ]
        for cluster, r in heat.get("cluster_exposure", {}).items():
            if r > 0:
                lines.append(f"  {cluster}: {r:.1f}R")
        if not heat["ftmo"]["safe"]:
            lines.append(f"⚠️ FTMO: {' | '.join(heat['ftmo']['issues'])}")
        return "\n".join(lines)

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    d = json.load(f)
                self._state.daily_loss_pct = d.get("daily_loss_pct", 0.0)
                self._state.daily_pnl_r    = d.get("daily_pnl_r", 0.0)
                self._state.peak_balance   = d.get("peak_balance", 10000.0)
                self._state.current_balance = d.get("current_balance", 10000.0)
        except Exception:
            pass

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({
                    "updated":          datetime.now(timezone.utc).isoformat(),
                    "daily_loss_pct":   self._state.daily_loss_pct,
                    "daily_pnl_r":      self._state.daily_pnl_r,
                    "peak_balance":     self._state.peak_balance,
                    "current_balance":  self._state.current_balance,
                }, f, indent=2)
        except Exception:
            pass


# Module-level singleton
_portfolio: Optional[PortfolioEngine] = None

def get_portfolio() -> PortfolioEngine:
    global _portfolio
    if _portfolio is None:
        _portfolio = PortfolioEngine()
    return _portfolio
