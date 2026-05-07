"""
AutoTrader Claude — Main orchestrator.

Modes:
  evolve    Run the full evolution loop (default)
  backtest  Run a single backtest on ACTIVE_PARAMS
  dashboard Start the Flask dashboard
  test      Run the unit test suite
"""

import argparse
import sys
from loguru import logger

from config import ACTIVE_PARAMS


def _setup_logging():
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        level="INFO",
        colorize=True,
    )
    logger.add(
        "C:\\AutoTraderClaude\\logs\\autotrader_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} | {message}",
    )


def cmd_evolve(args):
    from database.supabase_client import SupabaseClient
    from evolution.optimizer import Optimizer

    db = SupabaseClient()
    optimizer = Optimizer(db)
    pairs = args.pairs.split(",") if args.pairs else ["XAUUSD"]
    optimizer.evolve(max_iterations=args.iterations, pairs=pairs)


def cmd_backtest(args):
    import copy
    from backtester.engine import BacktestEngine
    from backtester.report import BacktestReport

    params = copy.deepcopy(ACTIVE_PARAMS)
    engine = BacktestEngine(params)
    result = engine.run(pair=args.pair)
    reporter = BacktestReport()
    path = reporter.generate(result)

    logger.info("─── Backtest Result ───")
    logger.info(f"Version     : v{result.strategy_version}")
    logger.info(f"Pair        : {result.pair}")
    logger.info(f"Win Rate    : {result.win_rate:.1%}")
    logger.info(f"Avg RRR     : {result.avg_rrr:.2f}")
    logger.info(f"Total Trades: {result.total_trades}")
    logger.info(f"Max DD      : {result.max_drawdown_pct:.2f}%")
    logger.info(f"Total Return: {result.total_return_pct:.2f}%")
    logger.info(f"Sharpe      : {result.sharpe_ratio:.2f}")
    logger.info(f"Profit Factor: {result.profit_factor:.2f}")
    logger.info(f"Report      : {path}")
    if result.overfitting_flag:
        logger.warning("⚠ Overfitting flag raised")
    if result.small_sample_flag:
        logger.warning("⚠ Small sample flag raised")


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


def main():
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="AutoTrader Claude — Evolutionary ICT Strategy System"
    )
    sub = parser.add_subparsers(dest="command")

    # evolve
    p_evolve = sub.add_parser("evolve", help="Run evolution loop")
    p_evolve.add_argument("--iterations", type=int, default=100,
                          help="Number of evolution iterations (default: 100)")
    p_evolve.add_argument("--pairs", type=str, default="XAUUSD",
                          help="Comma-separated pairs, e.g. XAUUSD,EURUSD")

    # backtest
    p_bt = sub.add_parser("backtest", help="Single backtest run")
    p_bt.add_argument("--pair", type=str, default="XAUUSD")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start Flask dashboard")
    p_dash.add_argument("--port", type=int, default=5000)

    # test
    sub.add_parser("test", help="Run unit tests")

    args = parser.parse_args()

    if args.command == "evolve" or args.command is None:
        if args.command is None:
            logger.info("No command given — defaulting to 'evolve'")
            args.iterations = 100
            args.pairs = "XAUUSD"
        cmd_evolve(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
