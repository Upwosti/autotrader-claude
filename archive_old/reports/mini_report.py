"""
Mini report — generated every 100 trades.
Summarises recent performance and session breakdown.
"""

import os
from datetime import datetime
from loguru import logger

from database.supabase_client import SupabaseClient
from backtester.engine import BacktestResult

REPORTS_DIR = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\reports_output"


class MiniReport:
    """100-trade milestone report."""

    def __init__(self, db: SupabaseClient):
        self.db = db

    def generate(self, total_trades: int, result: BacktestResult) -> str:
        """Generate and save a mini report. Returns file path."""
        os.makedirs(REPORTS_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(REPORTS_DIR, f"mini_report_{total_trades}trades_{ts}.md")

        recent_trades = self.db.select(
            "trades",
            filters={"strategy_version": result.strategy_version},
            limit=100,
        )

        session_stats = self._session_breakdown(recent_trades)

        lines = [
            f"# Mini Report — {total_trades} Trades",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Performance Summary",
            f"- Strategy Version: v{result.strategy_version}",
            f"- Win Rate: {result.win_rate:.1%}",
            f"- Avg RRR: {result.avg_rrr:.2f}",
            f"- Max Drawdown: {result.max_drawdown_pct:.2f}%",
            f"- Total Return: {result.total_return_pct:.2f}%",
            f"- Profit Factor: {result.profit_factor:.2f}",
            f"- Sharpe Ratio: {result.sharpe_ratio:.2f}",
            "",
            "## Session Breakdown (last 100 trades)",
            "| Session | Trades | Win Rate |",
            "|---------|--------|----------|",
        ]

        for session, stats in session_stats.items():
            wr = stats["wins"] / stats["total"] if stats["total"] else 0
            lines.append(f"| {session} | {stats['total']} | {wr:.1%} |")

        if result.overfitting_flag:
            lines += ["", "⚠️ **OVERFITTING WARNING** — Win rate suspiciously high on small sample."]

        content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        self.db.insert("milestone_reports", {
            "report_type": "mini",
            "trade_count": total_trades,
            "strategy_version": result.strategy_version,
            "win_rate": result.win_rate,
            "report_path": path,
        })
        logger.info(f"Mini report saved: {path}")
        return path

    def _session_breakdown(self, trades: list) -> dict:
        sessions = {}
        for t in trades:
            s = t.get("session", "Unknown")
            if s not in sessions:
                sessions[s] = {"total": 0, "wins": 0}
            sessions[s]["total"] += 1
            if t.get("outcome") == "win":
                sessions[s]["wins"] += 1
        return sessions

