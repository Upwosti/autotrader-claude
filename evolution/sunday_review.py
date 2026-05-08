"""
Sunday Review — weekly self-evolution: analyse performance, run evolution, Claude summary.
"""

from datetime import datetime
from loguru import logger
from config import ACTIVE_PARAMS, MAX_EVOLUTION_ITERS
from core.claude_brain import sunday_review as claude_review


class SundayReview:
    def __init__(self, db, optimizer, telegram=None):
        self.db        = db
        self.optimizer = optimizer
        self.telegram  = telegram

    def run(self):
        logger.info("=== Sunday Review Starting ===")

        trades  = self.db.select("trades", limit=500)
        total   = len(trades)
        wins    = sum(1 for t in trades if t.get("outcome") == "win")
        wr      = wins / max(total, 1)
        rrrs    = [t.get("rrr", 0) for t in trades if t.get("rrr")]
        avg_rrr = sum(rrrs) / max(len(rrrs), 1)

        pair_wins = {}
        for t in trades:
            p = t.get("pair", "XAUUSD")
            pair_wins[p] = pair_wins.get(p, {"wins": 0, "total": 0})
            pair_wins[p]["total"] += 1
            if t.get("outcome") == "win":
                pair_wins[p]["wins"] += 1

        best_pair = max(pair_wins, key=lambda p: pair_wins[p]["wins"] / max(pair_wins[p]["total"], 1),
                        default="XAUUSD") if pair_wins else "XAUUSD"

        stats = {
            "total_trades": total,
            "win_rate": wr,
            "avg_rrr": avg_rrr,
            "max_dd": 0.0,
            "best_pair": best_pair,
        }

        logger.info(f"Week stats: {total} trades, {wr:.1%} WR, {avg_rrr:.2f} RRR, best={best_pair}")

        # Run brief evolution (10 iterations)
        try:
            self.optimizer.evolve(max_iterations=10)
        except Exception as e:
            logger.error(f"Sunday evolution error: {e}")

        # Claude analysis
        summary = claude_review(stats)
        logger.info(f"Claude Sunday Review:\n{summary}")

        if self.telegram:
            msg = (
                f"Sunday Review Complete\n"
                f"Trades: {total} | WR: {wr:.1%} | RRR: {avg_rrr:.2f}\n"
                f"Best: {best_pair}\n\n{summary}"
            )
            self.telegram.send("Weekly Sunday Review", msg)

        return summary
