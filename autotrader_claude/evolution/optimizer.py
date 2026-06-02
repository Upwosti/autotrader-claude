"""
Parameter optimization loop — runs backtests, mutates params, keeps winners.
Uses Claude API for intelligent analysis between iterations.
Multi-metric optimization: composite_score (expectancy + win_rate + profit_factor
+ sharpe + walk_forward_stability - drawdown_penalty) prevents overfitting.

Allowed email alert types (OMEGA-1 rule — only 5 types):
  1. Daily Evolution  (08:00 UTC)
  2. Daily Report     (09:00 UTC)
  3. Weekly Audit     (Sunday)
  4. Monthly Summary  (1st of month)
  5. Critical Alert   (on-demand for emergencies only)
"""

import copy
import gc
from typing import Optional, Tuple
from loguru import logger

from config import StrategyParams, EVOLUTION_MIN_TRADES, ACTIVE_PARAMS
from strategy.evolution import StrategyEvolver
from backtester.engine import BacktestEngine, BacktestResult
from backtester.report import BacktestReport
from database.supabase_client import SupabaseClient
from database.logger import TradeLogger
from evolution.analyzer import ResultAnalyzer
from evolution.versioning import VersionManager
from alerts.telegram_bot import TelegramAlert
from alerts.email_alert import EmailAlert

# Only these alert types may trigger emails.
ALLOWED_EMAIL_TYPES = {
    "daily_evolution",
    "daily_report",
    "weekly_audit",
    "monthly_summary",
    "critical_alert",
}


class Optimizer:
    """Drives the full evolution loop — mutate → backtest → evaluate → repeat."""

    def __init__(self, db: SupabaseClient):
        self.db = db
        self.trade_logger = TradeLogger(db)
        self.evolver = StrategyEvolver()
        self.analyzer = ResultAnalyzer()
        self.versioner = VersionManager(db)
        self.reporter = BacktestReport()
        self.telegram = TelegramAlert()
        self.email = EmailAlert()
        self.current_result: Optional[BacktestResult] = None
        self.iteration = 1

    def run_iteration(self, params: StrategyParams, pair: str = "XAUUSD") -> BacktestResult:
        """Run one full backtest iteration."""
        engine = BacktestEngine(params)
        result = engine.run(pair=pair)
        report_path = self.reporter.generate(result)

        self.trade_logger.log_strategy_version(
            params=params, result=result,
            overfitting=result.overfitting_flag,
        )
        run_id = self.trade_logger.log_backtest(result, report_path)
        self.trade_logger.log_trades(result.trades, params.version, run_id)

        # Release memory after each pair — OMEGA-1 RAM limit
        del engine
        gc.collect()

        return result

    def evolve(
        self,
        max_iterations: int = 100,
        pairs: list = None,
    ):
        """Main evolution loop.
        - Runs initial backtest
        - Mutates one param at a time
        - Keeps improvements by composite_score (not win_rate alone)
        - Enforces Monte Carlo survival >= 60%, drawdown <= 25%
        - Alerts on milestones (only allowed email types)
        """
        if pairs is None:
            pairs = ["XAUUSD"]

        logger.info(f"Starting evolution — {max_iterations} iterations | pairs: {pairs}")

        current_params = copy.deepcopy(ACTIVE_PARAMS)
        logger.info(f"Baseline backtest v{current_params.version}")

        baseline = self.run_iteration(current_params, pair=pairs[0])
        self.evolver.best_score = baseline.composite_score()
        self.evolver.best_win_rate = baseline.win_rate
        self.evolver.best_params = copy.deepcopy(current_params)
        self.current_result = baseline

        self._send_alert("daily_evolution",
                         f"Baseline v{current_params.version} complete",
                         baseline)

        for i in range(1, max_iterations + 1):
            self.iteration = i
            logger.info(f"─── Iteration {i}/{max_iterations} ───")

            new_params, param_name, old_val, new_val, kept = self.evolver.next_iteration(
                current_score=self.current_result.composite_score(),
                current_win_rate=self.current_result.win_rate,
                current_trade_count=self.current_result.total_trades,
                mc_survival=self.current_result.monte_carlo_survival,
                max_drawdown=self.current_result.max_drawdown_pct,
            )

            new_result = self.run_iteration(new_params, pair=pairs[0])

            reasoning = self.analyzer.explain_change(
                param_name, old_val, new_val,
                self.current_result.win_rate, new_result.win_rate, kept,
            )

            self.trade_logger.log_evolution(
                iteration=i,
                from_version=current_params.version,
                to_version=new_params.version,
                param=param_name,
                old_val=old_val,
                new_val=new_val,
                wr_before=self.current_result.win_rate,
                wr_after=new_result.win_rate,
                decision="kept" if kept else "reverted",
                reasoning=reasoning,
            )

            if kept:
                current_params = new_params
                self.current_result = new_result
                if new_result.composite_score() > self.evolver.best_score:
                    self.trade_logger.log_strategy_version(
                        new_params, new_result, is_best=True,
                        overfitting=new_result.overfitting_flag,
                    )
                    self._send_alert("critical_alert",
                                     f"New best strategy v{new_params.version}!",
                                     new_result)

            if new_result.overfitting_flag:
                self._send_alert("critical_alert",
                                 f"Overfitting warning at v{new_params.version}",
                                 new_result)

            total_trades = self.db.get_total_trades()
            self._check_milestones(total_trades, new_result)

            if i % EVOLUTION_MIN_TRADES == 0:
                self.versioner.snapshot(current_params, new_result, i)

        logger.info("Evolution complete")
        best = self.evolver.get_best()
        logger.info(f"Best strategy: v{best.version} | Score: {self.evolver.best_score:.3f} | WR: {self.evolver.best_win_rate:.1%}")

    def _check_milestones(self, total_trades: int, result: BacktestResult):
        from config import MINI_REPORT_TRADES, EVOLUTION_REPORT_TRADES, FINAL_REPORT_TRADES
        from reports.mini_report import MiniReport
        from reports.evolution_report import EvolutionReport
        from reports.final_report import FinalReport

        if total_trades > 0 and total_trades % MINI_REPORT_TRADES == 0:
            MiniReport(self.db).generate(total_trades, result)
            self._send_alert("daily_report", f"{total_trades} trades milestone", result)

        if total_trades > 0 and total_trades % EVOLUTION_REPORT_TRADES == 0:
            EvolutionReport(self.db).generate(total_trades)
            self._send_alert("weekly_audit", f"{total_trades} trades evolution report", result)

        if total_trades >= FINAL_REPORT_TRADES:
            FinalReport(self.db).generate(self.evolver.get_best())
            self._send_alert("monthly_summary", "Final 10,000 trade report ready!", result)

    def _send_alert(self, alert_type: str, subject: str, result: BacktestResult):
        """Send alert — only ALLOWED_EMAIL_TYPES may trigger email."""
        body = (
            f"Version: v{result.strategy_version}\n"
            f"Win Rate: {result.win_rate:.1%}\n"
            f"Expectancy: {result.expectancy:.2f} pips\n"
            f"Composite Score: {result.composite_score():.3f}\n"
            f"Avg RRR: {result.avg_rrr:.2f}\n"
            f"Max DD: {result.max_drawdown_pct:.2f}%\n"
            f"Profit Factor: {result.profit_factor:.2f}\n"
            f"MC Survival: {result.monte_carlo_survival:.1%}\n"
            f"WF Stability: {result.walk_forward_stability:.1%}\n"
            f"Total Return: {result.total_return_pct:.2f}%\n"
            f"Trades: {result.total_trades}"
        )
        self.telegram.send(subject, body)
        if alert_type in ALLOWED_EMAIL_TYPES:
            self.email.send(subject, body)
        else:
            logger.debug(f"Email suppressed for alert_type='{alert_type}' (not in allowed list)")
