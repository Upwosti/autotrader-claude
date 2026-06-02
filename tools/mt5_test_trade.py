"""
MT5 Test Trade — Buy XAUUSD 0.1 lot then close immediately.

Works on:
  - Windows (native MetaTrader5 package)
  - Linux/cloud (via mt5linux bridge to Windows machine)

Linux setup:
  1. On Windows: run tools/mt5_windows_server.py
  2. Set env var: export MT5_HOST=<windows-ip>
  3. Run this script from Linux

Windows setup:
  python tools/mt5_test_trade.py
"""

import sys
import os
import time

# Allow running from project root or from tools/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'autotrader_claude'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SYMBOL = "XAUUSD"
LOT    = 0.1
MAGIC  = 99999


def get_mt5():
    """Get MT5 API via bridge (native or mt5linux)."""
    # Try native first
    try:
        import MetaTrader5 as mt5
        print("Using native MetaTrader5 package")
        return mt5
    except ImportError:
        pass

    # Try mt5linux bridge
    try:
        from mt5linux import MetaTrader5
        host = os.getenv("MT5_HOST", "localhost")
        port = int(os.getenv("MT5_PORT", "18812"))
        print(f"Connecting via mt5linux bridge at {host}:{port} ...")
        mt5 = MetaTrader5(host=host, port=port)
        mt5.version()   # ping
        print(f"mt5linux bridge connected at {host}:{port}")
        return mt5
    except ImportError:
        print("ERROR: Neither MetaTrader5 nor mt5linux is installed.")
        print("Windows: pip install MetaTrader5")
        print("Linux:   pip install mt5linux  (then set MT5_HOST=<windows-ip>)")
        return None
    except Exception as e:
        host = os.getenv("MT5_HOST", "localhost")
        print(f"ERROR: Cannot connect to mt5linux bridge at {host}: {e}")
        print(f"Make sure tools/mt5_windows_server.py is running on {host}")
        return None


def connect(mt5) -> bool:
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if info is None:
        print(f"Account info failed: {mt5.last_error()}")
        return False
    print(f"\nConnected:")
    print(f"  Account: {info.login}  Broker: {info.company}")
    print(f"  Balance: {info.balance:.2f}  Equity: {info.equity:.2f}")
    print(f"  Server:  {info.server}")
    return True


def buy_xauusd(mt5):
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print(f"Cannot get tick for {SYMBOL}: {mt5.last_error()}")
        return None

    price = tick.ask
    print(f"\nOpening BUY {SYMBOL}  {LOT} lot  @ {price:.2f}")
    print(f"  Bid={tick.bid:.2f}  Ask={tick.ask:.2f}  Spread={tick.ask - tick.bid:.2f}")

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       LOT,
        "type":         mt5.ORDER_TYPE_BUY,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC,
        "comment":      "AutoTrader-test",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        print(f"order_send returned None: {mt5.last_error()}")
        return None

    print(f"  Retcode: {result.retcode}  Order: {result.order}  Deal: {result.deal}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"FAILED: {result.comment}")
        return None

    print(f"BUY OPENED — ticket={result.order}")
    return result.order


def close_position(mt5, ticket):
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        print(f"No open positions for {SYMBOL}")
        return False

    pos = next((p for p in positions if p.magic == MAGIC), None)
    if pos is None:
        print("Test position not found (magic number mismatch?)")
        return False

    tick = mt5.symbol_info_tick(SYMBOL)
    close_price = tick.bid

    print(f"\nClosing  ticket={pos.ticket}  current_bid={close_price:.2f}")
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       pos.volume,
        "type":         mt5.ORDER_TYPE_SELL,
        "position":     pos.ticket,
        "price":        close_price,
        "deviation":    20,
        "magic":        MAGIC,
        "comment":      "AutoTrader-test-close",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = mt5.last_error() if result is None else result.comment
        print(f"CLOSE FAILED: {err}")
        return False

    print(f"CLOSED — P&L: {pos.profit:+.2f}  spread cost: {tick.ask - tick.bid:.2f}")
    return True


def main():
    print("=" * 55)
    print("MT5 CONNECTION TEST — XAUUSD 0.1 lot buy+close")
    print("=" * 55)

    mt5 = get_mt5()
    if mt5 is None:
        sys.exit(1)

    if not connect(mt5):
        mt5.shutdown() if hasattr(mt5, 'shutdown') else None
        sys.exit(1)

    ticket = buy_xauusd(mt5)
    if ticket is None:
        mt5.shutdown()
        sys.exit(1)

    print("\nWaiting 2 seconds before closing...")
    time.sleep(2)

    closed = close_position(mt5, ticket)
    mt5.shutdown()

    print("\n" + "=" * 55)
    if closed:
        print("TEST PASSED — MT5 buy/close works correctly.")
        print("Run live mode: python main.py live --pair XAUUSD --mode mt5")
    else:
        print("TEST FAILED — check MT5 terminal and account permissions.")
    print("=" * 55)


if __name__ == "__main__":
    main()
