"""
FTMO Guardian — enforces drawdown and daily loss limits.
Checked every 15 minutes by the scheduler.
"""

from loguru import logger
from config import MAX_DAILY_LOSS_PCT, MAX_TOTAL_DD_PCT


class FTMOGuardian:
    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance  = initial_balance
        self.day_start_balance = initial_balance
        self.trading_halted   = False
        self.halt_reason      = ""

    def update_day_start(self, balance: float):
        self.day_start_balance = balance

    def check(self, current_equity: float) -> bool:
        """
        Returns True if trading is allowed, False if limits breached.
        Call this before every trade attempt.
        """
        if self.trading_halted:
            return False

        daily_dd = (self.day_start_balance - current_equity) / self.day_start_balance * 100
        total_dd = (self.initial_balance - current_equity) / self.initial_balance * 100

        if daily_dd >= MAX_DAILY_LOSS_PCT:
            self._halt(f"Daily loss limit {MAX_DAILY_LOSS_PCT}% hit (current: {daily_dd:.2f}%)")
            return False

        if total_dd >= MAX_TOTAL_DD_PCT:
            self._halt(f"Total drawdown limit {MAX_TOTAL_DD_PCT}% hit (current: {total_dd:.2f}%)")
            return False

        return True

    def _halt(self, reason: str):
        self.trading_halted = True
        self.halt_reason    = reason
        logger.critical(f"FTMO GUARDIAN HALT: {reason}")

    def reset_daily(self, balance: float):
        self.day_start_balance = balance
        logger.info(f"FTMO daily reset — new day start balance: {balance}")

    def status(self) -> dict:
        return {
            "halted":      self.trading_halted,
            "halt_reason": self.halt_reason,
            "initial":     self.initial_balance,
            "day_start":   self.day_start_balance,
        }
