"""
OrderManager — tracks and manages the full lifecycle of open orders.

Handles partial closes, trailing stops, break-even moves, and emergency
close signals in a broker-agnostic way.  All MT5 integration points are
marked # MT5_PENDING and stubbed out for paper-trading.

# MT5_PENDING: integrate with MT5 for live execution
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Risk constants
# ---------------------------------------------------------------------------
_EMERGENCY_CLOSE_DD_PCT: float = 0.02   # close if trade DD exceeds 2 % of equity
_PARTIAL_CLOSE_RATIO:    float = 0.50   # close 50 % of position at 1:1 RR
_TRAIL_ATR_MULTIPLIER:   float = 1.0    # trail SL by 1 ATR when in profit


class OrderManager:
    """
    Broker-agnostic order lifecycle manager.

    Maintains an in-memory registry of open orders and exposes helper methods
    for automated trade management (partial close, trailing stop, break-even,
    emergency close).

    Parameters
    ----------
    telegram : Optional notifier with a .send(msg: str) method.
    db       : Optional database / Supabase client for trade logging.
    """

    def __init__(self, telegram=None, db=None) -> None:
        self.telegram = telegram
        self.db       = db

        # {trade_id: {pair, direction, entry, sl, tp, size, partial_closed, be_moved}}
        self.open_orders: Dict[str, Dict] = {}

        logger.info("OrderManager initialised")

    # ------------------------------------------------------------------
    # Order registry
    # ------------------------------------------------------------------

    def add_order(
        self,
        trade_id:  str,
        pair:      str,
        direction: str,
        entry:     float,
        sl:        float,
        tp:        float,
        size:      float,
    ) -> None:
        """
        Register a newly opened order in the tracker.

        If a trade with the same *trade_id* already exists it is overwritten
        with a warning so state stays consistent.
        """
        if trade_id in self.open_orders:
            logger.warning(
                f"OrderManager.add_order: {trade_id} already tracked — overwriting"
            )

        self.open_orders[trade_id] = {
            "pair":           pair,
            "direction":      direction.lower(),
            "entry":          float(entry),
            "sl":             float(sl),
            "tp":             float(tp),
            "size":           float(size),
            "partial_closed": False,
            "be_moved":       False,
            "opened_at":      datetime.now(timezone.utc).isoformat(),
        }
        self.log_action(trade_id, "OPEN", f"{direction} {pair} entry={entry} sl={sl} tp={tp} size={size}")

    def get_order(self, trade_id: str) -> Optional[Dict]:
        """Return the order dict for *trade_id*, or None if not tracked."""
        return self.open_orders.get(trade_id)

    def close_order(self, trade_id: str) -> None:
        """Remove an order from the tracker (called after MT5 confirms close)."""
        if trade_id in self.open_orders:
            order = self.open_orders.pop(trade_id)
            self.log_action(trade_id, "CLOSED", f"removed from tracker — {order.get('pair')}")
        else:
            logger.debug(f"OrderManager.close_order: {trade_id} not found (already removed?)")

    # ------------------------------------------------------------------
    # Trade management — partial close
    # ------------------------------------------------------------------

    def check_partial_close(
        self,
        trade_id:      str,
        current_price: float,
    ) -> bool:
        """
        Trigger a 50 % partial close when 1:1 RR is reached.

        Also moves SL to break-even when the partial close fires.

        Returns True if a partial close should be executed (caller is
        responsible for placing the actual MT5 close order).
        """
        order = self._get_order_or_warn(trade_id)
        if order is None:
            return False

        if order["partial_closed"]:
            return False   # already done

        entry = order["entry"]
        sl    = order["sl"]
        risk  = abs(entry - sl)

        if risk <= 0:
            return False

        target_1r = (
            entry + risk if order["direction"] in ("long", "buy")
            else entry - risk
        )

        reached = (
            current_price >= target_1r
            if order["direction"] in ("long", "buy")
            else current_price <= target_1r
        )

        if reached:
            order["partial_closed"] = True
            order["be_moved"]       = True
            order["sl"]             = entry   # move to BE

            details = (
                f"1:1 RR reached at {current_price:.5f} "
                f"(target {target_1r:.5f}) — closing {_PARTIAL_CLOSE_RATIO * 100:.0f}%, "
                f"SL moved to BE ({entry:.5f})"
            )
            self.log_action(trade_id, "PARTIAL_CLOSE", details)

            # MT5_PENDING: mt5.close_partial(ticket, size * 0.5)
            return True

        return False

    # ------------------------------------------------------------------
    # Trailing stop
    # ------------------------------------------------------------------

    def check_trailing_stop(
        self,
        trade_id:      str,
        current_price: float,
        atr:           float,
    ) -> Optional[float]:
        """
        Trail the SL by 1 ATR when the position is in profit.

        Returns the new SL price if updated, else None.
        Only trails in the profitable direction — never moves SL against trade.
        """
        order = self._get_order_or_warn(trade_id)
        if order is None:
            return None

        if atr <= 0:
            return None

        entry    = order["entry"]
        sl       = order["sl"]
        trail_by = atr * _TRAIL_ATR_MULTIPLIER

        if order["direction"] in ("long", "buy"):
            if current_price <= entry:
                return None   # not in profit
            new_sl = current_price - trail_by
            if new_sl > sl:
                order["sl"] = new_sl
                self.log_action(
                    trade_id, "TRAIL_SL",
                    f"long trail: price={current_price:.5f} atr={atr:.5f} "
                    f"new_sl={new_sl:.5f}"
                )
                # MT5_PENDING: mt5.modify_sl(ticket, new_sl)
                return new_sl

        else:  # short / sell
            if current_price >= entry:
                return None
            new_sl = current_price + trail_by
            if new_sl < sl:
                order["sl"] = new_sl
                self.log_action(
                    trade_id, "TRAIL_SL",
                    f"short trail: price={current_price:.5f} atr={atr:.5f} "
                    f"new_sl={new_sl:.5f}"
                )
                # MT5_PENDING: mt5.modify_sl(ticket, new_sl)
                return new_sl

        return None

    # ------------------------------------------------------------------
    # Break-even
    # ------------------------------------------------------------------

    def check_breakeven(
        self,
        trade_id:      str,
        current_price: float,
    ) -> Optional[float]:
        """
        Move SL to entry (break-even) when 1:1 RR is reached, if not already done.

        Returns the BE price if the move was triggered, else None.
        """
        order = self._get_order_or_warn(trade_id)
        if order is None:
            return None

        if order["be_moved"]:
            return None   # already at BE (or further)

        entry = order["entry"]
        sl    = order["sl"]
        risk  = abs(entry - sl)

        if risk <= 0:
            return None

        target_1r = (
            entry + risk if order["direction"] in ("long", "buy")
            else entry - risk
        )

        reached = (
            current_price >= target_1r
            if order["direction"] in ("long", "buy")
            else current_price <= target_1r
        )

        if reached:
            order["sl"]       = entry
            order["be_moved"] = True
            self.log_action(
                trade_id, "BREAKEVEN",
                f"SL moved to entry ({entry:.5f}) at price {current_price:.5f}"
            )
            # MT5_PENDING: mt5.modify_sl(ticket, entry)
            return entry

        return None

    # ------------------------------------------------------------------
    # Emergency close
    # ------------------------------------------------------------------

    def check_emergency_close(
        self,
        trade_id:      str,
        current_price: float,
        equity:        float,
    ) -> bool:
        """
        Signal an emergency close if the trade's floating loss exceeds
        2 % of current equity.

        Returns True if emergency close should be executed; the caller must
        place the actual MT5 close.
        """
        order = self._get_order_or_warn(trade_id)
        if order is None:
            return False

        entry    = order["entry"]
        size     = order["size"]
        direction = order["direction"]

        # Rough P&L estimation (price × size; sign varies by instrument type)
        if direction in ("long", "buy"):
            floating_pnl = (current_price - entry) * size
        else:
            floating_pnl = (entry - current_price) * size

        if floating_pnl >= 0:
            return False   # in profit — no emergency

        loss_pct = abs(floating_pnl) / equity if equity > 0 else 0.0

        if loss_pct >= _EMERGENCY_CLOSE_DD_PCT:
            details = (
                f"Emergency close triggered: floating loss "
                f"{loss_pct * 100:.2f}% of equity ({abs(floating_pnl):.2f} USD) "
                f"exceeds {_EMERGENCY_CLOSE_DD_PCT * 100:.1f}% limit"
            )
            self.log_action(trade_id, "EMERGENCY_CLOSE", details)
            if self.telegram:
                try:
                    self.telegram.send(f"🚨 EMERGENCY CLOSE — {order['pair']}: {details}")
                except Exception as exc:
                    logger.warning(f"OrderManager Telegram alert failed: {exc}")
            # MT5_PENDING: mt5.close_position(ticket)
            return True

        return False

    # ------------------------------------------------------------------
    # Logging / summary
    # ------------------------------------------------------------------

    def log_action(
        self,
        trade_id: str,
        action:   str,
        details:  str,
    ) -> None:
        """
        Write a structured log entry to loguru and optionally to the database.

        Parameters
        ----------
        trade_id : Unique trade identifier.
        action   : Action label (e.g. "OPEN", "PARTIAL_CLOSE", "TRAIL_SL").
        details  : Human-readable detail string.
        """
        msg = f"[OrderManager] {action} | {trade_id} | {details}"
        logger.info(msg)

        if self.db is not None:
            try:
                self.db.table("trade_events").insert({
                    "trade_id":  trade_id,
                    "action":    action,
                    "details":   details,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as exc:
                logger.warning(f"OrderManager.log_action DB write failed: {exc}")

    def summary(self) -> Dict:
        """
        Return a high-level snapshot of all tracked orders.

        Keys: open_count, pairs_open, total_size
        """
        pairs      = [o["pair"]  for o in self.open_orders.values()]
        total_size = sum(o["size"] for o in self.open_orders.values())

        return {
            "open_count": len(self.open_orders),
            "pairs_open": pairs,
            "total_size": round(total_size, 4),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_order_or_warn(self, trade_id: str) -> Optional[Dict]:
        """Fetch an order dict by ID, logging a warning if not found."""
        order = self.open_orders.get(trade_id)
        if order is None:
            logger.warning(
                f"OrderManager: trade_id '{trade_id}' not found in open_orders"
            )
        return order

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"OrderManager(open_orders={len(self.open_orders)})"
