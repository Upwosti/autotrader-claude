"""
Notion Integration — batch-sync system data to a Notion workspace.

Syncs every 2 hours (scheduled by run_forever.py):
  - Monthly performance reports
  - Weekly audit summaries
  - Pair performance analytics
  - Strategy version history
  - Evolution logs
  - Resource usage logs

To activate:
  1. Create a Notion integration at https://www.notion.so/my-integrations
  2. Add to .env:
       NOTION_TOKEN=secret_xxxxx
       NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  3. Share your Notion database with the integration

If NOTION_TOKEN is not set, this module logs a warning and skips silently.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DB_ID = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_API   = "https://api.notion.com/v1"
NOTION_VER   = "2022-06-28"

STATE_FILE   = Path(__file__).parent.parent / "local_db" / "engine_state.json"
REPORTS_DIR  = Path(__file__).parent / "monthly_reports"
EVOLUTION_LOG = Path(__file__).parent.parent / "local_db" / "evolution_log.json"


def _notion_request(
    endpoint: str,
    method: str = "POST",
    body: Optional[dict] = None,
) -> dict:
    """Execute a Notion API request. Returns {} on error."""
    if not NOTION_TOKEN:
        return {}
    url = f"{NOTION_API}/{endpoint}"
    data = json.dumps(body or {}).encode()
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization":  f"Bearer {NOTION_TOKEN}",
            "Content-Type":   "application/json",
            "Notion-Version": NOTION_VER,
        },
    )
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"Notion API error ({endpoint}): {e}")
        return {}


def _text_block(content: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
        },
    }


def _heading_block(content: str, level: int = 2) -> dict:
    t = f"heading_{level}"
    return {
        "object": "block",
        "type": t,
        t: {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_engine_status() -> bool:
    """Sync current engine state to Notion."""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        logger.debug("Notion: token not configured, skipping sync")
        return False
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)

        iteration     = state.get("iteration", 0)
        xau_wr        = state.get("xauusd_best_wr", 0)
        best_score    = state.get("global_best_score", 0)
        last_saved    = state.get("last_saved", "")

        best_wr_all   = state.get("best_wr", {})
        top_pairs = sorted(best_wr_all.items(), key=lambda x: x[1], reverse=True)[:5]
        top_pairs_txt = " | ".join(f"{p}: {v*100:.1f}%" for p, v in top_pairs)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        body = {
            "parent":     {"database_id": NOTION_DB_ID},
            "properties": {
                "Name": {
                    "title": [{"text": {"content": f"Engine Status — {ts}"}}]
                },
                "Type": {"select": {"name": "engine_status"}},
                "Date": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            },
            "children": [
                _heading_block("AutoTrader Claude — Engine Status", 2),
                _text_block(f"Iteration: {iteration:,}"),
                _text_block(f"XAUUSD WR: {xau_wr*100:.1f}%"),
                _text_block(f"Global Best Score: {best_score:.4f}"),
                _text_block(f"Top Pairs: {top_pairs_txt}"),
                _text_block(f"Last Saved: {last_saved}"),
                _divider_block(),
            ],
        }
        result = _notion_request("pages", method="POST", body=body)
        if result.get("id"):
            logger.info(f"Notion: engine status synced (iter {iteration})")
            return True
    except Exception as e:
        logger.debug(f"Notion sync_engine_status error: {e}")
    return False


def sync_monthly_summary() -> bool:
    """Sync last month's HTML report summary to Notion."""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False
    try:
        now = datetime.utcnow()
        # Find most recent report
        report_files = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
        if not report_files:
            return False

        latest = report_files[0]
        # Parse year/month from filename: 2026_05_report.html
        parts = latest.stem.split("_")
        if len(parts) >= 2:
            year, month = int(parts[0]), int(parts[1])
            month_name = datetime(year, month, 1).strftime("%B %Y")
        else:
            month_name = "Unknown"

        body = {
            "parent":     {"database_id": NOTION_DB_ID},
            "properties": {
                "Name": {
                    "title": [{"text": {"content": f"Monthly Report — {month_name}"}}]
                },
                "Type": {"select": {"name": "monthly_report"}},
                "Date": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            },
            "children": [
                _heading_block(f"Monthly Report — {month_name}", 2),
                _text_block(f"Report file: {latest.name}"),
                _text_block("See reporting/monthly_reports/ for full HTML report."),
                _divider_block(),
            ],
        }
        result = _notion_request("pages", method="POST", body=body)
        return bool(result.get("id"))
    except Exception as e:
        logger.debug(f"Notion sync_monthly_summary error: {e}")
        return False


def sync_evolution_log(max_entries: int = 20) -> bool:
    """Sync recent evolution log entries to Notion."""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False
    try:
        with open(EVOLUTION_LOG) as f:
            evo = json.load(f)

        entries = evo if isinstance(evo, list) else list(evo.values())
        recent  = entries[-max_entries:]

        lines = []
        for e in recent:
            pair  = e.get("pair", "?")
            wr    = e.get("win_rate", 0)
            rrr   = e.get("avg_rrr", 0)
            score = e.get("score", 0)
            itr   = e.get("iteration", "?")
            lines.append(f"Iter {itr} | {pair} | WR {wr*100:.1f}% | RRR {rrr:.2f} | Score {score:.3f}")

        body = {
            "parent":     {"database_id": NOTION_DB_ID},
            "properties": {
                "Name": {
                    "title": [{"text": {"content": f"Evolution Log — {datetime.utcnow().strftime('%Y-%m-%d')}"}}]
                },
                "Type": {"select": {"name": "evolution_log"}},
                "Date": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            },
            "children": [
                _heading_block("Recent Evolution Results", 2),
                *[_text_block(line) for line in lines[-15:]],
                _divider_block(),
            ],
        }
        result = _notion_request("pages", method="POST", body=body)
        return bool(result.get("id"))
    except Exception as e:
        logger.debug(f"Notion sync_evolution_log error: {e}")
        return False


def sync_pair_performance() -> bool:
    """Sync best WR per pair to Notion."""
    if not NOTION_TOKEN or not NOTION_DB_ID:
        return False
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)

        best_wr  = state.get("best_wr", {})
        best_rrr = state.get("best_rrr", {})

        lines = []
        for pair, wr in sorted(best_wr.items(), key=lambda x: x[1], reverse=True):
            rrr = best_rrr.get(pair, 0)
            lines.append(f"{pair}: WR {wr*100:.1f}% | RRR {rrr:.2f}")

        body = {
            "parent":     {"database_id": NOTION_DB_ID},
            "properties": {
                "Name": {
                    "title": [{"text": {"content": f"Pair Performance — {datetime.utcnow().strftime('%Y-%m-%d')}"}}]
                },
                "Type": {"select": {"name": "pair_performance"}},
                "Date": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            },
            "children": [
                _heading_block("Pair Performance Ranking", 2),
                *[_text_block(line) for line in lines],
                _divider_block(),
            ],
        }
        result = _notion_request("pages", method="POST", body=body)
        return bool(result.get("id"))
    except Exception as e:
        logger.debug(f"Notion sync_pair_performance error: {e}")
        return False


def run_full_sync() -> dict:
    """
    Run all sync tasks. Called by the scheduler every 2 hours.
    Returns dict of what was synced.
    """
    if not NOTION_TOKEN:
        logger.warning(
            "Notion sync skipped — NOTION_TOKEN not set in .env. "
            "Add NOTION_TOKEN=secret_xxx and NOTION_DATABASE_ID=xxx to enable."
        )
        return {"status": "not_configured"}

    results = {
        "engine_status":    sync_engine_status(),
        "monthly_summary":  sync_monthly_summary(),
        "evolution_log":    sync_evolution_log(),
        "pair_performance": sync_pair_performance(),
        "timestamp":        datetime.utcnow().isoformat(),
    }
    synced = sum(1 for v in results.values() if v is True)
    logger.info(f"Notion sync complete: {synced}/4 tasks succeeded")
    return results


if __name__ == "__main__":
    results = run_full_sync()
    print(json.dumps(results, indent=2))
