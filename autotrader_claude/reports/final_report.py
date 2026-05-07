"""
Final report — generated at 10,000 trades.
Comprehensive analysis including best params, full equity curve summary, and recommendations.
"""

import os
import json
from datetime import datetime
from loguru import logger

from config import StrategyParams
from database.supabase_client import SupabaseClient

REPORTS_DIR = "C:\\AutoTraderClaude\\reports_output"


class FinalReport:
    """10,000-trade comprehensive final report."""

    def __init__(self, db: SupabaseClient):
        self.db = db

    def generate(self, best_params: StrategyParams) -> str:
        """Generate the final 10,000-trade report. Returns file path."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(REPORTS_DIR, f"FINAL_REPORT_{ts}.md")
        json_path = os.path.join(REPORTS_DIR, f"FINAL_REPORT_{ts}.json")

        total_trades = self.db.get_total_trades()
        evolutions = self.db.select("evolution_log", limit=5000)
        versions = self.db.select("strategy_versions", limit=500)
        best_versions = sorted(versions, key=lambda v: v.get("win_rate", 0), reverse=True)[:10]
        all_trades = self.db.select("trades", limit=10000)

        win_count = sum(1 for t in all_trades if t.get("outcome") == "win")
        overall_wr = win_count / max(len(all_trades), 1)

        pair_stats = self._group_by(all_trades, "pair")
        session_stats = self._group_by(all_trades, "session")
        direction_stats = self._group_by(all_trades, "direction")

        lines = [
            "# AutoTrader Claude — Final 10,000 Trade Report",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "---",
            "",
            "## Executive Summary",
            f"- **Total Trades Simulated:** {total_trades:,}",
            f"- **Overall Win Rate:** {overall_wr:.1%}",
            f"- **Best Strategy Version:** v{best_params.version}",
            f"- **Total Evolution Iterations:** {len(evolutions)}",
            "",
            "## Best Strategy Parameters",
            "```json",
            json.dumps(best_params.to_dict(), indent=2),
            "```",
            "",
            "## Top 10 Strategy Versions",
            "| Version | Win Rate | Avg RRR | Max DD | Trades | Overfitting |",
            "|---------|----------|---------|--------|--------|-------------|",
        ]

        for v in best_versions:
            ov = "⚠️ Yes" if v.get("overfitting_flag") else "No"
            lines.append(
                f"| v{v.get('version')} "
                f"| {v.get('win_rate', 0):.1%} "
                f"| {v.get('avg_rrr', 0):.2f} "
                f"| {v.get('max_drawdown', 0):.2f}% "
                f"| {v.get('total_trades', 0)} "
                f"| {ov} |"
            )

        lines += [
            "",
            "## Performance by Pair",
            "| Pair | Trades | Win Rate |",
            "|------|--------|----------|",
        ]
        for pair, stats in pair_stats.items():
            wr = stats["wins"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {pair} | {stats['total']} | {wr:.1%} |")

        lines += [
            "",
            "## Performance by Session",
            "| Session | Trades | Win Rate |",
            "|---------|--------|----------|",
        ]
        for session, stats in session_stats.items():
            wr = stats["wins"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {session} | {stats['total']} | {wr:.1%} |")

        lines += [
            "",
            "## Performance by Direction",
            "| Direction | Trades | Win Rate |",
            "|-----------|--------|----------|",
        ]
        for direction, stats in direction_stats.items():
            wr = stats["wins"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {direction} | {stats['total']} | {wr:.1%} |")

        lines += [
            "",
            "## Evolution Summary",
            f"- Mutations accepted: {sum(1 for e in evolutions if e.get('decision') == 'kept')}",
            f"- Mutations reverted: {sum(1 for e in evolutions if e.get('decision') == 'reverted')}",
            "",
            "## Recommendations",
            "- Deploy best strategy version to live trading with reduced lot size for validation.",
            "- Monitor win rate degradation in first 50 live trades as overfitting check.",
            "- Re-run evolution quarterly with fresh market data.",
            "",
            "---",
            "*AutoTrader Claude — Evolutionary ICT Strategy System*",
        ]

        content = "\n".join(lines)
        with open(path, "w") as f:
            f.write(content)

        summary = {
            "generated_at": datetime.utcnow().isoformat(),
            "total_trades": total_trades,
            "overall_win_rate": overall_wr,
            "best_version": best_params.version,
            "best_params": best_params.to_dict(),
            "top_versions": best_versions,
        }
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)

        self.db.insert("milestone_reports", {
            "report_type": "final",
            "trade_count": total_trades,
            "strategy_version": best_params.version,
            "win_rate": overall_wr,
            "report_path": path,
        })
        logger.info(f"FINAL report saved: {path}")
        return path

    def _group_by(self, trades: list, field: str) -> dict:
        groups = {}
        for t in trades:
            key = t.get(field, "Unknown")
            if key not in groups:
                groups[key] = {"total": 0, "wins": 0}
            groups[key]["total"] += 1
            if t.get("outcome") == "win":
                groups[key]["wins"] += 1
        return groups
