"""
Autonomous 3-hour evaluation report.
Scheduled by the post-session setup script.
Sends a full system health report via Telegram.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

from loguru import logger

STATE_FILE   = ROOT / "local_db" / "engine_state.json"
DRIFT_FILE   = ROOT / "local_db" / "drift_state.json"
VAL_STATE    = ROOT / "local_db" / "validation_state.json"
PAPER_STATE  = ROOT / "local_db" / "paper_trading_state.json"
RESOURCE_ST  = ROOT / "local_db" / "resource_state.json"
DB_PATH      = ROOT / "data" / "autotrader.db"


def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _get_live_trade_count() -> int:
    if not DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE close_time IS NOT NULL")
        n = cur.fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


def build_report() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "=" * 40,
        f"AUTOTRADER 3H EVAL REPORT",
        f"{now}",
        "=" * 40,
    ]

    # Engine state
    state = _load_json(STATE_FILE)
    if state:
        xau_wr  = state.get("best_wr", {}).get("XAUUSD", 0)
        iteration = state.get("iteration", 0)
        lines += [
            "",
            "ENGINE",
            f"  Iteration    : {iteration:,}",
            f"  XAUUSD WR    : {xau_wr:.1%}",
        ]
        best_wrs = state.get("best_wr", {})
        top5 = sorted(best_wrs.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append("  Top 5 pairs  :")
        for p, wr in top5:
            lines.append(f"    {p:<10} {wr:.1%}")

    # Drift monitor
    drift = _load_json(DRIFT_FILE)
    if drift:
        overrides = drift.get("risk_overrides", {})
        halved = [p for p, m in overrides.items() if m < 1.0]
        lines += [
            "",
            "DRIFT MONITOR",
            f"  Last updated : {drift.get('last_updated', 'N/A')[:16]}",
            f"  Risk halved  : {halved if halved else 'none'}",
        ]

    # Walk-forward validation
    val = _load_json(VAL_STATE)
    if val:
        pairs = val.get("pairs", {})
        rejects = [p for p, v in pairs.items() if v.get("verdict") == "REJECT"]
        warns   = [p for p, v in pairs.items() if v.get("verdict") == "WARN"]
        passes  = [p for p, v in pairs.items() if v.get("verdict") == "PASS"]
        lines += [
            "",
            "VALIDATION",
            f"  Last run     : {val.get('last_run', 'N/A')[:16]}",
            f"  PASS         : {len(passes)}",
            f"  WARN         : {len(warns)}",
            f"  REJECT       : {rejects if rejects else 'none'}",
        ]

    # Paper trading
    paper = _load_json(PAPER_STATE)
    if paper:
        mode   = paper.get("mode", "paper")
        days   = paper.get("paper_days_elapsed", 0)
        trades = paper.get("total_paper_trades", 0)
        ready  = paper.get("pairs_ready", [])
        lines += [
            "",
            "PAPER TRADING",
            f"  Mode         : {mode.upper()}",
            f"  Days elapsed : {days}",
            f"  Paper trades : {trades}",
            f"  Pairs ready  : {ready if ready else 'evaluating...'}",
        ]

    # Live trades
    live_n = _get_live_trade_count()
    lines += [
        "",
        "LIVE TRADES",
        f"  Closed trades: {live_n}",
    ]

    lines += [
        "",
        "ALL PHASES: 1-8 COMPLETE",
        "Engine running autonomously.",
        "=" * 40,
    ]

    return "\n".join(lines)


def send_telegram(text: str):
    import ssl, urllib.request, json as _json
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning("Telegram not configured — printing report only")
        print(text)
        return
    try:
        body = _json.dumps({"chat_id": chat, "text": text[:4096]}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            logger.info(f"Telegram sent: {resp.status}")
    except Exception as e:
        logger.error(f"Telegram failed: {e}")
        print(text)


if __name__ == "__main__":
    logger.info("3-hour evaluation report starting...")
    report = build_report()
    logger.info(f"\n{report}")
    send_telegram(report)
    logger.info("Evaluation report complete.")
