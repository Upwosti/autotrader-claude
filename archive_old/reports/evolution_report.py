"""
Evolution report — generated every 1000 trades.
Summarises parameter evolution history and best-performing configs.
"""

import os
from datetime import datetime
from loguru import logger

from database.supabase_client import SupabaseClient

REPORTS_DIR = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\reports_output"


class EvolutionReport:
    """1000-trade evolution analysis report."""

    def __init__(self, db: SupabaseClient):
        self.db = db

    def generate(self, total_trades: int) -> str:
        """Generate and save an evolution report. Returns file path."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(REPORTS_DIR, f"evolution_report_{total_trades}trades_{ts}.md")

        evolutions = self.db.select("evolution_log", limit=1000)
        versions = self.db.select("strategy_versions", limit=200)

        best_versions = sorted(versions, key=lambda v: v.get("win_rate", 0), reverse=True)[:5]
        kept = [e for e in evolutions if e.get("decision") == "kept"]
        reverted = [e for e in evolutions if e.get("decision") == "reverted"]

        param_counts = {}
        for e in kept:
            p = e.get("param_changed", "unknown")
            param_counts[p] = param_counts.get(p, 0) + 1

        lines = [
            f"# Evolution Report — {total_trades} Trades",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Evolution Statistics",
            f"- Total mutations: {len(evolutions)}",
            f"- Kept: {len(kept)} ({len(kept)/max(len(evolutions),1):.1%})",
            f"- Reverted: {len(reverted)} ({len(reverted)/max(len(evolutions),1):.1%})",
            "",
            "## Most Improved Parameters",
            "| Parameter | Times Improved |",
            "|-----------|---------------|",
        ]

        for param, count in sorted(param_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {param} | {count} |")

        lines += [
            "",
            "## Top 5 Strategy Versions",
            "| Version | Win Rate | Avg RRR | Max DD | Trades |",
            "|---------|----------|---------|--------|--------|",
        ]

        for v in best_versions:
            lines.append(
                f"| v{v.get('version')} "
                f"| {v.get('win_rate', 0):.1%} "
                f"| {v.get('avg_rrr', 0):.2f} "
                f"| {v.get('max_drawdown', 0):.2f}% "
                f"| {v.get('total_trades', 0)} |"
            )

        if evolutions:
            last = evolutions[-1]
            lines += [
                "",
                f"## Latest State",
                f"- Current version: v{last.get('to_version')}",
                f"- Last change: `{last.get('param_changed')}` → {last.get('new_value')} ({last.get('decision')})",
                f"- Win rate: {last.get('win_rate_after', 0):.1%}",
            ]

        content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        self.db.insert("milestone_reports", {
            "report_type": "evolution",
            "trade_count": total_trades,
            "strategy_version": best_versions[0].get("version") if best_versions else 0,
            "win_rate": best_versions[0].get("win_rate") if best_versions else 0,
            "report_path": path,
        })
        logger.info(f"Evolution report saved: {path}")
        return path

