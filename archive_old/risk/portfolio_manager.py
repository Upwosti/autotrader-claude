"""
PortfolioManager — cross-pair exposure and correlation tracking.

Tracks open trades, enforces maximum pair limits, and prevents overexposure
to correlated instruments. Correlation matrix updated daily when price data
is available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Predefined correlation matrix (updated daily when market data is available)
# Tuple keys are canonical (pair_a, pair_b) — always looked up in both orders.
# ---------------------------------------------------------------------------
CORRELATION_MATRIX: Dict[Tuple[str, str], float] = {
    # Highly correlated (>0.8)
    ("XAUUSD", "GC=F"):     0.98,
    ("XAGUSD", "SI=F"):     0.97,
    ("GBPUSD", "GBPJPY"):   0.85,
    ("EURUSD", "EURJPY"):   0.82,
    ("BTCUSD", "ETHUSD"):   0.91,
    ("NAS100", "US30"):     0.88,
    # Moderately correlated (0.5–0.8)
    ("XAUUSD", "XAGUSD"):   0.75,
    ("EURUSD", "GBPUSD"):   0.72,
    ("AUDUSD", "NZDUSD"):   0.85,
    ("USDJPY", "EURJPY"):   0.65,
}

MAX_CORRELATED_PAIRS:  int   = 3
CORRELATION_THRESHOLD: float = 0.80


class PortfolioManager:
    """
    Tracks open trades across all pairs, enforces exposure limits, and monitors
    cross-instrument correlation to prevent over-concentration of risk.

    Parameters
    ----------
    max_open_pairs : int
        Maximum number of simultaneously open trade positions.
    max_risk_pct : float
        Maximum total portfolio risk in percent of account equity.
    """

    def __init__(
        self,
        max_open_pairs: int = 5,
        max_risk_pct: float = 5.0,
    ) -> None:
        self.max_open_pairs = max_open_pairs
        self.max_risk_pct   = max_risk_pct

        # {pair: {direction, entry, sl, size, opened_at}}
        self.open_trades: Dict[str, Dict] = {}

        # Live correlation dict — starts from the predefined matrix and may be
        # updated intra-day when fresh OHLCV data is available.
        self._correlation: Dict[Tuple[str, str], float] = dict(CORRELATION_MATRIX)

        logger.info(
            f"PortfolioManager initialised — max_pairs={max_open_pairs}, "
            f"max_risk={max_risk_pct}%"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_trade(
        self,
        pair:      str,
        direction: str,
        entry:     float,
        sl:        float,
        size:      float,
    ) -> bool:
        """
        Register a new open trade.

        Returns False without adding the trade if it would breach the
        max_open_pairs or max_risk_pct limits.  Returns True on success.
        """
        allowed, reason = self.can_open_trade(pair, direction)
        if not allowed:
            logger.warning(f"PortfolioManager.add_trade blocked for {pair}: {reason}")
            return False

        self.open_trades[pair] = {
            "direction": direction,
            "entry":     entry,
            "sl":        sl,
            "size":      size,
            "opened_at": datetime.utcnow().isoformat(),
        }
        logger.info(
            f"Trade registered — {pair} {direction} entry={entry} sl={sl} size={size}"
        )
        return True

    def remove_trade(self, pair: str) -> None:
        """Mark a trade as closed and remove it from the open-trades registry."""
        if pair in self.open_trades:
            self.open_trades.pop(pair)
            logger.info(f"Trade removed from portfolio — {pair}")
        else:
            logger.debug(f"remove_trade: {pair} not found in open_trades (already closed?)")

    def get_correlation(self, pair1: str, pair2: str) -> float:
        """
        Return the correlation coefficient between *pair1* and *pair2*.
        Checks both (pair1, pair2) and (pair2, pair1) orderings.
        Returns 0.0 if the pair combination is not known.
        """
        if pair1 == pair2:
            return 1.0
        corr = self._correlation.get((pair1, pair2))
        if corr is None:
            corr = self._correlation.get((pair2, pair1))
        return float(corr) if corr is not None else 0.0

    def count_correlated_open(self, pair: str) -> int:
        """
        Count how many currently-open trades have a correlation above
        CORRELATION_THRESHOLD with *pair* (excluding *pair* itself).
        """
        count = 0
        for open_pair in self.open_trades:
            if open_pair == pair:
                continue
            if self.get_correlation(pair, open_pair) >= CORRELATION_THRESHOLD:
                count += 1
        return count

    def can_open_trade(self, pair: str, direction: str) -> Tuple[bool, str]:
        """
        Validate whether a new trade on *pair* in *direction* is permitted.

        Checks (in order):
        1. Same-pair duplicate block
        2. Maximum open-pairs limit
        3. Maximum total risk limit
        4. Correlated-pairs limit (MAX_CORRELATED_PAIRS)

        Returns (allowed: bool, reason: str).
        """
        # 1. Same pair already open
        if pair in self.open_trades:
            existing_dir = self.open_trades[pair].get("direction", "")
            return (
                False,
                f"{pair} already open ({existing_dir}); close it before re-entering",
            )

        # 2. Max open pairs
        if len(self.open_trades) >= self.max_open_pairs:
            return (
                False,
                f"Max open pairs ({self.max_open_pairs}) already reached",
            )

        # 3. Total risk limit (simplified: each trade = 1 %)
        projected_risk = (len(self.open_trades) + 1) * 1.0
        if projected_risk > self.max_risk_pct:
            return (
                False,
                f"Adding {pair} would push total risk to {projected_risk:.1f}% "
                f"(limit {self.max_risk_pct}%)",
            )

        # 4. Correlation limit — count highly-correlated open trades
        correlated_count = self.count_correlated_open(pair)
        if correlated_count >= MAX_CORRELATED_PAIRS:
            return (
                False,
                f"{pair} has {correlated_count} highly-correlated trades already open "
                f"(limit {MAX_CORRELATED_PAIRS})",
            )

        return True, "ok"

    def get_total_risk_pct(self) -> float:
        """
        Simplified portfolio risk: each open trade is assumed to risk 1 % of equity.
        Override with real SL-based calculation when account equity is available.
        """
        return len(self.open_trades) * 1.0

    def update_correlation_from_data(
        self,
        price_data: Dict[str, pd.DataFrame],
    ) -> None:
        """
        Recompute Pearson correlations from daily closing prices and update the
        internal correlation dict.  Called once per day by the scheduler.

        *price_data* maps pair symbol → OHLCV DataFrame with at least a 'close'
        column and a DatetimeIndex.
        """
        pairs   = list(price_data.keys())
        updated = 0

        for i, p1 in enumerate(pairs):
            for p2 in pairs[i + 1:]:
                try:
                    df1 = price_data[p1]["close"].dropna()
                    df2 = price_data[p2]["close"].dropna()

                    # Align on common index
                    common = df1.index.intersection(df2.index)
                    if len(common) < 30:
                        logger.debug(
                            f"Skipping correlation {p1}/{p2}: only {len(common)} "
                            "common bars"
                        )
                        continue

                    r1 = df1.loc[common].pct_change().dropna()
                    r2 = df2.loc[common].pct_change().dropna()

                    # Re-align after pct_change drops first row
                    common2 = r1.index.intersection(r2.index)
                    corr = float(np.corrcoef(
                        r1.loc[common2].values,
                        r2.loc[common2].values,
                    )[0, 1])

                    if not np.isnan(corr):
                        self._correlation[(p1, p2)] = round(corr, 4)
                        updated += 1

                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        f"Correlation update failed for {p1}/{p2}: {exc}"
                    )

        logger.info(f"Correlation matrix updated — {updated} pair combinations refreshed")

    def summary(self) -> Dict:
        """
        Return a snapshot of portfolio state.

        Keys: open_trades, total_risk_pct, most_correlated_pair
        """
        most_correlated: Optional[str] = None
        max_score: float = 0.0

        for pair in self.open_trades:
            for other in self.open_trades:
                if other == pair:
                    continue
                score = self.get_correlation(pair, other)
                if score > max_score:
                    max_score        = score
                    most_correlated  = f"{pair}/{other} ({score:.2f})"

        return {
            "open_trades":        len(self.open_trades),
            "pairs":              list(self.open_trades.keys()),
            "total_risk_pct":     self.get_total_risk_pct(),
            "most_correlated_pair": most_correlated,
        }

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PortfolioManager(open={len(self.open_trades)}, "
            f"risk={self.get_total_risk_pct():.1f}%)"
        )
