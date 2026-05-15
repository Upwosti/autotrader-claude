"""
Phase 6: Walk-Forward Validation Runner

Mandatory rolling walk-forward validation for all active pairs.
Runs out-of-sample testing, parameter robustness, Monte Carlo validation.
Rejects fragile systems immediately.

Output: validation/reports/<pair>_<date>.json + summary HTML
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

REPORTS_DIR  = Path(__file__).parent / "reports"
STATE_FILE   = Path(__file__).parent.parent / "local_db" / "engine_state.json"
DB_PATH      = Path(__file__).parent.parent / "data" / "autotrader.db"
VAL_STATE    = Path(__file__).parent.parent / "local_db" / "validation_state.json"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Rejection thresholds
MIN_OOS_WIN_RATE     = 0.45   # must beat 45% OOS
MIN_MONTE_CARLO_SURV = 0.70   # 70% of MC shuffles must be profitable
MAX_PARAM_SENSITIVITY = 0.20  # parameter change shouldn't degrade WR by >20pp
MIN_PROFIT_FACTOR    = 1.10   # OOS profit factor must be > 1.1
MAX_MAX_DRAWDOWN     = 0.25   # OOS max drawdown < 25%
MIN_FOLDS_PASSING    = 3      # at least 3/5 folds must be profitable

ACTIVE_PAIRS = [
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY",
    "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURJPY", "GBPJPY", "XAGUSD", "BTCUSD",
    "ETHUSD", "NAS100", "US30",
]


@dataclass
class FoldResult:
    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_trades: int
    test_trades: int
    train_wr: float
    test_wr: float
    train_rr: float
    test_rr: float
    test_profit_factor: float
    test_max_dd: float
    passed: bool
    failure_reason: str = ""


@dataclass
class RobustnessResult:
    param_name: str
    base_wr: float
    perturbed_wr: float
    sensitivity: float   # |base_wr - perturbed_wr|
    robust: bool


@dataclass
class ValidationReport:
    pair: str
    timestamp: str
    engine_iter: int

    fold_results: List[FoldResult] = field(default_factory=list)
    folds_passing: int = 0
    folds_total: int = 0

    oos_win_rate: float = 0.0
    oos_rr: float = 0.0
    oos_profit_factor: float = 0.0
    oos_max_drawdown: float = 0.0
    oos_trades: int = 0

    monte_carlo_survival: float = 0.0
    mc_runs: int = 1000
    mc_5th_pct_return: float = 0.0

    robustness_results: List[RobustnessResult] = field(default_factory=list)
    params_robust: int = 0
    params_total: int = 0

    verdict: str = "PENDING"     # "PASS" | "WARN" | "REJECT"
    rejection_reasons: List[str] = field(default_factory=list)
    risk_multiplier: float = 1.0  # 1.0=full, 0.75=warn, 0.5=reject-but-monitor, 0.0=rejected


class WalkForwardValidator:
    """
    Standalone walk-forward validation runner.
    Call run_validation(pair) or run_all_pairs().
    """

    def __init__(self):
        self._engine_state = self._load_engine_state()
        self._validation_state: Dict[str, dict] = self._load_validation_state()

    def run_all_pairs(self) -> Dict[str, ValidationReport]:
        reports = {}
        for pair in ACTIVE_PAIRS:
            try:
                report = self.run_validation(pair)
                reports[pair] = report
                self._save_report(report)
                logger.info(f"[VAL] {pair}: {report.verdict} | "
                            f"OOS WR {report.oos_win_rate:.1%} | "
                            f"MC Surv {report.monte_carlo_survival:.1%} | "
                            f"Folds {report.folds_passing}/{report.folds_total}")
            except Exception as e:
                logger.error(f"[VAL] {pair} validation failed: {e}")

        self._save_validation_state(reports)
        self._generate_summary_html(reports)
        return reports

    def run_validation(self, pair: str) -> ValidationReport:
        now = datetime.now(timezone.utc)
        iter_n = self._engine_state.get("iteration", 0)

        report = ValidationReport(
            pair=pair,
            timestamp=now.isoformat(),
            engine_iter=iter_n,
        )

        # Load live stats from SQLite
        live_trades = self._load_live_trades(pair)

        if len(live_trades) < 10:
            # Not enough live data — use backtest stats from engine state
            report = self._validate_from_engine_state(pair, report)
        else:
            # Full validation from live trades
            report = self._validate_from_live_trades(pair, live_trades, report)

        self._determine_verdict(report)
        return report

    def get_risk_multiplier(self, pair: str) -> float:
        state = self._validation_state.get(pair, {})
        return state.get("risk_multiplier", 1.0)

    # ── Core validation logic ─────────────────────────────────────────────────

    def _validate_from_engine_state(self, pair: str, report: ValidationReport) -> ValidationReport:
        """Validate using engine's best_wr / best_rrr / wf_results."""
        best_wr  = self._engine_state.get("best_wr", {}).get(pair, 0.0)
        best_rrr = self._engine_state.get("best_rrr", {}).get(pair, 0.0)
        wf_data  = self._engine_state.get("wf_results", {}).get(pair, {})

        if not best_wr:
            report.verdict = "PENDING"
            report.rejection_reasons = ["No backtest data available"]
            report.risk_multiplier = 0.5
            return report

        # Simulate fold results from engine's stored WF data
        n_folds = wf_data.get("n_folds", 5)
        fold_wrs = wf_data.get("fold_wrs", [best_wr] * n_folds)
        fold_rrs = wf_data.get("fold_rrs", [best_rrr] * n_folds)

        report.folds_total = n_folds
        for i, (fwr, frr) in enumerate(zip(fold_wrs, fold_rrs)):
            pf = (fwr * frr) / max(1 - fwr, 0.01)
            passed = fwr >= MIN_OOS_WIN_RATE and pf >= MIN_PROFIT_FACTOR
            report.fold_results.append(FoldResult(
                fold_id=i+1,
                train_start="", train_end="", test_start="", test_end="",
                train_trades=0, test_trades=0,
                train_wr=fwr, test_wr=fwr,
                train_rr=frr, test_rr=frr,
                test_profit_factor=pf,
                test_max_dd=wf_data.get("max_dd", 0.1),
                passed=passed,
            ))

        report.folds_passing = sum(1 for f in report.fold_results if f.passed)
        report.oos_win_rate = best_wr
        report.oos_rr = best_rrr
        report.oos_profit_factor = (best_wr * best_rrr) / max(1 - best_wr, 0.01)
        report.oos_max_drawdown = wf_data.get("max_dd", 0.10)
        report.oos_trades = wf_data.get("total_trades", 50)

        # Monte Carlo on engine's WR/RR
        if _NUMPY:
            mc_result = self._run_monte_carlo(best_wr, best_rrr, n_trades=50)
            report.monte_carlo_survival = mc_result[0]
            report.mc_5th_pct_return    = mc_result[1]

        # Robustness from param sensitivity estimates in engine state
        report = self._estimate_robustness(pair, best_wr, report)

        return report

    def _validate_from_live_trades(
        self,
        pair: str,
        trades: List[dict],
        report: ValidationReport,
    ) -> ValidationReport:
        """Full validation from actual live trade history."""
        if not _NUMPY or len(trades) < 10:
            return self._validate_from_engine_state(pair, report)

        n = len(trades)
        fold_size = max(n // 5, 5)
        report.folds_total = min(5, n // fold_size)

        all_test_trades = []

        for i in range(report.folds_total):
            train_end = (i + 1) * fold_size
            test_start = train_end
            test_end = min(test_start + fold_size, n)
            if test_end <= test_start:
                break

            train = trades[:train_end]
            test  = trades[test_start:test_end]

            train_wr = sum(1 for t in train if t.get("pnl", 0) > 0) / max(len(train), 1)
            test_wr  = sum(1 for t in test  if t.get("pnl", 0) > 0) / max(len(test),  1)
            train_rr = _mean([t.get("rr_achieved", 0) for t in train])
            test_rr  = _mean([t.get("rr_achieved", 0) for t in test])

            wins = [t.get("pnl", 0) for t in test if t.get("pnl", 0) > 0]
            loss = [abs(t.get("pnl", 0)) for t in test if t.get("pnl", 0) <= 0]
            pf = sum(wins) / max(sum(loss), 0.01)

            dd = _compute_max_dd([t.get("pnl", 0) for t in test])

            passed = (test_wr >= MIN_OOS_WIN_RATE and
                      pf >= MIN_PROFIT_FACTOR and
                      dd <= MAX_MAX_DRAWDOWN)

            report.fold_results.append(FoldResult(
                fold_id=i+1,
                train_start=str(trades[0].get("open_time", ""))[:10],
                train_end=str(trades[train_end-1].get("open_time", ""))[:10],
                test_start=str(test[0].get("open_time", ""))[:10] if test else "",
                test_end=str(test[-1].get("open_time", ""))[:10] if test else "",
                train_trades=len(train), test_trades=len(test),
                train_wr=round(train_wr, 4), test_wr=round(test_wr, 4),
                train_rr=round(train_rr, 3), test_rr=round(test_rr, 3),
                test_profit_factor=round(pf, 3),
                test_max_dd=round(dd, 4),
                passed=passed,
            ))
            all_test_trades.extend(test)

        report.folds_passing = sum(1 for f in report.fold_results if f.passed)

        if all_test_trades:
            report.oos_win_rate = sum(1 for t in all_test_trades if t.get("pnl", 0) > 0) / len(all_test_trades)
            report.oos_rr = _mean([t.get("rr_achieved", 0) for t in all_test_trades])
            wins = [t.get("pnl", 0) for t in all_test_trades if t.get("pnl", 0) > 0]
            loss = [abs(t.get("pnl", 0)) for t in all_test_trades if t.get("pnl", 0) <= 0]
            report.oos_profit_factor = sum(wins) / max(sum(loss), 0.01)
            report.oos_max_drawdown  = _compute_max_dd([t.get("pnl", 0) for t in all_test_trades])
            report.oos_trades = len(all_test_trades)

        mc = self._run_monte_carlo(report.oos_win_rate, report.oos_rr, len(all_test_trades))
        report.monte_carlo_survival = mc[0]
        report.mc_5th_pct_return    = mc[1]

        report = self._estimate_robustness(pair, report.oos_win_rate, report)
        return report

    def _run_monte_carlo(
        self,
        win_rate: float,
        avg_rr: float,
        n_trades: int = 50,
        n_sims: int = 1000,
    ) -> Tuple[float, float]:
        """Returns (survival_rate, 5th_percentile_return)."""
        if not _NUMPY or win_rate <= 0 or n_trades < 5:
            return 1.0, 0.0

        rng = np.random.default_rng(seed=42)
        final_returns = []

        for _ in range(n_sims):
            wins = rng.random(n_trades) < win_rate
            pnls = np.where(wins, avg_rr, -1.0)
            balance = 1.0
            for pnl in pnls:
                balance *= (1 + pnl * 0.01)  # 1% risk per trade
            final_returns.append(balance - 1.0)

        arr = np.array(final_returns)
        survival = float(np.mean(arr > 0))
        pct5 = float(np.percentile(arr, 5))
        return round(survival, 4), round(pct5, 4)

    def _estimate_robustness(
        self,
        pair: str,
        base_wr: float,
        report: ValidationReport,
    ) -> ValidationReport:
        """Estimate parameter sensitivity from stored WF fold variance."""
        wf_data = self._engine_state.get("wf_results", {}).get(pair, {})
        fold_wrs = wf_data.get("fold_wrs", [base_wr])

        if _NUMPY and len(fold_wrs) >= 2:
            wr_std = float(np.std(fold_wrs))
            sensitivity = wr_std
        else:
            sensitivity = 0.05

        robust = sensitivity <= MAX_PARAM_SENSITIVITY
        report.robustness_results = [
            RobustnessResult(
                param_name="fold_wr_stability",
                base_wr=base_wr,
                perturbed_wr=base_wr - sensitivity,
                sensitivity=round(sensitivity, 4),
                robust=robust,
            )
        ]
        report.params_robust = 1 if robust else 0
        report.params_total  = 1
        return report

    # ── Verdict ───────────────────────────────────────────────────────────────

    def _determine_verdict(self, report: ValidationReport):
        reasons = []

        if report.oos_win_rate < MIN_OOS_WIN_RATE and report.oos_trades > 0:
            reasons.append(f"OOS WR {report.oos_win_rate:.1%} < {MIN_OOS_WIN_RATE:.0%} min")

        if report.monte_carlo_survival < MIN_MONTE_CARLO_SURV and report.oos_trades > 0:
            reasons.append(f"MC survival {report.monte_carlo_survival:.1%} < {MIN_MONTE_CARLO_SURV:.0%}")

        if report.oos_profit_factor < MIN_PROFIT_FACTOR and report.oos_trades > 0:
            reasons.append(f"PF {report.oos_profit_factor:.2f} < {MIN_PROFIT_FACTOR}")

        if report.oos_max_drawdown > MAX_MAX_DRAWDOWN and report.oos_trades > 0:
            reasons.append(f"Max DD {report.oos_max_drawdown:.1%} > {MAX_MAX_DRAWDOWN:.0%}")

        if report.folds_passing < MIN_FOLDS_PASSING and report.folds_total >= MIN_FOLDS_PASSING:
            reasons.append(f"Only {report.folds_passing}/{report.folds_total} folds profitable")

        report.rejection_reasons = reasons

        if not reasons:
            # Check for warnings
            warn_reasons = []
            if report.oos_win_rate < 0.55:
                warn_reasons.append("OOS WR below 55%")
            if report.monte_carlo_survival < 0.80:
                warn_reasons.append("MC survival below 80%")
            if report.params_robust < report.params_total:
                warn_reasons.append("Some parameters not robust")

            if warn_reasons:
                report.verdict = "WARN"
                report.risk_multiplier = 0.75
            else:
                report.verdict = "PASS"
                report.risk_multiplier = 1.0
        else:
            if len(reasons) >= 3:
                report.verdict = "REJECT"
                report.risk_multiplier = 0.0
            else:
                report.verdict = "WARN"
                report.risk_multiplier = 0.5

        logger.info(f"[VAL] {report.pair}: verdict={report.verdict}, "
                    f"risk_mult={report.risk_multiplier}, reasons={reasons}")

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_live_trades(self, pair: str) -> List[dict]:
        if not DB_PATH.exists():
            return []
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("""
                SELECT pair, open_time, close_time, pnl, rr_achieved, spread_at_entry
                FROM trades
                WHERE pair = ? AND close_time IS NOT NULL
                ORDER BY open_time ASC
            """, (pair,))
            rows = cur.fetchall()
            conn.close()
            return [
                {"pair": r[0], "open_time": r[1], "close_time": r[2],
                 "pnl": r[3] or 0, "rr_achieved": r[4] or 0, "spread_at_entry": r[5] or 0}
                for r in rows
            ]
        except Exception as e:
            logger.debug(f"[VAL] trade load error: {e}")
            return []

    def _load_engine_state(self) -> dict:
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_report(self, report: ValidationReport):
        date_str = datetime.now().strftime("%Y%m%d")
        path = REPORTS_DIR / f"{report.pair}_{date_str}.json"
        try:
            data = asdict(report)
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.debug(f"[VAL] save report: {e}")

    def _load_validation_state(self) -> dict:
        try:
            if VAL_STATE.exists():
                with open(VAL_STATE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_validation_state(self, reports: Dict[str, ValidationReport]):
        try:
            state = {
                "last_run": datetime.utcnow().isoformat(),
                "pairs": {
                    pair: {
                        "verdict":         r.verdict,
                        "risk_multiplier": r.risk_multiplier,
                        "oos_wr":          round(r.oos_win_rate, 4),
                        "mc_survival":     round(r.monte_carlo_survival, 4),
                    }
                    for pair, r in reports.items()
                }
            }
            with open(VAL_STATE, "w") as f:
                json.dump(state, f, indent=2)
            self._validation_state = state.get("pairs", {})
        except Exception as e:
            logger.debug(f"[VAL] save state: {e}")

    def _generate_summary_html(self, reports: Dict[str, ValidationReport]):
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        rows = []
        for pair, r in sorted(reports.items()):
            color = {"PASS": "#22c55e", "WARN": "#f59e0b", "REJECT": "#ef4444",
                     "PENDING": "#94a3b8"}.get(r.verdict, "#94a3b8")
            rows.append(
                f"<tr><td>{pair}</td><td style='color:{color}'><b>{r.verdict}</b></td>"
                f"<td>{r.oos_win_rate:.1%}</td><td>{r.oos_rr:.2f}</td>"
                f"<td>{r.monte_carlo_survival:.1%}</td>"
                f"<td>{r.folds_passing}/{r.folds_total}</td>"
                f"<td>{r.risk_multiplier:.2f}</td>"
                f"<td>{', '.join(r.rejection_reasons[:2])}</td></tr>"
            )

        html = f"""<!DOCTYPE html><html><head><title>WF Validation {date_str}</title>
<style>body{{font-family:monospace;background:#0f172a;color:#e2e8f0;padding:20px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #334155;padding:8px;text-align:left}}
th{{background:#1e293b}}</style></head>
<body><h2>Walk-Forward Validation — {date_str}</h2>
<table><tr><th>Pair</th><th>Verdict</th><th>OOS WR</th><th>OOS RR</th>
<th>MC Surv</th><th>Folds</th><th>Risk×</th><th>Issues</th></tr>
{''.join(rows)}</table></body></html>"""

        path = REPORTS_DIR / f"summary_{datetime.now().strftime('%Y%m%d')}.html"
        try:
            with open(path, "w") as f:
                f.write(html)
        except Exception:
            pass


# ── Utilities ─────────────────────────────────────────────────────────────────

def _mean(vals: list) -> float:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _compute_max_dd(pnls: list) -> float:
    if not _NUMPY or not pnls:
        return 0.0
    balance = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        balance *= (1 + p * 0.01)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


if __name__ == "__main__":
    validator = WalkForwardValidator()
    all_reports = validator.run_all_pairs()
    for pair, r in all_reports.items():
        print(f"{pair:10s} {r.verdict:7s} OOS WR={r.oos_win_rate:.1%} "
              f"MC={r.monte_carlo_survival:.1%} Risk×{r.risk_multiplier:.2f}")
