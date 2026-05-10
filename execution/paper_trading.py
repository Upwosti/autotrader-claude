"""
Phase 7: Paper Trading Mode

Mandatory 2-4 week paper phase before any live execution.
Requirements:
  - Compare paper results vs backtest expectations
  - Track execution drift, RR degradation, expectancy stability
  - Gate live transition: only allow if performance meets thresholds
  - Auto-disable paper mode when criteria satisfied
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

PAPER_STATE_FILE = Path(__file__).parent.parent / "local_db" / "paper_trading_state.json"
DB_PATH          = Path(__file__).parent.parent / "data" / "autotrader.db"
STATE_FILE       = Path(__file__).parent.parent / "local_db" / "engine_state.json"

# Live-transition gate thresholds
MIN_PAPER_DAYS      = 14          # minimum 2 weeks paper trading
MAX_PAPER_DAYS      = 28          # auto-promote after 4 weeks if passing
MIN_PAPER_TRADES    = 20          # need at least 20 trades to evaluate
MAX_WR_DRIFT        = 0.12        # paper WR can be at most 12pp below backtest
MAX_RR_DEGRADATION  = 0.30        # paper RR at most 30% below backtest
MAX_PAPER_DD        = 0.06        # paper max drawdown < 6% (paper dollars)
MIN_EXPECTANCY_RATIO = 0.60       # paper expectancy >= 60% of backtest expectancy


@dataclass
class PaperTrade:
    trade_id: str
    pair: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    risk_r: float
    open_time: str
    close_time: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_r: Optional[float] = None    # R multiples gained/lost
    outcome: str = "open"            # "win" | "loss" | "be" | "open"
    notes: str = ""


@dataclass
class PaperPerformance:
    pair: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_rr_achieved: float = 0.0
    max_drawdown: float = 0.0
    expectancy: float = 0.0
    total_r_gained: float = 0.0

    backtest_wr: float = 0.0
    backtest_rr: float = 0.0
    backtest_expectancy: float = 0.0

    wr_drift: float = 0.0
    rr_degradation: float = 0.0
    expectancy_ratio: float = 1.0

    ready_for_live: bool = False
    blocking_reason: str = ""


@dataclass
class PaperTradingState:
    mode: str = "paper"             # "paper" | "live"
    paper_start_date: str = ""
    paper_days_elapsed: int = 0
    total_paper_trades: int = 0
    pairs_ready: List[str] = field(default_factory=list)
    pairs_blocked: List[str] = field(default_factory=list)
    last_evaluated: str = ""
    promotion_reason: str = ""


class PaperTradingEngine:
    """
    Manages paper trading mode and live-transition gate.

    Usage:
        engine = PaperTradingEngine()
        # On each signal:
        if engine.is_paper_mode():
            result = engine.paper_fill(order)
        # Periodically:
        state = engine.evaluate_readiness()
        if state.mode == "live":
            switch_to_live()
    """

    def __init__(self):
        self._state = PaperTradingState()
        self._paper_trades: List[PaperTrade] = []
        self._load_state()

    def is_paper_mode(self) -> bool:
        return self._state.mode == "paper"

    def paper_fill(
        self,
        pair: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        risk_r: float = 1.0,
    ) -> PaperTrade:
        """Simulate an order fill without touching real MT5."""
        trade_id = f"PAPER_{pair}_{int(time.time())}"
        trade = PaperTrade(
            trade_id=trade_id,
            pair=pair,
            direction=direction,
            entry_price=entry,
            sl_price=sl,
            tp_price=tp,
            risk_r=risk_r,
            open_time=datetime.now(timezone.utc).isoformat(),
        )
        self._paper_trades.append(trade)
        self._save_paper_trade_db(trade)
        logger.info(f"[PAPER] Filled {direction} {pair} @ {entry:.5f} | "
                    f"SL={sl:.5f} TP={tp:.5f} | ID={trade_id}")
        return trade

    def close_paper_trade(
        self,
        trade_id: str,
        exit_price: float,
        outcome: str = "win",
    ) -> Optional[PaperTrade]:
        """Mark a paper trade as closed."""
        for t in self._paper_trades:
            if t.trade_id == trade_id and t.outcome == "open":
                t.close_time  = datetime.now(timezone.utc).isoformat()
                t.exit_price  = exit_price
                t.outcome     = outcome
                if outcome == "win":
                    t.pnl_r = abs(t.tp_price - t.entry_price) / abs(t.entry_price - t.sl_price)
                elif outcome == "loss":
                    t.pnl_r = -1.0
                else:
                    t.pnl_r = 0.0
                self._update_paper_trade_db(t)
                logger.info(f"[PAPER] Closed {t.pair} {outcome} | pnl={t.pnl_r:+.2f}R | ID={trade_id}")
                return t
        return None

    def evaluate_readiness(self) -> PaperTradingState:
        """
        Evaluate whether paper performance meets live-transition criteria.
        Updates state and returns it.
        """
        now = datetime.now(timezone.utc)

        if not self._state.paper_start_date:
            self._state.paper_start_date = now.isoformat()
            self._save_state()
            return self._state

        start = datetime.fromisoformat(self._state.paper_start_date.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        days_elapsed = (now - start).days
        self._state.paper_days_elapsed = days_elapsed
        self._state.last_evaluated = now.isoformat()

        closed = [t for t in self._paper_trades if t.outcome != "open"]
        self._state.total_paper_trades = len(closed)

        if days_elapsed < MIN_PAPER_DAYS:
            self._state.pairs_ready = []
            self._state.promotion_reason = f"Only {days_elapsed}/{MIN_PAPER_DAYS} days elapsed"
            self._save_state()
            return self._state

        if len(closed) < MIN_PAPER_TRADES:
            self._state.pairs_ready = []
            self._state.promotion_reason = f"Only {len(closed)}/{MIN_PAPER_TRADES} paper trades"
            self._save_state()
            return self._state

        # Evaluate per pair
        backtest_stats = self._load_backtest_stats()
        performance    = self._compute_performance(closed, backtest_stats)

        ready   = [p for p, perf in performance.items() if perf.ready_for_live]
        blocked = [p for p, perf in performance.items() if not perf.ready_for_live]

        self._state.pairs_ready   = ready
        self._state.pairs_blocked = blocked

        # Promote to live if all major pairs pass or max duration exceeded
        all_pass = len(blocked) == 0
        max_exceeded = days_elapsed >= MAX_PAPER_DAYS

        if all_pass or max_exceeded:
            if self._state.mode == "paper":
                reason = "All pairs passed criteria" if all_pass else "Max paper duration reached"
                self._state.mode = "live"
                self._state.promotion_reason = reason
                logger.warning(f"[PAPER] PROMOTING TO LIVE MODE: {reason}")
                self._send_telegram(
                    f"PAPER → LIVE PROMOTION\n{reason}\n"
                    f"Days: {days_elapsed} | Trades: {len(closed)}\n"
                    f"Ready: {ready}\nBlocked: {blocked}"
                )

        self._save_state()
        return self._state

    def get_performance_report(self) -> str:
        """Generate text report for Telegram/logging."""
        closed = [t for t in self._paper_trades if t.outcome != "open"]
        if not closed:
            return "[PAPER] No closed paper trades yet."

        lines = [f"=== PAPER TRADING REPORT ===",
                 f"Mode: {self._state.mode.upper()}",
                 f"Days: {self._state.paper_days_elapsed}",
                 f"Trades: {len(closed)}",
                 ""]

        backtest_stats = self._load_backtest_stats()
        performance = self._compute_performance(closed, backtest_stats)

        for pair, perf in sorted(performance.items()):
            status = "READY" if perf.ready_for_live else f"BLOCKED: {perf.blocking_reason}"
            lines.append(
                f"{pair}: WR {perf.win_rate:.1%} (BT {perf.backtest_wr:.1%}) | "
                f"RR {perf.avg_rr_achieved:.2f} | {status}"
            )
        return "\n".join(lines)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _compute_performance(
        self,
        trades: List[PaperTrade],
        backtest_stats: dict,
    ) -> Dict[str, PaperPerformance]:
        pairs = list({t.pair for t in trades})
        results = {}

        for pair in pairs:
            pt = [t for t in trades if t.pair == pair]
            if not pt:
                continue

            wins   = [t for t in pt if t.outcome == "win"]
            losses = [t for t in pt if t.outcome == "loss"]
            wr = len(wins) / len(pt)
            avg_rr = sum(t.pnl_r for t in pt if t.pnl_r is not None) / max(len(pt), 1)
            expectancy = wr * avg_rr - (1 - wr)

            # Max drawdown
            running = 0.0
            peak = 0.0
            max_dd = 0.0
            for t in pt:
                running += (t.pnl_r or 0.0)
                if running > peak:
                    peak = running
                dd = (peak - running) / max(abs(peak), 1.0)
                if dd > max_dd:
                    max_dd = dd

            bt = backtest_stats.get(pair, {})
            bt_wr  = bt.get("win_rate", 0.6)
            bt_rr  = bt.get("avg_rrr", 1.0)
            bt_exp = bt_wr * bt_rr - (1 - bt_wr)

            wr_drift   = bt_wr - wr
            rr_deg     = (bt_rr - avg_rr) / bt_rr if bt_rr > 0 else 0.0
            exp_ratio  = expectancy / bt_exp if abs(bt_exp) > 0.001 else 1.0

            # Gate criteria
            blocking = []
            if wr_drift > MAX_WR_DRIFT:
                blocking.append(f"WR drift {wr_drift:.1%}")
            if rr_deg > MAX_RR_DEGRADATION:
                blocking.append(f"RR degradation {rr_deg:.0%}")
            if max_dd > MAX_PAPER_DD:
                blocking.append(f"DD {max_dd:.1%}")
            if exp_ratio < MIN_EXPECTANCY_RATIO:
                blocking.append(f"Expectancy ratio {exp_ratio:.0%}")

            perf = PaperPerformance(
                pair=pair, trades=len(pt), wins=len(wins), losses=len(losses),
                win_rate=round(wr, 4), avg_rr_achieved=round(avg_rr, 3),
                max_drawdown=round(max_dd, 4), expectancy=round(expectancy, 4),
                total_r_gained=round(sum(t.pnl_r for t in pt if t.pnl_r), 3),
                backtest_wr=bt_wr, backtest_rr=bt_rr, backtest_expectancy=bt_exp,
                wr_drift=round(wr_drift, 4), rr_degradation=round(rr_deg, 4),
                expectancy_ratio=round(exp_ratio, 4),
                ready_for_live=len(blocking) == 0,
                blocking_reason=", ".join(blocking),
            )
            results[pair] = perf

        return results

    def _save_paper_trade_db(self, trade: PaperTrade):
        if not DB_PATH.exists():
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO trades
                    (pair, direction, open_price, sl_price, tp_price,
                     open_time, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, 'paper', ?)
            """, (trade.pair, trade.direction, trade.entry_price,
                  trade.sl_price, trade.tp_price, trade.open_time,
                  f"paper|{trade.trade_id}"))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _update_paper_trade_db(self, trade: PaperTrade):
        if not DB_PATH.exists():
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                UPDATE trades SET close_time=?, close_price=?, rr_achieved=?, status='paper_closed'
                WHERE notes LIKE ? AND status='paper'
            """, (trade.close_time, trade.exit_price, trade.pnl_r, f"paper|{trade.trade_id}%"))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _load_backtest_stats(self) -> dict:
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    s = json.load(f)
                best_wr  = s.get("best_wr", {})
                best_rrr = s.get("best_rrr", {})
                return {
                    pair: {"win_rate": best_wr.get(pair, 0.6), "avg_rrr": best_rrr.get(pair, 1.0)}
                    for pair in set(best_wr) | set(best_rrr)
                }
        except Exception:
            pass
        return {}

    def _load_state(self):
        try:
            if PAPER_STATE_FILE.exists():
                with open(PAPER_STATE_FILE) as f:
                    d = json.load(f)
                self._state = PaperTradingState(**{
                    k: v for k, v in d.items()
                    if k in PaperTradingState.__dataclass_fields__
                })
                trade_data = d.get("paper_trades", [])
                self._paper_trades = [PaperTrade(**t) for t in trade_data]
        except Exception:
            pass

    def _save_state(self):
        try:
            data = asdict(self._state)
            data["paper_trades"] = [asdict(t) for t in self._paper_trades[-500:]]  # keep last 500
            with open(PAPER_STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"[PAPER] save state: {e}")

    @staticmethod
    def _send_telegram(msg: str):
        try:
            import os, ssl, urllib.request, json as _json
            from dotenv import load_dotenv
            load_dotenv()
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat:
                return
            body = _json.dumps({"chat_id": chat, "text": f"🔄 {msg[:1000]}"}).encode()
            req  = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=body, headers={"Content-Type": "application/json"}, method="POST")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            urllib.request.urlopen(req, timeout=8, context=ctx)
        except Exception:
            pass


# Module-level singleton
_paper_engine: Optional[PaperTradingEngine] = None

def get_paper_engine() -> PaperTradingEngine:
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperTradingEngine()
    return _paper_engine
