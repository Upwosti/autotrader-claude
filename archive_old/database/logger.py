"""
Trade and iteration logger — persists every trade, version, and evolution step.
"""

from datetime import datetime
from typing import Optional
from loguru import logger

from config import StrategyParams
from database.supabase_client import SupabaseClient
from backtester.engine import BacktestResult, BacktestTrade


class TradeLogger:
    """Logs all trades, strategy versions, and evolution events to Supabase."""

    def __init__(self, db: SupabaseClient):
        self.db = db

    def log_strategy_version(self, params: StrategyParams,
                              result: Optional[BacktestResult] = None,
                              is_best: bool = False,
                              overfitting: bool = False) -> int:
        """Insert or update a strategy version record."""
        data = {
            "version": params.version,
            "params": params.to_dict(),
            "notes": params.notes,
            "is_best": is_best,
            "overfitting_flag": overfitting,
        }
        if result:
            data.update({
                "win_rate": result.win_rate,
                "avg_rrr": result.avg_rrr,
                "max_drawdown": result.max_drawdown_pct,
                "total_trades": result.total_trades,
                "profitable_trades": result.winning_trades,
            })
        self.db.upsert("strategy_versions", data)
        self.db.set_state("current_version", str(params.version))
        logger.info(f"Logged strategy v{params.version} (best={is_best})")
        return params.version

    def log_backtest(self, result: BacktestResult, report_path: str = "") -> int:
        """Insert a backtest run record."""
        data = {
            "strategy_version": result.strategy_version,
            "pair": result.pair,
            "timeframe": result.timeframe,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return_pct": result.total_return_pct,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "avg_rrr": result.avg_rrr,
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "profit_factor": result.profit_factor,
            "report_path": report_path,
            "overfitting_flag": result.overfitting_flag,
            "small_sample_flag": result.small_sample_flag,
        }
        row = self.db.insert("backtest_runs", data)
        run_id = row.get("id", 0) if row else 0
        logger.info(f"Logged backtest run {run_id} for v{result.strategy_version}")
        return run_id

    def log_trades(self, trades: list, strategy_version: int, run_id: int):
        """Batch insert trade records."""
        for t in trades:
            data = {
                "strategy_version": strategy_version,
                "backtest_run_id": run_id,
                "pair": t.pair,
                "direction": t.direction,
                "entry_time": str(t.entry_time),
                "exit_time": str(t.exit_time) if t.exit_time else None,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "risk_pct": t.risk_pct,
                "rrr_achieved": t.rrr_achieved,
                "pnl_pips": t.pnl_pips,
                "pnl_pct": t.pnl_pct,
                "outcome": t.outcome,
                "session": t.session,
                "confidence_score": t.confidence_score,
            }
            self.db.insert("trades", data)
        self.db.increment_trades(len(trades))
        logger.info(f"Logged {len(trades)} trades for run {run_id}")

    def log_evolution(self, iteration: int, from_version: int, to_version: int,
                      param: str, old_val: str, new_val: str,
                      wr_before: float, wr_after: float, decision: str,
                      reasoning: str):
        """Log one evolution step."""
        data = {
            "iteration": iteration,
            "from_version": from_version,
            "to_version": to_version,
            "change_type": "mutation",
            "param_changed": param,
            "old_value": old_val,
            "new_value": new_val,
            "win_rate_before": wr_before,
            "win_rate_after": wr_after,
            "decision": decision,
            "reasoning": reasoning,
        }
        self.db.insert("evolution_log", data)
        logger.info(f"Evolution {iteration}: {param} {old_val}→{new_val} → {decision}")

    def log_alert(self, alert_type: str, channel: str,
                  subject: str, body: str, success: bool, error: str = ""):
        """Log an alert send attempt."""
        self.db.insert("alerts_log", {
            "alert_type": alert_type,
            "channel": channel,
            "subject": subject,
            "body": body[:500],
            "success": success,
            "error_msg": error,
        })
