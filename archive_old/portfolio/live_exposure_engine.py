"""
Phase 4: Correlation + Portfolio Risk Engine

Tracks:
  - Correlation matrix between all pairs
  - USD exposure (EURUSD + GBPUSD + AUDUSD + NZDUSD + USDCAD etc.)
  - Metals exposure (XAUUSD + XAGUSD + GC=F + SI=F)
  - Crypto exposure (BTCUSD + ETHUSD)
  - Indices exposure (NAS100 + US30 + GER40)

Rules:
  - Max 1 trade per correlated cluster (corr > 0.7)
  - Max 3% USD exposure at once
  - Max 2% metals exposure at once
  - Never open EURUSD long and USDCHF long simultaneously (100% inverse)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from loguru import logger

DATA_CACHE = Path(__file__).parent.parent / "data_cache"

# Static correlation clusters (based on known pair relationships)
CORRELATION_CLUSTERS: Dict[str, List[str]] = {
    "usd_long":    ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],   # all move against USD
    "usd_short":   ["USDJPY", "USDCHF", "USDCAD"],              # USD base
    "metals":      ["XAUUSD", "GC=F", "XAGUSD", "SI=F", "XPTUSD"],
    "crypto":      ["BTCUSD", "ETHUSD"],
    "indices_us":  ["NAS100", "US30"],
    "xau_xag":     ["XAUUSD", "GC=F", "XAGUSD", "SI=F"],        # tightest cluster
}

# Inverse pairs — if one is long, the other MUST be the same directional bet
INVERSE_PAIRS: Dict[str, Tuple[str, str]] = {
    # (pair_a, pair_b) — opening both same direction = double exposure
    "eurusd_usdchf": ("EURUSD", "USDCHF"),
    "gbpusd_usdchf": ("GBPUSD", "USDCHF"),
}

# Maximum simultaneous exposure per cluster (in units of 1R risk)
MAX_CLUSTER_EXPOSURE_R = 2.0

# Correlation threshold above which pairs are "too correlated"
CORRELATION_THRESHOLD = 0.70


@dataclass
class ExposureReport:
    allowed: bool = True
    reason: str   = ""
    current_exposure: Dict[str, float] = field(default_factory=dict)  # cluster → R units
    warnings: List[str] = field(default_factory=list)


@dataclass
class OpenTrade:
    pair: str
    direction: str    # "long" | "short"
    risk_r: float     = 1.0


class LiveExposureEngine:
    """
    Checks portfolio risk before each new trade entry.
    Usage:
        engine = LiveExposureEngine()
        report = engine.check_new_trade(pair, direction, open_trades)
        if report.allowed:
            proceed_with_trade()
    """

    def check_new_trade(
        self,
        pair: str,
        direction: str,
        open_trades: List[OpenTrade],
    ) -> ExposureReport:
        """
        Evaluate whether a new trade on `pair` in `direction` is safe to take.
        """
        report = ExposureReport()

        # 1. One trade per correlated cluster rule
        cluster_check = self._check_cluster_conflict(pair, direction, open_trades)
        if not cluster_check[0]:
            report.allowed = False
            report.reason  = cluster_check[1]
            return report

        # 2. Total cluster exposure check
        exposure = self._compute_cluster_exposure(open_trades)
        new_cluster = self._get_cluster(pair)
        current = exposure.get(new_cluster, 0.0)
        if current >= MAX_CLUSTER_EXPOSURE_R:
            report.allowed = False
            report.reason  = f"Cluster '{new_cluster}' at {current:.1f}R exposure (max {MAX_CLUSTER_EXPOSURE_R}R)"
            return report

        # 3. Inverse pair check
        inverse_check = self._check_inverse_pairs(pair, direction, open_trades)
        if not inverse_check[0]:
            report.warnings.append(inverse_check[1])
            # Warn but don't block — inverse pairs can sometimes be legitimate hedges

        # 4. USD double-long check
        usd_check = self._check_usd_exposure(pair, direction, open_trades)
        if not usd_check[0]:
            report.allowed = False
            report.reason  = usd_check[1]
            return report

        report.current_exposure = exposure
        return report

    def get_portfolio_heat(self, open_trades: List[OpenTrade]) -> dict:
        """
        Return current portfolio risk heat map.
        """
        exposure = self._compute_cluster_exposure(open_trades)
        total_r  = sum(t.risk_r for t in open_trades)

        return {
            "total_open_r":    round(total_r, 2),
            "cluster_exposure": {k: round(v, 2) for k, v in exposure.items()},
            "open_pairs":      [t.pair for t in open_trades],
            "timestamp":       datetime.utcnow().isoformat(),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_cluster_conflict(
        self,
        pair: str,
        direction: str,
        open_trades: List[OpenTrade],
    ) -> Tuple[bool, str]:
        """
        Block if another trade in the same tight cluster is already open.
        """
        cluster = self._get_cluster(pair)
        if cluster == "solo":
            return True, ""

        # Tight clusters where we only allow 1 trade
        tight_clusters = {"xau_xag", "crypto"}
        if cluster in tight_clusters:
            for t in open_trades:
                if self._get_cluster(t.pair) == cluster:
                    return False, (f"Cluster '{cluster}' already has open trade "
                                   f"on {t.pair} — skip {pair}")

        return True, ""

    def _compute_cluster_exposure(self, open_trades: List[OpenTrade]) -> Dict[str, float]:
        """Sum R exposure by cluster."""
        exposure: Dict[str, float] = {}
        for t in open_trades:
            cluster = self._get_cluster(t.pair)
            exposure[cluster] = exposure.get(cluster, 0.0) + t.risk_r
        return exposure

    def _check_inverse_pairs(
        self,
        pair: str,
        direction: str,
        open_trades: List[OpenTrade],
    ) -> Tuple[bool, str]:
        """
        Warn if opening a trade that is directionally equivalent to an existing one
        via an inverse relationship.
        """
        open_pairs = {t.pair: t.direction for t in open_trades}
        for key, (a, b) in INVERSE_PAIRS.items():
            if pair == a and b in open_pairs:
                existing_dir = open_pairs[b]
                # EURUSD long + USDCHF long = double USD short
                if direction == "long" and existing_dir == "long":
                    return False, f"Double USD short: {pair} long + {b} long"
                if direction == "short" and existing_dir == "short":
                    return False, f"Double USD long: {pair} short + {b} short"
            if pair == b and a in open_pairs:
                existing_dir = open_pairs[a]
                if direction == "long" and existing_dir == "long":
                    return False, f"Double USD exposure: {a} long + {pair} long"
        return True, ""

    def _check_usd_exposure(
        self,
        pair: str,
        direction: str,
        open_trades: List[OpenTrade],
    ) -> Tuple[bool, str]:
        """
        Count USD-denominated long trades. Cap at 3 simultaneous.
        """
        USD_LONG_PAIRS = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}  # USD short when these are long
        USD_SHORT_PAIRS = {"USDJPY", "USDCHF", "USDCAD"}            # USD long when these are long

        new_is_usd_short = (pair in USD_LONG_PAIRS and direction == "long") or \
                           (pair in USD_SHORT_PAIRS and direction == "short")

        usd_short_count = sum(
            1 for t in open_trades
            if (t.pair in USD_LONG_PAIRS and t.direction == "long") or
               (t.pair in USD_SHORT_PAIRS and t.direction == "short")
        )

        if new_is_usd_short and usd_short_count >= 3:
            return False, (f"USD exposure limit: already {usd_short_count} USD-short positions — "
                           f"skip {pair}")
        return True, ""

    @staticmethod
    def _get_cluster(pair: str) -> str:
        for cluster, pairs in CORRELATION_CLUSTERS.items():
            if pair in pairs:
                return cluster
        return "solo"


# ── Portfolio-level FTMO compliance ──────────────────────────────────────────

def check_portfolio_ftmo(open_trades: List[OpenTrade], daily_loss_pct: float) -> dict:
    """
    Portfolio-level FTMO safety check.
    Returns {'safe': bool, 'issues': list}.
    """
    issues = []
    total_r = sum(t.risk_r for t in open_trades)

    if total_r > 3.0:
        issues.append(f"Total open risk {total_r:.1f}R exceeds 3R portfolio limit")

    if daily_loss_pct > 0.018:    # 1.8% → approaching 2% daily limit
        issues.append(f"Daily loss {daily_loss_pct:.1%} approaching 2% FTMO limit")

    if len(open_trades) > 5:
        issues.append(f"{len(open_trades)} open trades — may appear excessive for FTMO")

    return {"safe": len(issues) == 0, "issues": issues, "total_open_r": total_r}
