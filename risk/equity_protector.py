"""
EquityProtector — automatic trading pause on drawdown limit breaches.

State is persisted to local_db/equity_state.json so pauses survive restarts.
Supports three breach tiers:
  - Daily   (3 %)  → 24-hour pause
  - Weekly  (5 %)  → 48-hour pause
  - Total   (8 %)  → permanent pause until manual reset
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# Drawdown limits
# ---------------------------------------------------------------------------
DAILY_LIMIT:  float = 0.03   # 3 %  of daily starting equity
WEEKLY_LIMIT: float = 0.05   # 5 %  of weekly starting equity
TOTAL_LIMIT:  float = 0.08   # 8 %  of peak equity

# Pause durations (in hours)
_DAILY_PAUSE_HOURS:  int = 24
_WEEKLY_PAUSE_HOURS: int = 48
_TOTAL_PAUSE_MARKER: str = "MANUAL_RESUME_REQUIRED"

# Path to persisted state
_STATE_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "local_db",
        "equity_state.json",
    )
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EquityProtector:
    """
    Guards trading capital by automatically pausing order flow when equity
    drawdown thresholds are breached.

    Parameters
    ----------
    telegram : optional Telegram notifier with a .send(msg: str) method.
    """

    def __init__(self, telegram=None) -> None:
        self.telegram = telegram
        self._load_state()
        logger.info(
            f"EquityProtector ready — peak={self._state['peak_equity']:.2f}, "
            f"paused={self.is_paused()}"
        )

    # ------------------------------------------------------------------
    # Primary update / check interface
    # ------------------------------------------------------------------

    def update_equity(self, current_equity: float) -> None:
        """
        Update equity tracking records.

        - Advances daily_start and weekly_start when the calendar rolls over.
        - Updates peak_equity when a new high-water mark is set.
        - Checks all drawdown limits and triggers a pause if any are breached.
        """
        now = _utcnow()

        # ── Roll over daily baseline ─────────────────────────────────────
        last_checked_str = self._state.get("last_checked")
        if last_checked_str:
            try:
                last_checked = datetime.fromisoformat(last_checked_str)
                if last_checked.date() < now.date():
                    self._state["daily_start"] = current_equity
                    logger.info(f"Daily baseline reset to {current_equity:.2f}")

                    # ── Roll over weekly baseline ────────────────────────
                    if last_checked.isocalendar()[1] != now.isocalendar()[1]:
                        self._state["weekly_start"] = current_equity
                        logger.info(f"Weekly baseline reset to {current_equity:.2f}")
            except (ValueError, TypeError):
                pass
        else:
            # First run — initialise baselines
            self._state["daily_start"]  = current_equity
            self._state["weekly_start"] = current_equity

        # ── Update peak ──────────────────────────────────────────────────
        if current_equity > self._state.get("peak_equity", current_equity):
            self._state["peak_equity"] = current_equity

        self._state["last_checked"] = now.isoformat()
        self._save_state()

        # ── Evaluate limits ──────────────────────────────────────────────
        self.check_and_pause(current_equity)

    def check_and_pause(self, current_equity: float) -> Tuple[bool, str]:
        """
        Evaluate all drawdown limits and pause trading if any are breached.

        Returns (should_pause: bool, reason: str).
        Auto-sends Telegram alert on new pause or auto-resume.
        """
        # ── Auto-resume check ────────────────────────────────────────────
        if self.is_paused():
            if self.try_resume():
                pass   # Telegram alert sent inside try_resume
            else:
                reason = self._state.get("pause_reason", "unknown")
                return True, f"Trading paused: {reason}"

        peak_equity   = self._state.get("peak_equity",   current_equity)
        daily_start   = self._state.get("daily_start",   current_equity)
        weekly_start  = self._state.get("weekly_start",  current_equity)

        # ── Total drawdown ───────────────────────────────────────────────
        if peak_equity > 0:
            total_dd = (peak_equity - current_equity) / peak_equity
            if total_dd >= TOTAL_LIMIT:
                reason = (
                    f"Total drawdown {total_dd * 100:.2f}% exceeds "
                    f"limit {TOTAL_LIMIT * 100:.1f}% — MANUAL RESUME REQUIRED"
                )
                self._trigger_pause(reason, duration_hours=None)
                return True, reason

        # ── Weekly drawdown ──────────────────────────────────────────────
        if weekly_start > 0:
            weekly_dd = (weekly_start - current_equity) / weekly_start
            if weekly_dd >= WEEKLY_LIMIT:
                reason = (
                    f"Weekly drawdown {weekly_dd * 100:.2f}% exceeds "
                    f"limit {WEEKLY_LIMIT * 100:.1f}%"
                )
                self._trigger_pause(reason, duration_hours=_WEEKLY_PAUSE_HOURS)
                return True, reason

        # ── Daily drawdown ───────────────────────────────────────────────
        if daily_start > 0:
            daily_dd = (daily_start - current_equity) / daily_start
            if daily_dd >= DAILY_LIMIT:
                reason = (
                    f"Daily drawdown {daily_dd * 100:.2f}% exceeds "
                    f"limit {DAILY_LIMIT * 100:.1f}%"
                )
                self._trigger_pause(reason, duration_hours=_DAILY_PAUSE_HOURS)
                return True, reason

        return False, "Within limits"

    # ------------------------------------------------------------------
    # Pause state accessors
    # ------------------------------------------------------------------

    def is_paused(self) -> bool:
        """Return True if trading should currently be paused."""
        paused_until_str = self._state.get("paused_until")
        if paused_until_str is None:
            return False
        if paused_until_str == _TOTAL_PAUSE_MARKER:
            return True
        try:
            paused_until = datetime.fromisoformat(paused_until_str)
            return _utcnow() < paused_until
        except (ValueError, TypeError):
            return False

    def get_pause_reason(self) -> str:
        """Return the human-readable reason for the current pause."""
        return self._state.get("pause_reason", "Not paused")

    def try_resume(self) -> bool:
        """
        Attempt to auto-resume after the pause duration has elapsed.

        Returns True if resumed, False if still within the pause window
        (or requires manual resume).
        """
        paused_until_str = self._state.get("paused_until")

        if paused_until_str is None:
            return False  # Not paused

        if paused_until_str == _TOTAL_PAUSE_MARKER:
            logger.warning("EquityProtector: total DD breach — manual resume required")
            return False

        try:
            paused_until = datetime.fromisoformat(paused_until_str)
        except (ValueError, TypeError):
            return False

        if _utcnow() >= paused_until:
            prev_reason = self._state.get("pause_reason", "unknown")
            self._state["paused_until"] = None
            self._state["pause_reason"] = ""
            self._save_state()
            msg = f"Trading AUTO-RESUMED after pause ({prev_reason})"
            logger.info(msg)
            self._send_alert(f"✅ {msg}")
            return True

        return False

    def manual_resume(self) -> None:
        """Manually clear any active pause (including total-DD pauses)."""
        self._state["paused_until"] = None
        self._state["pause_reason"] = ""
        self._save_state()
        msg = "Trading manually resumed by operator"
        logger.info(f"EquityProtector: {msg}")
        self._send_alert(f"✅ {msg}")

    # ------------------------------------------------------------------
    # Status snapshot
    # ------------------------------------------------------------------

    def status(self) -> Dict:
        """Return a summary of current equity-protection state."""
        current    = self._state.get("peak_equity", 0.0)   # best proxy we have
        peak       = self._state.get("peak_equity", 0.0)
        daily_s    = self._state.get("daily_start",  0.0)
        weekly_s   = self._state.get("weekly_start", 0.0)

        current_dd = (peak - current) / peak * 100   if peak   > 0 else 0.0
        daily_dd   = (daily_s  - current) / daily_s  * 100 if daily_s  > 0 else 0.0
        weekly_dd  = (weekly_s - current) / weekly_s * 100 if weekly_s > 0 else 0.0

        return {
            "paused":       self.is_paused(),
            "pause_reason": self.get_pause_reason(),
            "paused_until": self._state.get("paused_until"),
            "peak_equity":  peak,
            "current_dd_pct":  round(current_dd, 3),
            "daily_dd_pct":    round(daily_dd,   3),
            "weekly_dd_pct":   round(weekly_dd,  3),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _trigger_pause(self, reason: str, duration_hours: Optional[int]) -> None:
        """Set the pause state and persist it."""
        if duration_hours is None:
            # Total-DD breach: require manual resume
            self._state["paused_until"] = _TOTAL_PAUSE_MARKER
        else:
            resume_at = _utcnow() + timedelta(hours=duration_hours)
            self._state["paused_until"] = resume_at.isoformat()

        self._state["pause_reason"] = reason
        self._save_state()

        logger.critical(f"EquityProtector PAUSE: {reason}")
        self._send_alert(f"🚨 TRADING PAUSED: {reason}")

    def _send_alert(self, msg: str) -> None:
        """Send a Telegram notification if a notifier is configured."""
        if self.telegram is None:
            return
        try:
            self.telegram.send(msg)
        except Exception as exc:
            logger.warning(f"EquityProtector Telegram alert failed: {exc}")

    def _save_state(self) -> None:
        """Persist the current state dict to equity_state.json."""
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
        except Exception as exc:
            logger.error(f"EquityProtector._save_state failed: {exc}")

    def _load_state(self) -> None:
        """
        Load persisted state from equity_state.json.
        Initialises a fresh state dict if the file does not exist or is corrupt.
        """
        default: Dict = {
            "peak_equity":  0.0,
            "daily_start":  0.0,
            "weekly_start": 0.0,
            "paused_until": None,
            "pause_reason": "",
            "last_checked": None,
        }

        if _STATE_FILE.exists():
            try:
                with open(_STATE_FILE, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                # Merge loaded values into default (ensures all keys present)
                default.update(loaded)
                logger.debug(f"EquityProtector state loaded from {_STATE_FILE}")
            except Exception as exc:
                logger.warning(
                    f"EquityProtector: could not read state file ({exc}), "
                    "starting fresh"
                )
        else:
            logger.info(
                f"EquityProtector: no state file at {_STATE_FILE}, "
                "starting fresh"
            )

        self._state = default

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"EquityProtector(paused={self.is_paused()}, "
            f"peak={self._state.get('peak_equity', 0):.2f})"
        )
