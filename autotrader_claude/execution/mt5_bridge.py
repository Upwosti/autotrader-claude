"""
MT5 Bridge — provides a unified MetaTrader5 API regardless of OS.

Priority:
  1. Native MetaTrader5 package (Windows only)
  2. mt5linux via RPC to a Windows machine (Linux / cloud)
  3. None — falls back to paper mode

Usage:
    from execution.mt5_bridge import get_mt5
    mt5 = get_mt5()   # returns mt5-like object or None
    if mt5:
        mt5.initialize()
        info = mt5.account_info()

On Windows:
    pip install MetaTrader5

On Linux/cloud:
    pip install mt5linux
    Set MT5_HOST env var to your Windows machine IP, e.g.:
        export MT5_HOST=192.168.1.100

On the Windows machine (one-time setup):
    pip install mt5linux MetaTrader5
    python -c "import rpyc; rpyc.lib.configure(logger=None); \
               from mt5linux import MetaTrader5; MetaTrader5().start_server(port=18812)"
    # OR just run: python tools/mt5_windows_server.py
"""

import sys
import os
from loguru import logger


def get_mt5():
    """Return an MT5 API object, or None if unavailable.

    Returns the native MetaTrader5 module if on Windows, or an mt5linux
    proxy if MT5_HOST is configured and the server is reachable.
    """
    # Try native Windows package first
    try:
        import MetaTrader5 as mt5
        logger.info("MT5: using native MetaTrader5 package")
        return mt5
    except ImportError:
        pass

    # Try mt5linux bridge
    try:
        from mt5linux import MetaTrader5

        host = os.getenv("MT5_HOST", "localhost")
        port = int(os.getenv("MT5_PORT", "18812"))
        logger.info(f"MT5: attempting mt5linux bridge at {host}:{port}")
        mt5 = MetaTrader5(host=host, port=port)
        # Quick ping — if the server isn't up this throws ConnectionRefusedError
        mt5.version()
        logger.info(f"MT5: mt5linux bridge connected at {host}:{port}")
        return mt5
    except ImportError:
        logger.debug("mt5linux not installed")
    except Exception as e:
        host = os.getenv("MT5_HOST", "localhost")
        logger.warning(
            f"MT5: bridge at {host} unreachable ({e}). "
            "Set MT5_HOST env var to your Windows machine IP and run "
            "tools/mt5_windows_server.py on that machine."
        )

    return None


def test_connection() -> bool:
    """Quick connection test. Returns True if MT5 is reachable."""
    mt5 = get_mt5()
    if mt5 is None:
        return False
    try:
        if not mt5.initialize():
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        info = mt5.account_info()
        if info is None:
            logger.error("MT5 account_info returned None")
            mt5.shutdown()
            return False
        logger.info(
            f"MT5 connected: login={info.login}  broker={info.company}  "
            f"balance={info.balance:.2f}  server={info.server}"
        )
        mt5.shutdown()
        return True
    except Exception as e:
        logger.error(f"MT5 test_connection error: {e}")
        return False
