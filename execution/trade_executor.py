"""
Trade Executor — places trades via MT5 after all checks pass.
"""

from datetime import datetime
from loguru import logger
from config import ONE_TRADE_AT_A_TIME, RISK_PER_TRADE_PCT
from risk.position_sizer import calculate_lot_size


class TradeExecutor:
    def __init__(self, mt5, ftmo_guardian, news_manager, trade_logger):
        self.mt5           = mt5
        self.guardian      = ftmo_guardian
        self.news          = news_manager
        self.logger        = trade_logger
        self.active_trade  = None   # dict or None

    def can_trade(self, account_equity: float) -> tuple:
        """Returns (ok: bool, reason: str)."""
        if ONE_TRADE_AT_A_TIME and self.active_trade:
            return False, "Trade already active"
        if not self.guardian.check(account_equity):
            return False, f"FTMO halt: {self.guardian.halt_reason}"
        if not self.news.is_safe_to_trade():
            return False, "News block active"
        return True, ""

    def execute(self, setup: dict, account: dict) -> dict:
        """
        setup keys: symbol, direction, entry, sl, tp, confidence, pair
        account keys: balance, equity
        Returns order result dict.
        """
        equity   = account.get("equity", account.get("balance", 10000))
        ok, reason = self.can_trade(equity)
        if not ok:
            logger.warning(f"Trade blocked: {reason}")
            return {"status": "blocked", "reason": reason}

        symbol    = setup["symbol"]
        direction = setup["direction"]
        entry     = setup["entry"]
        sl        = setup["sl"]
        tp        = setup["tp"]

        lot = calculate_lot_size(
            account_balance=account.get("balance", 10000),
            risk_pct=RISK_PER_TRADE_PCT,
            entry=entry, sl=sl, symbol=symbol,
        )

        result = self.mt5.place_order(
            symbol=symbol, order_type=direction,
            volume=lot, price=entry, sl=sl, tp=tp,
            comment=f"AT_v{setup.get('version', 1)}_conf{setup.get('confidence', 0)}",
        )

        if result.get("retcode") == 10009 or result.get("simulated"):
            self.active_trade = {
                "symbol": symbol, "direction": direction,
                "entry": entry, "sl": sl, "tp": tp,
                "lot": lot, "ticket": result.get("order", 0),
                "opened_at": datetime.utcnow().isoformat(),
                "confidence": setup.get("confidence", 0),
            }
            logger.info(f"Trade opened: {direction} {symbol} {lot} lots @ {entry} "
                        f"SL={sl} TP={tp}")
            return {"status": "opened", "lot": lot, "ticket": result.get("order", 0)}
        else:
            logger.error(f"Order failed: {result}")
            return {"status": "failed", "result": result}

    def close_active(self) -> bool:
        if not self.active_trade:
            return False
        ticket = self.active_trade.get("ticket", 0)
        ok = self.mt5.close_position(ticket)
        if ok:
            logger.info(f"Active trade closed: ticket={ticket}")
            self.active_trade = None
        return ok
