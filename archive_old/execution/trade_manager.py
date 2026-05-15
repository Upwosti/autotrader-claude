"""
Trade Manager — monitors open positions: trailing stop, partial close, BE move.
"""

from loguru import logger


class TradeManager:
    def __init__(self, mt5, trade_executor):
        self.mt5      = mt5
        self.executor = trade_executor

    def manage(self, account: dict):
        """Called on each hourly scan to manage any open position."""
        trade = self.executor.active_trade
        if not trade:
            return

        positions = self.mt5.get_open_positions()
        live = next((p for p in positions if p["ticket"] == trade["ticket"]), None)

        if live is None:
            logger.info(f"Trade {trade['ticket']} no longer open — clearing")
            self.executor.active_trade = None
            return

        self._check_breakeven(live, trade)

    def _check_breakeven(self, live: dict, trade: dict):
        entry = trade["entry"]
        sl    = trade["sl"]
        tp    = trade["tp"]
        price = live.get("open_price", entry)   # current unrealized

        risk     = abs(entry - sl)
        target_1r = entry + risk if trade["direction"] == "buy" else entry - risk

        # Move SL to breakeven after 1R reached
        if trade["direction"] == "buy" and price >= target_1r and sl < entry:
            logger.info(f"Moving SL to breakeven for {trade['symbol']}")
            trade["sl"] = entry + 0.0001   # small buffer
        elif trade["direction"] == "sell" and price <= target_1r and sl > entry:
            logger.info(f"Moving SL to breakeven for {trade['symbol']}")
            trade["sl"] = entry - 0.0001
