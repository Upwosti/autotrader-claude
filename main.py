"""
AutoTrader Claude — Main orchestrator.

Modes:
  evolve    Run the full evolution loop (default)
  backtest  Run a single backtest on ACTIVE_PARAMS
  dashboard Start the Flask dashboard
  live      Start live trading loop (MT5 required)
  test      Run the unit test suite
  check     Verify all connections (Telegram, Email, Claude, GitHub)
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
        "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\logs\\autotrader_{time:YYYY-MM-DD}.log",
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


def cmd_live(args):
    """Live trading loop using MT5 + ICT signals."""
    from core.mt5_connector import MT5Connector
    from core.data_manager import DataManager
    from core.scheduler import SystemScheduler
    from risk.ftmo_guardian import FTMOGuardian
    from risk.news_manager import NewsManager
    from execution.trade_executor import TradeExecutor
    from execution.trade_manager import TradeManager
    from database.supabase_client import SupabaseClient
    from database.logger import TradeLogger
    from alerts.telegram_bot import TelegramAlert
    from strategy.ict_engine import ICTEngine
    from config import ACTIVE_PARAMS, ALL_PAIRS

    db       = SupabaseClient()
    tg       = TelegramAlert()
    mt5      = MT5Connector()
    mt5.connect()

    account   = mt5.get_account_info()
    guardian  = FTMOGuardian(initial_balance=account.get("balance", 10000))
    news      = NewsManager()
    tlogger   = TradeLogger(db)
    executor  = TradeExecutor(mt5, guardian, news, tlogger)
    manager   = TradeManager(mt5, executor)
    data_mgr  = DataManager(mt5)

    def hourly_scan():
        account_now = mt5.get_account_info()
        manager.manage(account_now)

        for pair in ALL_PAIRS:
            df = data_mgr.get_ohlcv(pair, "H4")
            if df is None or df.empty:
                continue
            engine = ICTEngine(ACTIVE_PARAMS)
            setup  = engine.scan(df, pair)
            if setup:
                result = executor.execute(setup, account_now)
                if result.get("status") == "opened":
                    tg.send_trade_opened({**setup, "lot": result.get("lot", 0)})

    def ftmo_check():
        account_now = mt5.get_account_info()
        if not guardian.check(account_now.get("equity", 0)):
            executor.close_active()
            tg.send("FTMO HALT", guardian.halt_reason)

    scheduler = SystemScheduler(on_hourly_scan=hourly_scan, on_ftmo_check=ftmo_check)
    tg.start_listening(db=db)
    scheduler.start()
    tg.send_performance_report({
        "win_rate": 0, "avg_rrr": 0, "max_dd": 0,
        "total_return": 0, "total_trades": db.get_total_trades(),
        "version": db.get_current_version(),
    })
    tg.send("AutoTrader Claude Online", f"Live system started.\nDashboard: http://144.91.69.63:5000\nPairs: {', '.join(ALL_PAIRS)}\n\nUse /help to see bot commands.")
    logger.info("Live trading active. Press Ctrl+C to stop.")

    try:
        import time
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        scheduler.stop()
        mt5.disconnect()
        logger.info("Live trading stopped")


def cmd_check(_args):
    """Test all connections."""
    from alerts.telegram_bot import TelegramAlert
    from alerts.email_alert import EmailAlert
    from backup.github_backup import GitHubBackup
    import anthropic
    from config import ANTHROPIC_API_KEY

    print("\n=== AutoTrader Claude — Connection Check ===\n")

    # Telegram
    try:
        tg = TelegramAlert()
        tg.send("Connection Test", "AutoTrader Claude system check OK")
        print("  Telegram   : OK")
    except Exception as e:
        print(f"  Telegram   : FAIL ({e})")

    # Email
    try:
        em = EmailAlert()
        em.send("AutoTrader Connection Test", "System check passed.")
        print("  Email      : OK")
    except Exception as e:
        print(f"  Email      : FAIL ({e})")

    # Anthropic
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        txt = resp.content[0].text.strip().encode("ascii", "replace").decode()
        print(f"  Claude API : OK ({txt})")
    except Exception as e:
        print(f"  Claude API : FAIL ({e})")

    # GitHub
    try:
        gh = GitHubBackup()
        print(f"  GitHub     : {'configured' if gh.enabled else 'not configured'}")
    except Exception as e:
        print(f"  GitHub     : FAIL ({e})")

    print("\nDone.\n")


def cmd_test(_args):
    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def cmd_auto(args):
    """24-hour autonomous evolution loop — targets 80%+ win rate."""
    from database.supabase_client import SupabaseClient
    from evolution.autonomous_loop import AutonomousLoop

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    db    = SupabaseClient()
    loop  = AutonomousLoop(db=db, pairs=pairs, max_hours=args.hours)
    loop.run()


def cmd_wfbacktest(args):
    """Walk-forward backtest with 70/30 split on D1 data (10 years)."""
    from backtester.walk_forward import WalkForwardBacktester
    from strategy.trend_engine import TrendParams
    import yfinance as yf

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    params = TrendParams()

    TICKER_MAP = {
        "XAUUSD": "GC=F", "GBPUSD": "GBPUSD=X",
        "EURUSD": "EURUSD=X", "BTCUSD": "BTC-USD",
    }

    pair_data = {}
    for pair in pairs:
        ticker = TICKER_MAP.get(pair, pair)
        logger.info(f"Downloading {pair} ({ticker}) …")
        try:
            d1 = yf.download(ticker, period="10y", interval="1d",
                             progress=False, auto_adjust=True)
            w1 = yf.download(ticker, period="10y", interval="1wk",
                             progress=False, auto_adjust=True)
            for raw in (d1, w1):
                if hasattr(raw.columns, "levels"):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [c.lower() for c in raw.columns]
            d1 = d1[["open","high","low","close","volume"]].dropna()
            w1 = w1[["open","high","low","close","volume"]].dropna()
            if d1.index.tzinfo is not None:
                d1.index = d1.index.tz_localize(None)
            if w1.index.tzinfo is not None:
                w1.index = w1.index.tz_localize(None)
            pair_data[pair] = (d1, w1)
            logger.info(f"  {pair}: {len(d1)} D1 bars, {len(w1)} W1 bars")
        except Exception as e:
            logger.error(f"  {pair}: download failed — {e}")

    if not pair_data:
        logger.error("No data downloaded. Check internet / yfinance.")
        return

    tester = WalkForwardBacktester(params)
    results = tester.run_multi_pair(pair_data)
    agg = tester.aggregate(results)

    logger.info("─" * 55)
    logger.info("WALK-FORWARD BACKTEST RESULTS (70% train / 30% test)")
    logger.info("─" * 55)
    for pair, r in results.items():
        s = r.summary()
        logger.info(
            f"{pair:8s} | Test WR={s['test_win_rate']:.1%} "
            f"Trades={s['test_trades']:4d} | "
            f"PF={s['test_profit_factor']:.2f} "
            f"DD={s['test_max_dd_pct']:.1f}% "
            f"Sharpe={s['test_sharpe']:.2f} | "
            f"Train WR={s['train_win_rate']:.1%} "
            f"| Score={s['composite_score']:.3f}"
        )
    logger.info("─" * 55)
    logger.info(
        f"AGGREGATE | WR={agg['aggregate_win_rate']:.1%} "
        f"Trades={agg['total_test_trades']} "
        f"PF={agg['aggregate_profit_factor']:.2f} "
        f"RRR={agg['aggregate_avg_rrr']:.2f} "
        f"Score={agg['aggregate_score']:.3f}"
    )
    if agg['aggregate_win_rate'] >= 0.80 and agg['total_test_trades'] >= 500:
        logger.info("★ TARGET ACHIEVED: 80%+ win rate on 500+ trades!")
    elif agg['aggregate_win_rate'] >= 0.65:
        logger.info("Progress: WR above 65% — keep evolving")
    else:
        logger.info(f"Current gap: need {0.80 - agg['aggregate_win_rate']:.1%} more WR")


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

    # live
    sub.add_parser("live", help="Start live trading (MT5 required)")

    # check
    sub.add_parser("check", help="Test all connections")

    # test
    sub.add_parser("test", help="Run unit tests")

    # auto — 24-hour autonomous evolution loop
    p_auto = sub.add_parser("auto", help="24-hour autonomous evolution (80%+ WR target)")
    p_auto.add_argument("--hours", type=float, default=24.0,
                        help="Max runtime in hours (default: 24)")
    p_auto.add_argument("--pairs", type=str,
                        default="XAUUSD,GBPUSD,EURUSD,BTCUSD",
                        help="Comma-separated pairs to evolve on")

    # wfbacktest — walk-forward backtest (new strategy)
    p_wf = sub.add_parser("wfbacktest", help="Walk-forward backtest on D1 data")
    p_wf.add_argument("--pairs", type=str,
                      default="XAUUSD,GBPUSD,EURUSD,BTCUSD")

    args = parser.parse_args()

    if args.command == "evolve" or args.command is None:
        if args.command is None:
            logger.info("No command given — defaulting to 'evolve'")
            args.iterations = 100
            args.pairs = "XAUUSD"
        cmd_evolve(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "wfbacktest":
        cmd_wfbacktest(args)
    elif args.command == "auto":
        cmd_auto(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "live":
        cmd_live(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

