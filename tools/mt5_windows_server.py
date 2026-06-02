"""
MT5 Windows Server — run this on your Windows machine with MT5 terminal open.

This starts the rpyc server that allows the Linux/cloud AutoTrader Claude
to connect and execute MT5 orders remotely.

Requirements (Windows machine):
    pip install mt5linux MetaTrader5

Usage:
    python tools/mt5_windows_server.py

Then on the cloud/Linux side, set your Windows IP:
    export MT5_HOST=<your-windows-ip>   (e.g. 192.168.1.100)

The cloud system will then be able to call MT5 as if running locally.
"""

import sys
import socket

try:
    from mt5linux import MetaTrader5
except ImportError:
    print("ERROR: mt5linux not installed. Run: pip install mt5linux MetaTrader5")
    sys.exit(1)

try:
    import MetaTrader5 as _native_mt5
    if not _native_mt5.initialize():
        print(f"WARNING: MT5 initialize failed: {_native_mt5.last_error()}")
        print("Make sure MT5 terminal is running and logged in before starting this server.")
    else:
        info = _native_mt5.account_info()
        if info:
            print(f"MT5 terminal connected: login={info.login}  broker={info.company}")
            print(f"  Balance={info.balance:.2f}  Server={info.server}")
        _native_mt5.shutdown()
except ImportError:
    print("ERROR: Native MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

PORT = 18812
hostname = socket.gethostname()
local_ip = socket.gethostbyname(hostname)

print(f"\nStarting MT5 bridge server on {local_ip}:{PORT}")
print(f"On your Linux/cloud system, run:")
print(f"    export MT5_HOST={local_ip}")
print(f"\nPress Ctrl+C to stop.\n")

try:
    mt5 = MetaTrader5()
    mt5.start_server(port=PORT)
except KeyboardInterrupt:
    print("\nServer stopped.")
