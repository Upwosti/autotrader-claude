"""
Backtest report generator — produces JSON + Markdown summary.
"""

import os
import json
from datetime import datetime
from typing import List
from backtester.engine import BacktestResult, BacktestTrade
from loguru import logger

REPORTS_DIR = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\reports_output"


class BacktestReport:
    """Generates human-readable and machine-readable backtest reports."""

    def __init__(self):
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def generate(self, result: BacktestResult) -> str:
        """Generate markdown + JSON report. Returns path to markdown file."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        base = f"{result.pair}_v{result.strategy_version}_{timestamp}"
        md_path = os.path.join(REPORTS_DIR, f"{base}.md")
        json_path = os.path.join(REPORTS_DIR, f"{base}.json")

        md = self._build_markdown(result)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self._build_json(result), f, indent=2, default=str)

        logger.info(f"Report saved: {md_path}")
        return md_path

    def _build_markdown(self, r: BacktestResult) -> str:
        win_icon = "🟢" if r.win_rate >= 0.5 else "🔴"
        dd_icon = "🟢" if r.max_drawdown_pct < 5 else "🔴"
        ret_icon = "🟢" if r.total_return_pct > 0 else "🔴"

        lines = [
            f"# Backtest Report — {r.pair} v{r.strategy_version}",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Summary",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Period | {r.start_date} → {r.end_date} |",
            f"| Total Trades | {r.total_trades} |",
            f"| Win Rate | {win_icon} {r.win_rate:.1%} |",
            f"| Avg RRR | {r.avg_rrr:.2f} |",
            f"| Total Return | {ret_icon} {r.total_return_pct:.2f}% |",
            f"| Max Drawdown | {dd_icon} {r.max_drawdown_pct:.2f}% |",
            f"| Sharpe Ratio | {r.sharpe_ratio:.2f} |",
            f"| Profit Factor | {r.profit_factor:.2f} |",
            "",
        ]

        if r.overfitting_flag:
            lines += ["⚠️ **OVERFITTING WARNING**: High win rate on small sample.", ""]
        if r.small_sample_flag:
            lines += ["⚠️ **SMALL SAMPLE WARNING**: Fewer than 30 trades — results may not be statistically significant.", ""]

        lines += ["## Session Breakdown"]
        sessions = {}
        for t in r.trades:
            s = t.session
            if s not in sessions:
                sessions[s] = {"trades": 0, "wins": 0}
            sessions[s]["trades"] += 1
            if t.outcome == "win":
                sessions[s]["wins"] += 1

        lines.append("| Session | Trades | Win Rate |")
        lines.append("|---------|--------|----------|")
        for s, d in sessions.items():
            wr = d["wins"] / d["trades"] if d["trades"] > 0 else 0
            lines.append(f"| {s} | {d['trades']} | {wr:.1%} |")

        lines += ["", "## Pair Stats",
                  f"Initial Capital: ${r.initial_capital:,.2f}",
                  f"Final Capital: ${r.final_capital:,.2f}",
                  f"Wins: {r.winning_trades} | Losses: {r.losing_trades}"]

        return "\n".join(lines)

    def _build_json(self, r: BacktestResult) -> dict:
        return {
            "metadata": {
                "strategy_version": r.strategy_version,
                "pair": r.pair,
                "timeframe": r.timeframe,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "generated_at": datetime.utcnow().isoformat(),
            },
            "performance": {
                "initial_capital": r.initial_capital,
                "final_capital": r.final_capital,
                "total_return_pct": r.total_return_pct,
                "win_rate": r.win_rate,
                "total_trades": r.total_trades,
                "winning_trades": r.winning_trades,
                "losing_trades": r.losing_trades,
                "avg_rrr": r.avg_rrr,
                "max_drawdown_pct": r.max_drawdown_pct,
                "sharpe_ratio": r.sharpe_ratio,
                "profit_factor": r.profit_factor,
            },
            "flags": {
                "overfitting": r.overfitting_flag,
                "small_sample": r.small_sample_flag,
            },
            "trades": [
                {
                    "pair": t.pair,
                    "direction": t.direction,
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time),
                    "outcome": t.outcome,
                    "pnl_pips": t.pnl_pips,
                    "rrr": t.rrr_achieved,
                    "confidence": t.confidence_score,
                    "session": t.session,
                }
                for t in r.trades
            ],
        }

