"""
MonthlyReporter — generates per-month performance breakdown from backtest history.

Reads the auto_loop.jsonl log and state files to build monthly summaries.
Generates reports from Jan 2022 to current month.
Saves as JSON + optional CSV.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, date
from typing import Dict, List, Optional

from loguru import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_JSONL   = os.path.join(_ROOT, "logs", "auto_loop.jsonl")
STATE_PATH  = os.path.join(_ROOT, "local_db", "auto_loop_state.json")
REPORTS_DIR = os.path.join(_ROOT, "reports_output")


class MonthlyReporter:
    """
    Builds monthly performance summary from evolution log data.
    Per pair, per strategy, per month WR / RRR / drawdown.
    """

    def __init__(self):
        os.makedirs(REPORTS_DIR, exist_ok=True)

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_iteration_log(self) -> List[Dict]:
        """Load all entries from auto_loop.jsonl."""
        entries = []
        if not os.path.exists(LOG_JSONL):
            return entries
        try:
            with open(LOG_JSONL, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.warning(f"Monthly reporter: log load failed: {e}")
        return entries

    def _load_state(self) -> Dict:
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    # ── Monthly aggregation ───────────────────────────────────────────────────

    def build_monthly_summary(
        self,
        start_year: int = 2022,
        start_month: int = 1,
    ) -> Dict[str, Dict]:
        """
        Build monthly summary dict keyed by "YYYY-MM".
        Uses iteration log data bucketed by timestamp.
        """
        entries = self._load_iteration_log()
        state   = self._load_state()

        # Group entries by month
        by_month: Dict[str, List[Dict]] = defaultdict(list)
        for entry in entries:
            ts = entry.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                month_key = dt.strftime("%Y-%m")
                by_month[month_key].append(entry)
            except Exception:
                pass

        # Generate from start to today
        now = datetime.now()
        summaries: Dict[str, Dict] = {}

        year, month = start_year, start_month
        while (year, month) <= (now.year, now.month):
            key = f"{year:04d}-{month:02d}"
            month_entries = by_month.get(key, [])
            summaries[key] = self._summarize_month(key, month_entries)
            month += 1
            if month > 12:
                month = 1
                year += 1

        return summaries

    def _summarize_month(self, month_key: str, entries: List[Dict]) -> Dict:
        """Compute stats for a single month from its iteration entries."""
        if not entries:
            return {
                "month":       month_key,
                "iterations":  0,
                "avg_wr":      0.0,
                "avg_score":   0.0,
                "best_wr":     0.0,
                "total_trades": 0,
                "improvements": 0,
                "data_available": False,
            }

        wrs    = [e.get("wr", 0) for e in entries if e.get("wr", 0) > 0]
        scores = [e.get("score", 0) for e in entries if e.get("score", 0) > 0]
        trades = [e.get("test_trades", 0) for e in entries]
        kept   = sum(1 for e in entries if e.get("kept", False))

        return {
            "month":          month_key,
            "iterations":     len(entries),
            "avg_wr":         round(sum(wrs) / len(wrs), 4) if wrs else 0.0,
            "best_wr":        round(max(wrs), 4) if wrs else 0.0,
            "avg_score":      round(sum(scores) / len(scores), 4) if scores else 0.0,
            "total_trades":   max(trades) if trades else 0,
            "improvements":   kept,
            "improvement_rate": round(kept / len(entries), 3),
            "data_available": True,
        }

    # ── Report generation ─────────────────────────────────────────────────────

    def generate_text_report(
        self,
        start_year: int = 2022,
        save: bool = True,
    ) -> str:
        """Generate a text report covering all months from start_year."""
        summaries = self.build_monthly_summary(start_year=start_year)
        state     = self._load_state()

        lines = [
            "=" * 70,
            "AUTOTRADER — MONTHLY EVOLUTION REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Total iterations logged: {sum(s['iterations'] for s in summaries.values())}",
            f"Current best XAUUSD WR: {state.get('best_xauusd_wr_real', 0):.1%}",
            "=" * 70,
            "",
            f"{'Month':<10} {'Iters':>6} {'Avg WR':>8} {'Best WR':>8} {'Score':>7} {'Improv':>7}",
            "-" * 55,
        ]

        for key in sorted(summaries.keys()):
            s = summaries[key]
            if not s["data_available"]:
                lines.append(f"{key:<10} {'—':>6} {'—':>8} {'—':>8} {'—':>7} {'—':>7}")
            else:
                lines.append(
                    f"{key:<10} {s['iterations']:>6} "
                    f"{s['avg_wr']:>7.1%} {s['best_wr']:>7.1%} "
                    f"{s['avg_score']:>7.4f} {s['improvements']:>7}"
                )

        lines += [
            "-" * 55,
            "",
            "NOTES:",
            "  Avg WR  = mean aggregate WR across all iterations in month",
            "  Best WR = best aggregate WR achieved in month",
            "  Improv  = iterations where a new best was accepted",
            "=" * 70,
        ]
        report = "\n".join(lines)

        if save:
            path = os.path.join(REPORTS_DIR, f"monthly_report_{datetime.now().strftime('%Y%m%d')}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"Monthly report saved: {path}")

        return report

    def generate_json_report(self, start_year: int = 2022) -> Dict:
        """Return full monthly summary as dict (for API/dashboard use)."""
        return self.build_monthly_summary(start_year=start_year)

    def get_current_month_stats(self) -> Dict:
        """Quick stats for current month only."""
        now = datetime.now()
        key = now.strftime("%Y-%m")
        summaries = self.build_monthly_summary(
            start_year=now.year, start_month=now.month
        )
        return summaries.get(key, {})

    def best_month(self) -> Optional[str]:
        """Return month key with highest best_wr."""
        summaries = self.build_monthly_summary()
        best_key = None
        best_val = 0.0
        for key, s in summaries.items():
            if s.get("best_wr", 0) > best_val:
                best_val = s["best_wr"]
                best_key = key
        return best_key
