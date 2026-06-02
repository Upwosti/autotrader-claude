"""
AutoTrader Claude — Main orchestrator.

Modes:
  evolve-modules  Backtest all 4 modules → auto-evolve until profitable → Telegram report
  live            Run live (paper or MT5) market execution
  evolve          Run the classic full evolution loop
  backtest        Run a single backtest on ACTIVE_PARAMS
  dashboard       Start the Flask dashboard
  test            Run the unit test suite
"""

import argparse
import copy
import os
import sys
from loguru import logger

from config import ACTIVE_PARAMS

_LOG_DIR = os.path.join(os.path.expanduser("~"), "autotrader_logs")
os.makedirs(_LOG_DIR, exist_ok=True)


def _setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        level="INFO",
        colorize=True,
    )
    logger.add(
        os.path.join(_LOG_DIR, "autotrader_{time:YYYY-MM-DD}.log"),
        rotation="00:00",
        retention="14 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    )


# ── evolve-modules: backtest all 4, auto-improve, report to Telegram ───────────

def cmd_evolve_modules(args):
    """Run all 4 module backtests, auto-evolve underperformers, send Telegram report."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'execution'))
    from backtester.module_backtester import ModuleBacktester
    from evolution.module_evolver import ModuleEvolver
    from alerts.telegram_bot import TelegramAlert
    from config import BACKTEST_INITIAL_CAPITAL

    telegram = TelegramAlert()
    params = copy.deepcopy(ACTIVE_PARAMS)
    pair = args.pair

    # ── Step 1: Initial backtest of all 4 modules ──────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 1: Running initial backtests for all 4 modules...")
    logger.info("=" * 60)
    backtester = ModuleBacktester(params)
    results = backtester.run_all(pair=pair, n_bars=args.bars)

    balance = BACKTEST_INITIAL_CAPITAL
    telegram.send_module_report(results, balance=balance)

    for r in results:
        status = "PASS" if r.is_profitable else "NEEDS WORK"
        logger.info(
            f"[{status}] {r.module_name:10s} WR={r.win_rate:.1%} "
            f"RRR={r.avg_rrr:.2f} PF={r.profit_factor:.2f} "
            f"Trades={r.total_trades} Return={r.total_return_pct:+.1f}%"
        )

    # ── Step 2: Evolve underperforming modules ─────────────────────────────
    needs_improvement = [r for r in results if r.needs_improvement]
    if needs_improvement:
        logger.info("=" * 60)
        logger.info(f"STEP 2: Evolving {len(needs_improvement)} underperforming module(s)...")
        logger.info("=" * 60)
        evolver = ModuleEvolver(params=params, telegram=telegram)
        evolve_results = evolver.evolve_all(pair=pair)

        # final_result from each ModuleEvolveResult is already the best backtested result
        final_results = [er.final_result for er in evolve_results]
    else:
        logger.info("All modules already profitable — no evolution needed!")
        final_results = results
        if hasattr(telegram, 'send_all_profitable'):
            telegram.send_all_profitable(final_results, balance=balance)

    # ── Step 3: Final report ───────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("FINAL RESULTS:")
    logger.info("=" * 60)
    all_pass = True
    for r in final_results:
        ok = r.is_profitable
        if not ok:
            all_pass = False
        status = "✅ PASS" if ok else "❌ FAIL"
        logger.info(
            f"{status} [{r.module_name}] WR={r.win_rate:.1%} "
            f"RRR={r.avg_rrr:.2f} PF={r.profit_factor:.2f} "
            f"DD={r.max_drawdown_pct:.1f}% Return={r.total_return_pct:+.1f}%"
        )

    telegram.send_module_report(final_results, balance=balance)
    logger.info(f"Balance: ${balance:,.2f}")
    logger.info("evolve-modules complete.")


# ── live: run all 4 modules in live/paper mode ─────────────────────────────────

def cmd_live(args):
    """Start live market controller (paper mode by default, MT5 if --mt5 flag)."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    from execution.live_controller import LiveController

    params = copy.deepcopy(ACTIVE_PARAMS)
    mode = "mt5" if args.mt5 else "paper"
    controller = LiveController(
        params=params,
        pair=args.pair,
        mode=mode,
        poll_interval=args.interval,
        balance_interval=args.balance_interval,
        max_open_trades=4,
        risk_pct=args.risk,
    )

    balance = controller.get_balance()
    logger.info(f"Starting live controller | mode={mode} | pair={args.pair} | balance=${balance:,.2f}")
    controller.run()


# ── Legacy commands ─────────────────────────────────────────────────────────────

def cmd_evolve(args):
    from database.supabase_client import SupabaseClient
    from evolution.optimizer import Optimizer

    db = SupabaseClient()
    optimizer = Optimizer(db)
    pairs = args.pairs.split(",") if args.pairs else ["XAUUSD"]
    optimizer.evolve(max_iterations=args.iterations, pairs=pairs)


def cmd_backtest(args):
    from backtester.engine import BacktestEngine
    from backtester.report import BacktestReport

    params = copy.deepcopy(ACTIVE_PARAMS)
    engine = BacktestEngine(params)
    result = engine.run(pair=args.pair)
    reporter = BacktestReport()
    path = reporter.generate(result)

    logger.info("─── Backtest Result ───")
    logger.info(f"Win Rate    : {result.win_rate:.1%}")
    logger.info(f"Avg RRR     : {result.avg_rrr:.2f}")
    logger.info(f"Total Trades: {result.total_trades}")
    logger.info(f"Max DD      : {result.max_drawdown_pct:.2f}%")
    logger.info(f"Expectancy  : {result.expectancy:.2f} pips")
    logger.info(f"MC Survival : {result.monte_carlo_survival:.1%}")
    logger.info(f"WF Stability: {result.walk_forward_stability:.1%}")
    logger.info(f"Composite   : {result.composite_score():.3f}")
    logger.info(f"Report      : {path}")
    if result.overfitting_flag:
        logger.warning("Overfitting flag raised")


def cmd_dashboard(args):
    from database.supabase_client import SupabaseClient
    from dashboard.app import create_app

    db = SupabaseClient()
    app = create_app(db)
    logger.info(f"Dashboard running at http://0.0.0.0:{args.port}")
    app.run(host="0.0.0.0", port=args.port, debug=False)


def cmd_test(_args):
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ── Entry point ─────────────────────────────────────────────────────────────────

def main():
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="AutoTrader Claude — ICT Multi-Module Trading System"
    )
    sub = parser.add_subparsers(dest="command")

    # evolve-modules (PRIMARY: runs all 4, auto-evolves, Telegrams)
    p_em = sub.add_parser("evolve-modules",
                           help="Backtest all 4 modules, auto-evolve until profitable, report to Telegram")
    p_em.add_argument("--pair", type=str, default="XAUUSD")
    p_em.add_argument("--bars", type=int, default=2000,
                      help="Number of LTF2 bars per module backtest (default: 2000)")

    # live
    p_live = sub.add_parser("live", help="Run live trading (paper or MT5)")
    p_live.add_argument("--pair", type=str, default="XAUUSD")
    p_live.add_argument("--mt5", action="store_true", help="Use MT5 (default: paper mode)")
    p_live.add_argument("--interval", type=int, default=60,
                        help="Poll interval in seconds (default: 60)")
    p_live.add_argument("--balance-interval", type=int, default=3600,
                        help="Balance report interval in seconds (default: 3600)")
    p_live.add_argument("--risk", type=float, default=1.0,
                        help="Risk per trade in %% (default: 1.0)")

    # evolve (legacy)
    p_evolve = sub.add_parser("evolve", help="Run classic evolution loop")
    p_evolve.add_argument("--iterations", type=int, default=100)
    p_evolve.add_argument("--pairs", type=str, default="XAUUSD")

    # backtest
    p_bt = sub.add_parser("backtest", help="Single backtest")
    p_bt.add_argument("--pair", type=str, default="XAUUSD")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start Flask dashboard")
    p_dash.add_argument("--port", type=int, default=5000)

    # test
    sub.add_parser("test", help="Run unit tests")

    args = parser.parse_args()

    if args.command == "evolve-modules":
        cmd_evolve_modules(args)
    elif args.command == "live":
        cmd_live(args)
    elif args.command == "evolve":
        cmd_evolve(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        logger.info("No command given — defaulting to 'evolve-modules'")
        args.pair = "XAUUSD"
        args.bars = 2000
        cmd_evolve_modules(args)


if __name__ == "__main__":
    main()
