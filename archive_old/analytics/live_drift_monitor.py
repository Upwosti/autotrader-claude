"""
Phase 3: Live vs Backtest Drift Monitor

Tracks divergence between live execution results and backtest expectations.
Detects:
  - WR drift (live WR < backtest WR by more than threshold)
  - RR drift (live RR < backtest RR)
  - Expectancy drift
  - Slippage creep
  - Spread inflation

Actions on excessive drift:
  - Reduce risk to 50% automatically
  - Flag pair for retraining
  - Send Telegram warning
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

STATE_FILE  = Path(__file__).parent.parent / "local_db" / "engine_state.json"
DB_PATH     = Path(__file__).parent.parent / "data" / "autotrader.db"
DRIFT_FILE  = Path(__file__).parent.parent / "local_db" / "drift_state.json"

# Thresholds
WR_DRIFT_WARN      = 0.08    # warn  if live WR is 8pp below backtest
WR_DRIFT_ACTION    = 0.15    # act   if live WR is 15pp below backtest
RR_DRIFT_WARN      = 0.20    # warn  if live RR is 20% below backtest
RR_DRIFT_ACTION    = 0.35    # act   if live RR is 35% below backtest
EXPECTANCY_DRIFT   = 0.40    # act   if expectancy is 40% below backtest
MIN_LIVE_TRADES    = 20      # need at least 20 live trades before evaluating drift


@dataclass
class DriftReport:
    pair: str
    backtest_wr: float
    live_wr: float
    wr_drift: float               # positive = live is worse

    backtest_rr: float
    live_rr: float
    rr_drift_pct: float           # positive = live is worse

    backtest_expectancy: float
    live_expectancy: float
    expectancy_drift_pct: float

    avg_slippage_pips: float
    avg_spread_pips: float

    status: str                   # "ok" | "warn" | "action_required"
    risk_multiplier: float = 1.0  # 1.0 = normal, 0.5 = halved due to drift
    retrain_flagged: bool  = False
    live_trades: int       = 0
    notes: str             = ""


class LiveDriftMonitor:
    """
    Monitors live vs backtest performance drift per pair.
    Called periodically (e.g., every 2 hours) by the scheduler.
    """

    def __init__(self):
        self._risk_overrides: Dict[str, float] = {}
        self._load_drift_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate_all_pairs(self) -> Dict[str, DriftReport]:
        """
        Evaluate drift for all pairs that have live trade history.
        Returns {pair: DriftReport}.
        """
        backtest_stats = self._load_backtest_stats()
        live_stats     = self._load_live_stats()

        reports = {}
        for pair, live in live_stats.items():
            if live["trades"] < MIN_LIVE_TRADES:
                continue
            bt = backtest_stats.get(pair, {})
            if not bt:
                continue
            report = self._compute_drift(pair, bt, live)
            reports[pair] = report
            self._handle_drift_actions(report)

        self._save_drift_state(reports)
        return reports

    def get_risk_multiplier(self, pair: str) -> float:
        """Returns current risk multiplier for pair (1.0 normal, 0.5 halved)."""
        return self._risk_overrides.get(pair, 1.0)

    def generate_report_text(self, reports: Dict[str, DriftReport]) -> str:
        """Format drift report for Telegram/email."""
        lines = ["=== LIVE DRIFT REPORT ===", f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC", ""]
        for pair, r in sorted(reports.items()):
            status_icon = "✅" if r.status == "ok" else ("⚠️" if r.status == "warn" else "🚨")
            lines.append(f"{status_icon} {pair}: WR {r.live_wr:.1%} (BT {r.backtest_wr:.1%}) | "
                         f"RR {r.live_rr:.2f} (BT {r.backtest_rr:.2f}) | "
                         f"Risk x{r.risk_multiplier:.1f} | Trades: {r.live_trades}")
            if r.notes:
                lines.append(f"   → {r.notes}")
        return "\n".join(lines)

    # ── Core computation ──────────────────────────────────────────────────────

    def _compute_drift(self, pair: str, bt: dict, live: dict) -> DriftReport:
        bt_wr    = bt.get("win_rate", 0.6)
        bt_rr    = bt.get("avg_rrr", 1.0)
        live_wr  = live.get("win_rate", 0.0)
        live_rr  = live.get("avg_rrr", 0.0)

        wr_drift  = bt_wr - live_wr
        rr_drift  = (bt_rr - live_rr) / bt_rr if bt_rr > 0 else 0.0

        bt_exp    = bt_wr * bt_rr - (1 - bt_wr)
        live_exp  = live_wr * live_rr - (1 - live_wr)
        exp_drift = (bt_exp - live_exp) / abs(bt_exp) if abs(bt_exp) > 0.001 else 0.0

        avg_slip  = live.get("avg_slippage_pips", 0.0)
        avg_spr   = live.get("avg_spread_pips", 0.0)

        # Determine status
        if (wr_drift >= WR_DRIFT_ACTION or
                rr_drift >= RR_DRIFT_ACTION or
                exp_drift >= EXPECTANCY_DRIFT):
            status = "action_required"
        elif (wr_drift >= WR_DRIFT_WARN or rr_drift >= RR_DRIFT_WARN):
            status = "warn"
        else:
            status = "ok"

        # Risk multiplier
        if status == "action_required":
            risk_mult = 0.5
        elif status == "warn":
            risk_mult = 0.75
        else:
            risk_mult = 1.0

        notes_parts = []
        if wr_drift >= WR_DRIFT_WARN:
            notes_parts.append(f"WR -{ wr_drift:.1%}")
        if rr_drift >= RR_DRIFT_WARN:
            notes_parts.append(f"RR -{rr_drift:.0%}")
        if avg_slip > 2.0:
            notes_parts.append(f"slippage {avg_slip:.1f}pips")

        return DriftReport(
            pair=pair,
            backtest_wr=bt_wr, live_wr=live_wr, wr_drift=wr_drift,
            backtest_rr=bt_rr, live_rr=live_rr, rr_drift_pct=rr_drift,
            backtest_expectancy=bt_exp, live_expectancy=live_exp,
            expectancy_drift_pct=exp_drift,
            avg_slippage_pips=avg_slip, avg_spread_pips=avg_spr,
            status=status, risk_multiplier=risk_mult,
            retrain_flagged=(status == "action_required"),
            live_trades=live.get("trades", 0),
            notes=", ".join(notes_parts),
        )

    def _handle_drift_actions(self, report: DriftReport):
        pair = report.pair
        prev = self._risk_overrides.get(pair, 1.0)

        if report.risk_multiplier != prev:
            self._risk_overrides[pair] = report.risk_multiplier
            logger.warning(
                f"[DRIFT] {pair}: risk x{prev:.1f} → x{report.risk_multiplier:.1f} | {report.notes}"
            )

        if report.status == "action_required":
            msg = (f"DRIFT ALERT {pair}: WR {report.live_wr:.1%} vs BT {report.backtest_wr:.1%} | "
                   f"RR {report.live_rr:.2f} vs BT {report.backtest_rr:.2f} | "
                   f"Risk halved. Retrain queued.")
            logger.warning(f"[DRIFT] {msg}")
            self._send_telegram(msg)

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_backtest_stats(self) -> Dict[str, dict]:
        """Load best WR/RRR from engine state (evolved params)."""
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            best_wr  = s.get("best_wr", {})
            best_rrr = s.get("best_rrr", {})
            return {
                pair: {"win_rate": best_wr.get(pair, 0.0), "avg_rrr": best_rrr.get(pair, 0.0)}
                for pair in set(best_wr) | set(best_rrr)
            }
        except Exception:
            return {}

    def _load_live_stats(self) -> Dict[str, dict]:
        """Load live execution statistics from SQLite trades table."""
        if not DB_PATH.exists():
            return {}
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                SELECT pair,
                       COUNT(*) as trades,
                       AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                       AVG(rr_achieved) as avg_rrr,
                       AVG(spread_at_entry) as avg_spread
                FROM trades
                WHERE close_time IS NOT NULL
                GROUP BY pair
                HAVING COUNT(*) >= 1
            """)
            rows = cur.fetchall()
            conn.close()
            return {
                r[0]: {
                    "trades":           r[1],
                    "win_rate":         r[2] or 0.0,
                    "avg_rrr":          r[3] or 0.0,
                    "avg_spread_pips":  r[4] or 0.0,
                    "avg_slippage_pips": 0.0,   # add when MT5 fills are logged
                }
                for r in rows if r[0]
            }
        except Exception as e:
            logger.debug(f"[DRIFT] live stats load error: {e}")
            return {}

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_drift_state(self):
        try:
            if DRIFT_FILE.exists():
                with open(DRIFT_FILE) as f:
                    d = json.load(f)
                self._risk_overrides = d.get("risk_overrides", {})
        except Exception:
            self._risk_overrides = {}

    def _save_drift_state(self, reports: Dict[str, DriftReport]):
        try:
            state = {
                "last_updated":   datetime.utcnow().isoformat(),
                "risk_overrides": self._risk_overrides,
                "reports": {
                    pair: {
                        "status":          r.status,
                        "wr_drift":        round(r.wr_drift, 4),
                        "rr_drift_pct":    round(r.rr_drift_pct, 4),
                        "live_wr":         round(r.live_wr, 4),
                        "live_rr":         round(r.live_rr, 4),
                        "risk_multiplier": r.risk_multiplier,
                        "live_trades":     r.live_trades,
                    }
                    for pair, r in reports.items()
                },
            }
            with open(DRIFT_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.debug(f"[DRIFT] save state: {e}")

    @staticmethod
    def _send_telegram(msg: str):
        try:
            import os, ssl, urllib.request
            from dotenv import load_dotenv
            load_dotenv()
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not token or not chat:
                return
            import json as _json
            body = _json.dumps({"chat_id": chat, "text": f"⚠️ {msg[:1000]}"}).encode()
            req  = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=body, headers={"Content-Type": "application/json"}, method="POST")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            urllib.request.urlopen(req, timeout=8, context=ctx)
        except Exception:
            pass
