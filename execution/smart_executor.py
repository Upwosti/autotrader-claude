"""
SmartExecutor — pre-execution validation and slippage simulation.

Performs three pre-flight checks before an order is allowed to proceed:
  1. Spread guard  — rejects entries when the live spread is too wide.
  2. Slippage sim  — adjusts the entry price for realistic fill assumptions.
  3. News blackout — blocks entry within NEWS_BLACKOUT_MINUTES of high-impact news.

# MT5_PENDING: connect to live MT5 when login provided
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# Cost / spread reference (from backtester)
# ---------------------------------------------------------------------------
try:
    from backtester.costs import SPREAD_PIPS, PIP_SIZE
except ImportError:
    logger.warning(
        "SmartExecutor: could not import backtester.costs — using built-in fallbacks"
    )
    SPREAD_PIPS: Dict[str, float] = {
        "XAUUSD": 0.30, "XAGUSD": 0.35,
        "GBPUSD": 0.8,  "EURUSD": 0.6,
        "USDJPY": 0.7,  "USDCHF": 0.9,
        "AUDUSD": 0.9,  "NZDUSD": 1.0,
        "USDCAD": 1.0,  "EURJPY": 1.2,  "GBPJPY": 1.5,
        "BTCUSD": 15.0, "ETHUSD": 8.0,
        "NAS100": 1.0,  "US30": 2.0,    "GER40": 1.5,
    }
    PIP_SIZE: Dict[str, float] = {
        "XAUUSD": 0.10,  "XAGUSD": 0.01,
        "GBPUSD": 0.0001, "EURUSD": 0.0001,
        "USDJPY": 0.01,   "USDCHF": 0.0001,
        "AUDUSD": 0.0001, "NZDUSD": 0.0001,
        "USDCAD": 0.0001, "EURJPY": 0.01,  "GBPJPY": 0.01,
        "BTCUSD": 1.0,    "ETHUSD": 0.10,
        "NAS100": 1.0,    "US30": 1.0,    "GER40": 1.0,
    }

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SPREAD_LIMIT_MULTIPLIER: float = 2.0   # reject when live spread > normal × this
NEWS_BLACKOUT_MINUTES:   int   = 30    # minutes before/after high-impact news
_SLIPPAGE_PIPS:          float = 0.5  # adverse slippage per fill


class SmartExecutor:
    """
    Pre-execution validator for a single trading pair.

    Parameters
    ----------
    pair     : Instrument symbol (e.g. "XAUUSD").
    telegram : Optional notifier with a .send(msg: str) method.
    """

    def __init__(self, pair: str, telegram=None) -> None:
        self.pair     = pair
        self.telegram = telegram
        logger.info(f"SmartExecutor initialised for {pair}")

    # ------------------------------------------------------------------
    # Spread check
    # ------------------------------------------------------------------

    def check_spread(
        self,
        current_spread_pips: float,
    ) -> Tuple[bool, str]:
        """
        Verify the live spread is within acceptable limits.

        Returns (ok: bool, reason: str).
        A spread more than SPREAD_LIMIT_MULTIPLIER × normal is rejected.
        """
        normal_spread = SPREAD_PIPS.get(self.pair, 1.0)
        limit         = normal_spread * SPREAD_LIMIT_MULTIPLIER

        if current_spread_pips > limit:
            reason = (
                f"Spread too wide for {self.pair}: "
                f"{current_spread_pips:.2f} pips vs normal {normal_spread:.2f} pips "
                f"(limit {limit:.2f} pips)"
            )
            logger.warning(f"SmartExecutor.check_spread: {reason}")
            return False, reason

        return True, f"Spread ok ({current_spread_pips:.2f} vs {normal_spread:.2f} normal)"

    # ------------------------------------------------------------------
    # Slippage simulation
    # ------------------------------------------------------------------

    def simulate_slippage(self, entry: float, direction: str) -> float:
        """
        Adjust the entry price for realistic adverse slippage (0.5 pips).

        For long entries the fill price is slightly higher; for short entries
        it is slightly lower.

        Returns the adjusted entry price.
        """
        pip   = PIP_SIZE.get(self.pair, 0.0001)
        shift = _SLIPPAGE_PIPS * pip

        if direction.lower() in ("long", "buy"):
            adjusted = entry + shift
        else:
            adjusted = entry - shift

        logger.debug(
            f"SmartExecutor.simulate_slippage [{self.pair}]: "
            f"{direction} entry {entry} → {adjusted:.6f} "
            f"({_SLIPPAGE_PIPS} pip adverse)"
        )
        return adjusted

    # ------------------------------------------------------------------
    # News blackout check
    # ------------------------------------------------------------------

    def check_news_blackout(self, dt: Optional[datetime] = None) -> bool:
        """
        Return True if *dt* falls within a high-impact news blackout window.

        Delegates to strategy.news_filter.is_news_blackout when the module
        is available.  Falls back to False (no block) on import failure.

        Parameters
        ----------
        dt : datetime to test (defaults to now in UTC).
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        try:
            from strategy.news_filter import is_news_blackout  # type: ignore
            return bool(is_news_blackout(dt))
        except ImportError:
            logger.debug(
                "SmartExecutor.check_news_blackout: strategy.news_filter not "
                "available — news blackout check skipped"
            )
            return False
        except Exception as exc:
            logger.warning(f"SmartExecutor.check_news_blackout error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Combined validation
    # ------------------------------------------------------------------

    def validate_entry(
        self,
        entry:               float,
        sl:                  float,
        direction:           str,
        spread_pips:         Optional[float] = None,
        dt:                  Optional[datetime] = None,
    ) -> Dict:
        """
        Run all pre-entry checks and return a consolidated result dict.

        Parameters
        ----------
        entry        : Proposed entry price.
        sl           : Stop-loss price.
        direction    : "long" / "buy" or "short" / "sell".
        spread_pips  : Current live spread in pips.  If None, spread check is
                       skipped (assumed ok).
        dt           : Datetime for the news-blackout check (defaults to now).

        Returns
        -------
        dict with keys:
            valid          : bool  — True if all checks pass
            reason         : str   — human-readable summary
            adjusted_entry : float — entry after slippage simulation
            spread_ok      : bool
            news_ok        : bool
        """
        issues:     list[str] = []
        spread_ok:  bool      = True
        news_ok:    bool      = True

        # ── Spread guard ─────────────────────────────────────────────────
        if spread_pips is not None:
            spread_ok, spread_msg = self.check_spread(spread_pips)
            if not spread_ok:
                issues.append(spread_msg)
        else:
            spread_msg = "spread check skipped (no live spread provided)"
            logger.debug(f"SmartExecutor [{self.pair}]: {spread_msg}")

        # ── News blackout ────────────────────────────────────────────────
        in_blackout = self.check_news_blackout(dt)
        if in_blackout:
            news_ok = False
            issues.append(
                f"News blackout active within {NEWS_BLACKOUT_MINUTES} min "
                "of high-impact release"
            )

        # ── Basic SL sanity ──────────────────────────────────────────────
        if sl == entry:
            issues.append("SL equals entry price — invalid setup")

        # ── Slippage-adjusted entry ──────────────────────────────────────
        adjusted_entry = self.simulate_slippage(entry, direction)

        valid  = len(issues) == 0
        reason = "; ".join(issues) if issues else "all pre-entry checks passed"

        if not valid:
            logger.warning(f"SmartExecutor.validate_entry [{self.pair}] BLOCKED: {reason}")
        else:
            logger.info(
                f"SmartExecutor.validate_entry [{self.pair}] OK — "
                f"adjusted_entry={adjusted_entry:.6f}"
            )

        return {
            "valid":          valid,
            "reason":         reason,
            "adjusted_entry": adjusted_entry,
            "spread_ok":      spread_ok,
            "news_ok":        news_ok,
        }

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    def retry_logic(
        self,
        func,
        max_retries: int   = 3,
        delay:       float = 1.0,
    ):
        """
        Call *func* up to *max_retries* times with exponential back-off.

        Raises the last exception if all attempts fail.

        # MT5_PENDING: use for live order placement retries.

        Parameters
        ----------
        func        : Zero-argument callable to retry.
        max_retries : Total number of attempts.
        delay       : Base delay in seconds (doubles each retry).
        """
        last_exc: Optional[Exception] = None
        wait      = delay

        for attempt in range(1, max_retries + 1):
            try:
                result = func()
                if attempt > 1:
                    logger.info(
                        f"SmartExecutor.retry_logic: succeeded on attempt {attempt}"
                    )
                return result

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"SmartExecutor.retry_logic: attempt {attempt}/{max_retries} "
                    f"failed — {exc}. "
                    + (f"Retrying in {wait:.1f}s …" if attempt < max_retries else "No more retries.")
                )
                if attempt < max_retries:
                    time.sleep(wait)
                    wait *= 2   # exponential back-off

        raise RuntimeError(
            f"retry_logic: all {max_retries} attempts failed. "
            f"Last error: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"SmartExecutor(pair={self.pair})"
