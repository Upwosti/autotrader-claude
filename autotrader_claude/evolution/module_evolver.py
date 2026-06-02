"""
Module Evolver — auto-evolves each trading module until profitable.
Target: win_rate >= 50% AND avg_rrr >= 2.0 AND profit_factor >= 1.5

Evolution strategy per module:
  1. Run backtest
  2. If below targets: mutate ONE parameter randomly from EVOLVABLE_PARAMS
  3. Re-run backtest with new params
  4. If improved (composite score higher): keep change
  5. Repeat until targets met or max_iterations reached
  6. Send Telegram update after each module completes

Uses composite score to avoid single-metric overfitting:
  score = win_rate*3 + min(avg_rrr,4)*1.5 + min(pf,5)*0.5 - drawdown*0.1
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import copy
import random
import gc
from dataclasses import dataclass
from typing import List, Optional, Tuple
from loguru import logger

from config import StrategyParams, PARAM_RANGES
from backtester.module_backtester import ModuleBacktester, ModuleBacktestResult
from trading_module import TRADING_MODULE_CONFIGS, TradingModuleConfig


# ── Targets ────────────────────────────────────────────────────────────────────
WIN_RATE_TARGET = 0.50
RRR_TARGET = 2.0
PF_TARGET = 1.5
MAX_ITERATIONS_PER_MODULE = 200


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ModuleEvolveResult:
    module_name: str
    initial_result: ModuleBacktestResult
    final_result: ModuleBacktestResult
    iterations: int
    improved: bool
    best_params_snapshot: dict


# ── Evolver ────────────────────────────────────────────────────────────────────

class ModuleEvolver:
    """Evolves each trading module's strategy params until profit targets are met."""

    def __init__(
        self,
        params: StrategyParams,
        telegram=None,
        email=None,
    ):
        # params may be mutated per module; always work on copies
        self.params = params
        self.telegram = telegram
        self.email = email
        # best_params is updated after each module evolves — holds globally best seen
        self.best_params = copy.deepcopy(params)

    # ── Scoring ────────────────────────────────────────────────────────────────

    def _composite_score(self, result: ModuleBacktestResult) -> float:
        """Higher score = better overall result.

        Weighted composite that balances win rate, RRR, profit factor, and
        drawdown without overfitting any single metric.
        """
        if result.total_trades == 0:
            return 0.0
        score = (
            result.win_rate * 3.0
            + min(result.avg_rrr, 4.0) * 1.5
            + min(result.profit_factor, 5.0) * 0.5
            - result.max_drawdown_pct * 0.1
        )
        return max(0.0, score)

    # ── Parameter mutation ─────────────────────────────────────────────────────

    def _mutate_params(
        self, params: StrategyParams
    ) -> Tuple[StrategyParams, str, str, str]:
        """Pick one evolvable param at random and mutate it.

        Returns (new_params, param_name, old_value_str, new_value_str).
        """
        new_params = copy.deepcopy(params)
        param_name = random.choice(list(PARAM_RANGES.keys()))
        spec = PARAM_RANGES[param_name]
        old_val = getattr(new_params, param_name, None)

        if isinstance(spec, list):
            # Discrete set — pick any value (re-roll if same)
            choices = [v for v in spec if v != old_val] or spec
            new_val = random.choice(choices)
        else:
            # (min, max, step) tuple
            lo, hi, step = spec
            if isinstance(step, float) or isinstance(lo, float):
                # Float param
                n_steps = int(round((hi - lo) / step))
                new_val = round(lo + random.randint(0, n_steps) * step, 6)
            else:
                # Integer param
                n_steps = (hi - lo) // step
                new_val = lo + random.randint(0, n_steps) * step

        setattr(new_params, param_name, new_val)
        return new_params, param_name, str(old_val), str(new_val)

    # ── Target check ──────────────────────────────────────────────────────────

    def _meets_targets(self, result: ModuleBacktestResult) -> bool:
        """Return True when all three profitability targets are satisfied."""
        return (
            result.win_rate >= WIN_RATE_TARGET
            and result.avg_rrr >= RRR_TARGET
            and result.profit_factor >= PF_TARGET
        )

    # ── Single-module evolution ────────────────────────────────────────────────

    def evolve_module(
        self,
        config: TradingModuleConfig,
        pair: str = "XAUUSD",
    ) -> ModuleEvolveResult:
        """Evolve one module until targets are met or MAX_ITERATIONS_PER_MODULE reached.

        Strategy:
          - Run backtest with current params
          - Loop: mutate one param, re-run, keep if composite score improves OR
            targets are newly met
          - After loop: send final Telegram message
        """
        logger.info(
            f"[ModuleEvolver] === Evolving {config.name.upper()} "
            f"({config.htf}/{config.ltf1}/{config.ltf2}) ==="
        )

        # Working copy of params for this module
        current_params = copy.deepcopy(self.params)

        # ── Initial backtest ───────────────────────────────────────────────────
        backtester = ModuleBacktester(current_params)
        initial_result = backtester.run_module(config, pair=pair)
        gc.collect()

        logger.info(
            f"[{config.name}] Initial: WR={initial_result.win_rate:.1%} "
            f"RRR={initial_result.avg_rrr:.2f} PF={initial_result.profit_factor:.2f} "
            f"score={self._composite_score(initial_result):.3f}"
        )
        self._send_module_update(config, initial_result, 0, "INITIAL")

        # Already profitable — return immediately
        if self._meets_targets(initial_result):
            logger.info(f"[{config.name}] Already meets all targets — skipping evolution.")
            return ModuleEvolveResult(
                module_name=config.name,
                initial_result=initial_result,
                final_result=initial_result,
                iterations=0,
                improved=True,
                best_params_snapshot=current_params.to_dict(),
            )

        # ── Evolution loop ─────────────────────────────────────────────────────
        best_result = initial_result
        best_score = self._composite_score(initial_result)
        best_params = copy.deepcopy(current_params)
        iteration = 0

        for iteration in range(1, MAX_ITERATIONS_PER_MODULE + 1):
            # Mutate from the current best params
            candidate_params, pname, old_v, new_v = self._mutate_params(best_params)

            backtester = ModuleBacktester(candidate_params)
            candidate_result = backtester.run_module(config, pair=pair)
            del backtester
            gc.collect()

            candidate_score = self._composite_score(candidate_result)
            targets_met = self._meets_targets(candidate_result)

            # Accept if score improved OR if this is the first time targets are met
            improved_score = candidate_score > best_score
            if improved_score or targets_met:
                logger.info(
                    f"[{config.name}] iter {iteration:3d} | ACCEPTED {pname}: "
                    f"{old_v} -> {new_v} | "
                    f"score {best_score:.3f} -> {candidate_score:.3f} | "
                    f"WR={candidate_result.win_rate:.1%} "
                    f"RRR={candidate_result.avg_rrr:.2f} "
                    f"PF={candidate_result.profit_factor:.2f}"
                )
                best_result = candidate_result
                best_score = candidate_score
                best_params = copy.deepcopy(candidate_params)
            else:
                logger.debug(
                    f"[{config.name}] iter {iteration:3d} | rejected {pname}: "
                    f"{old_v} -> {new_v} | "
                    f"score {candidate_score:.3f} <= {best_score:.3f}"
                )

            # Periodic Telegram update every 25 iterations
            if iteration % 25 == 0:
                status = (
                    "TARGETS MET" if self._meets_targets(best_result) else f"iter {iteration}"
                )
                self._send_module_update(config, best_result, iteration, status)

            if targets_met:
                logger.info(
                    f"[{config.name}] All targets met at iteration {iteration}!"
                )
                break

        # ── Final update ───────────────────────────────────────────────────────
        final_improved = self._composite_score(best_result) > self._composite_score(initial_result)
        final_status = "TARGETS MET" if self._meets_targets(best_result) else "MAX ITER REACHED"
        self._send_module_update(config, best_result, iteration, f"FINAL — {final_status}")

        logger.info(
            f"[{config.name}] Evolution done after {iteration} iterations. "
            f"improved={final_improved} | "
            f"WR={best_result.win_rate:.1%} RRR={best_result.avg_rrr:.2f} "
            f"PF={best_result.profit_factor:.2f} | {final_status}"
        )

        # Update evolver's tracked best_params if this module improved the score
        if final_improved:
            self.best_params = copy.deepcopy(best_params)

        return ModuleEvolveResult(
            module_name=config.name,
            initial_result=initial_result,
            final_result=best_result,
            iterations=iteration,
            improved=final_improved,
            best_params_snapshot=best_params.to_dict(),
        )

    # ── All 4 modules ──────────────────────────────────────────────────────────

    def evolve_all(self, pair: str = "XAUUSD") -> List[ModuleEvolveResult]:
        """Evolve all 4 trading modules sequentially.

        After all are done, sends a comprehensive Telegram summary.
        """
        results: List[ModuleEvolveResult] = []
        for config in TRADING_MODULE_CONFIGS:
            evolve_result = self.evolve_module(config, pair=pair)
            results.append(evolve_result)
            gc.collect()

        # Comprehensive final summary
        self._send_final_summary(results)

        # Log summary table
        logger.info("[ModuleEvolver] === EVOLUTION COMPLETE ===")
        for er in results:
            fr = er.final_result
            status = "PASS" if self._meets_targets(fr) else "FAIL"
            logger.info(
                f"  {er.module_name:<10} | iters={er.iterations:3d} "
                f"WR={fr.win_rate:.1%} RRR={fr.avg_rrr:.2f} PF={fr.profit_factor:.2f} "
                f"improved={er.improved} [{status}]"
            )

        return results

    # ── Telegram helpers ───────────────────────────────────────────────────────

    def _send_module_update(
        self,
        config: TradingModuleConfig,
        result: ModuleBacktestResult,
        iteration: int,
        status: str,
    ) -> None:
        """Format and send a Telegram progress message for one module."""
        wr_icon = "v" if result.win_rate >= WIN_RATE_TARGET else "x"
        rrr_icon = "v" if result.avg_rrr >= RRR_TARGET else "x"
        pf_icon = "v" if result.profit_factor >= PF_TARGET else "x"

        lines = [
            f"Module: {config.name.upper()} ({config.ltf1}->{config.ltf2})",
            f"Iteration: {iteration}",
            f"Status: {status}",
            f"Win Rate:      {result.win_rate:.1%}  Target: {WIN_RATE_TARGET:.0%}  [{wr_icon}]",
            f"Avg RRR:       {result.avg_rrr:.2f}   Target: {RRR_TARGET:.1f}  [{rrr_icon}]",
            f"Profit Factor: {result.profit_factor:.2f}   Target: {PF_TARGET:.1f}  [{pf_icon}]",
            f"Total Trades:  {result.total_trades}",
            f"Max DD:        {result.max_drawdown_pct:.1f}%",
            f"Return:        {result.total_return_pct:.1f}%",
        ]
        message = "\n".join(lines)
        logger.info(f"[ModuleEvolver] Telegram update:\n{message}")

        if self.telegram is not None:
            try:
                self.telegram.send(
                    subject=f"Evolution Update — {config.name.upper()}",
                    body=message,
                )
            except Exception as exc:
                logger.warning(f"[ModuleEvolver] Telegram send failed: {exc}")

    def _send_final_summary(self, results: List[ModuleEvolveResult]) -> None:
        """Send a comprehensive Telegram message covering all 4 modules."""
        lines = [
            "=== MODULE EVOLUTION — FINAL SUMMARY ===",
            "",
        ]
        for er in results:
            fr = er.final_result
            ir = er.initial_result
            targets_met = self._meets_targets(fr)
            outcome = "PASS" if targets_met else "FAIL"
            wr_icon = "v" if fr.win_rate >= WIN_RATE_TARGET else "x"
            rrr_icon = "v" if fr.avg_rrr >= RRR_TARGET else "x"
            pf_icon = "v" if fr.profit_factor >= PF_TARGET else "x"

            lines += [
                f"--- {er.module_name.upper()} ({fr.htf}/{fr.ltf1}/{fr.ltf2}) ---",
                f"Iterations:    {er.iterations}",
                f"Improved:      {'Yes' if er.improved else 'No'}",
                f"Win Rate:  {ir.win_rate:.1%} -> {fr.win_rate:.1%}  [{wr_icon}]",
                f"Avg RRR:   {ir.avg_rrr:.2f} -> {fr.avg_rrr:.2f}  [{rrr_icon}]",
                f"PF:        {ir.profit_factor:.2f} -> {fr.profit_factor:.2f}  [{pf_icon}]",
                f"Trades:        {fr.total_trades}",
                f"Max DD:        {fr.max_drawdown_pct:.1f}%",
                f"Return:        {fr.total_return_pct:.1f}%",
                f"Outcome:       {outcome}",
                "",
            ]

        n_pass = sum(1 for er in results if self._meets_targets(er.final_result))
        lines.append(f"Modules passing: {n_pass}/{len(results)}")
        message = "\n".join(lines)

        logger.info(f"[ModuleEvolver] Final summary:\n{message}")

        if self.telegram is not None:
            try:
                self.telegram.send(
                    subject="Evolution Complete — All Modules",
                    body=message,
                )
            except Exception as exc:
                logger.warning(f"[ModuleEvolver] Final summary Telegram send failed: {exc}")
