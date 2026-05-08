"""
CorrelationFilter — pre-entry correlation check.

Thin decision layer called immediately before a trade is submitted.
Delegates correlation lookups to a PortfolioManager instance when one is
provided; falls back to the module-level CORRELATION_MATRIX otherwise.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from loguru import logger

# Inline reference — avoids circular imports when PortfolioManager is not wired
from risk.portfolio_manager import CORRELATION_MATRIX, CORRELATION_THRESHOLD

# Maximum number of highly-correlated open trades allowed in the *same*
# direction before a new trade is blocked.
_MAX_SAME_DIR_CORRELATED: int = 2


class CorrelationFilter:
    """
    Pre-entry gate that prevents over-accumulation of correlated exposure.

    Parameters
    ----------
    portfolio_manager : optional PortfolioManager
        When supplied, correlation lookups and open-trade data are sourced from
        the live portfolio.  When absent, the module uses the static
        CORRELATION_MATRIX and the *open_trades* dict passed to each method.
    """

    def __init__(self, portfolio_manager=None) -> None:
        self.pm = portfolio_manager

    # ------------------------------------------------------------------
    # Primary check
    # ------------------------------------------------------------------

    def check(
        self,
        pair:        str,
        direction:   str,
        open_trades: Dict[str, Dict],
    ) -> Tuple[bool, str]:
        """
        Decide whether a new trade on *pair* / *direction* is safe to enter.

        Rules (in order):
        1. Reject if *pair* is already open.
        2. Count highly-correlated (> CORRELATION_THRESHOLD) open trades in
           the *same* direction.  Reject if count >= _MAX_SAME_DIR_CORRELATED.

        Returns
        -------
        (allowed: bool, reason: str)
        """
        # ── Rule 1: duplicate pair ───────────────────────────────────────
        if pair in open_trades:
            reason = f"{pair} is already in open_trades — no duplicate allowed"
            self.log_correlation(pair, 1.0, allowed=False)
            return False, reason

        # ── Rule 2: correlated same-direction count ─────────────────────
        correlated_same_dir = 0
        for open_pair, trade_info in open_trades.items():
            corr = self._get_corr(pair, open_pair)
            if corr < CORRELATION_THRESHOLD:
                continue
            open_dir = trade_info.get("direction", "").lower()
            if open_dir == direction.lower():
                correlated_same_dir += 1

        score = self.get_correlation_score(pair, open_trades)

        if correlated_same_dir >= _MAX_SAME_DIR_CORRELATED:
            reason = (
                f"{pair} blocked — {correlated_same_dir} highly-correlated "
                f"{direction} trades already open "
                f"(limit {_MAX_SAME_DIR_CORRELATED})"
            )
            self.log_correlation(pair, score, allowed=False)
            return False, reason

        reason = (
            f"{pair} allowed — correlated same-dir count: {correlated_same_dir}, "
            f"score: {score:.2f}"
        )
        self.log_correlation(pair, score, allowed=True)
        return True, reason

    # ------------------------------------------------------------------
    # Correlation score
    # ------------------------------------------------------------------

    def get_correlation_score(
        self,
        pair:        str,
        open_trades: Dict[str, Dict],
    ) -> float:
        """
        Aggregate correlation risk for a proposed new trade.

        Computes the mean correlation between *pair* and all currently open
        trades (ignoring pairs with correlation below 0.3 as negligible).

        Returns
        -------
        float in [0.0, 1.0]
            0.0 = no correlation risk, 1.0 = maximum correlation.
        """
        if not open_trades:
            return 0.0

        correlations: list[float] = []
        for open_pair in open_trades:
            if open_pair == pair:
                continue
            corr = self._get_corr(pair, open_pair)
            if corr >= 0.3:
                correlations.append(corr)

        if not correlations:
            return 0.0

        return round(min(sum(correlations) / len(correlations), 1.0), 4)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_correlation(
        self,
        pair:    str,
        score:   float,
        allowed: bool,
    ) -> None:
        """Emit a structured log entry for the correlation check result."""
        verb   = "ALLOWED" if allowed else "BLOCKED"
        level  = "info"    if allowed else "warning"
        msg    = (
            f"CorrelationFilter [{verb}] {pair} "
            f"— correlation score: {score:.3f}"
        )
        getattr(logger, level)(msg)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_corr(self, pair1: str, pair2: str) -> float:
        """
        Resolve correlation coefficient for a pair tuple.

        Delegates to PortfolioManager when available; falls back to the
        module-level CORRELATION_MATRIX.
        """
        if self.pm is not None:
            try:
                return self.pm.get_correlation(pair1, pair2)
            except Exception as exc:
                logger.debug(
                    f"CorrelationFilter: PM.get_correlation failed "
                    f"({exc}), using static matrix"
                )

        # Static fallback
        corr = CORRELATION_MATRIX.get((pair1, pair2))
        if corr is None:
            corr = CORRELATION_MATRIX.get((pair2, pair1))
        return float(corr) if corr is not None else 0.0

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        src = "PortfolioManager" if self.pm else "static matrix"
        return f"CorrelationFilter(source={src})"
