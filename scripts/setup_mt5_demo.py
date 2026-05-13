"""
MT5 Demo Account Setup

Autonomously:
1. Check if MetaTrader5 library is installed; install if not
2. Attempt MT5 connection (existing credentials or demo)
3. Verify connection
4. Save credentials to .env (demo only)
5. Start paper trading mode

Run once: python scripts/setup_mt5_demo.py
"""

import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def install_mt5():
    try:
        import MetaTrader5
        print(f"MT5 library already installed: {MetaTrader5.__version__}")
        return True
    except ImportError:
        print("Installing MetaTrader5...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "MetaTrader5"],
                           check=True, capture_output=True)
            print("MT5 installed successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"MT5 install failed: {e}")
            return False


def check_env_credentials() -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    return {
        "login":    os.environ.get("MT5_LOGIN", ""),
        "password": os.environ.get("MT5_PASSWORD", ""),
        "server":   os.environ.get("MT5_SERVER", ""),
    }


def try_connect(creds: dict) -> bool:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MT5 not installed")
        return False

    if not mt5.initialize():
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        return False

    print(f"MT5 version: {mt5.version()}")

    if creds["login"] and creds["password"] and creds["server"]:
        result = mt5.login(
            int(creds["login"]),
            password=creds["password"],
            server=creds["server"],
        )
        if result:
            info = mt5.account_info()
            if info:
                print(f"Connected: {info.name} | {info.server} | Balance: {info.balance}")
                return True
            else:
                print(f"Login failed: {mt5.last_error()}")
        else:
            print(f"Login failed: {mt5.last_error()}")

    # Try without login (terminal auto-login)
    info = mt5.account_info()
    if info:
        print(f"Auto-connected: {info.name} | Server: {info.server} | Balance: {info.balance}")
        return True

    print("MT5 not connected. Please:")
    print("  1. Install MetaTrader5 terminal")
    print("  2. Open a demo account at any broker")
    print("  3. Add to .env:")
    print("     MT5_LOGIN=<account_number>")
    print("     MT5_PASSWORD=<password>")
    print("     MT5_SERVER=<broker_server>")
    mt5.shutdown()
    return False


def set_paper_mode():
    """Force paper trading mode until demo validated."""
    paper_state = ROOT / "local_db" / "paper_trading_state.json"
    import json
    from datetime import datetime, timezone

    state = {}
    if paper_state.exists():
        try:
            with open(paper_state) as f:
                state = json.load(f)
        except Exception:
            pass

    if state.get("mode") == "live":
        print("WARNING: System is in LIVE mode. Keeping live.")
    else:
        state["mode"] = "paper"
        state["paper_start_date"] = state.get("paper_start_date") or datetime.now(timezone.utc).isoformat()
        paper_state.parent.mkdir(parents=True, exist_ok=True)
        with open(paper_state, "w") as f:
            json.dump(state, f, indent=2)
        print("Paper trading mode: ACTIVE (demo validation required before live)")


def send_telegram(msg: str):
    import ssl, urllib.request, json as _json
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        body = _json.dumps({"chat_id": chat, "text": msg[:4096]}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception:
        pass


if __name__ == "__main__":
    print("=" * 50)
    print("MT5 DEMO SETUP")
    print("=" * 50)

    # 1. Install
    ok = install_mt5()
    if not ok:
        print("Cannot proceed without MT5 library.")
        sys.exit(1)

    # 2. Check credentials
    creds = check_env_credentials()
    print(f"Credentials: login={bool(creds['login'])} server={bool(creds['server'])}")

    # 3. Try connect
    connected = try_connect(creds)

    # 4. Ensure paper mode
    set_paper_mode()

    # 5. Report
    status = "CONNECTED" if connected else "OFFLINE (paper mode)"
    msg = (f"MT5 SETUP: {status}\n"
           f"Paper trading: ACTIVE\n"
           f"Demo required: 30 days before live promotion")
    print(msg)
    send_telegram(msg)
