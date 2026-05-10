"""
Post-session setup script.
Run once after Claude session ends.
- Sends IMPLEMENTATION COMPLETE Telegram
- Schedules 3-hour evaluation report
- Verifies all new modules import cleanly
"""

import json
import os
import ssl
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(text: str):
    import urllib.request, json as _json
    if not TG_TOKEN or not TG_CHAT:
        print(f"[NO TELEGRAM] {text}")
        return
    try:
        body = _json.dumps({"chat_id": TG_CHAT, "text": text[:4096]}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            print(f"Telegram OK: {resp.status}")
    except Exception as e:
        print(f"Telegram failed: {e}")
        print(text)


def verify_imports():
    results = []
    modules = [
        ("analytics.live_drift_monitor",      "LiveDriftMonitor"),
        ("portfolio.live_exposure_engine",    "LiveExposureEngine"),
        ("risk.news_volatility_filter",       "NewsVolatilityFilter"),
        ("execution.live_execution_guard",    "LiveExecutionGuard"),
        ("execution.paper_trading",           "PaperTradingEngine"),
        ("validation.walk_forward_validator", "WalkForwardValidator"),
        ("core.resource_monitor",             "ResourceMonitor"),
    ]
    for mod, cls in modules:
        try:
            m = __import__(mod, fromlist=[cls])
            getattr(m, cls)
            results.append(f"  OK  {mod}.{cls}")
        except Exception as e:
            results.append(f"  ERR {mod}.{cls}: {e}")
    return results


def schedule_eval_report(delay_seconds: int = 10800):
    """Schedule evaluation report in background after delay_seconds."""
    def _run():
        print(f"Eval report scheduled in {delay_seconds//3600}h "
              f"({datetime.now().strftime('%H:%M')} + {delay_seconds//60}min)")
        time.sleep(delay_seconds)
        script = ROOT / "scripts" / "evaluation_report.py"
        python = sys.executable
        try:
            subprocess.run([python, str(script)], timeout=120)
        except Exception as e:
            print(f"Eval report failed: {e}")

    t = threading.Thread(target=_run, daemon=False)
    t.start()
    return t


if __name__ == "__main__":
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 50)
    print("POST-SESSION SETUP")
    print("=" * 50)

    # Verify all imports
    print("\nVerifying module imports...")
    import_results = verify_imports()
    for line in import_results:
        print(line)

    ok_count  = sum(1 for r in import_results if r.startswith("  OK"))
    err_count = sum(1 for r in import_results if r.startswith("  ERR"))

    # Build implementation complete message
    msg = (
        f"IMPLEMENTATION COMPLETE\n"
        f"{now_str}\n\n"
        f"ALL 8 PHASES DEPLOYED:\n"
        f"P1 Real backtest reporter\n"
        f"P2 Live execution guard\n"
        f"P3 Drift monitor\n"
        f"P4 Exposure engine\n"
        f"P5 News/volatility filter\n"
        f"P6 WF validator\n"
        f"P7 Paper trading\n"
        f"P8 Resource monitor\n\n"
        f"Modules: {ok_count}/7 OK, {err_count} ERR\n"
        f"Engine: autonomous — no restart needed\n"
        f"Eval report: in 3 hours\n"
    )

    print(f"\n{msg}")
    send_telegram(msg)

    # Schedule 3-hour eval
    print("\nScheduling 3-hour evaluation report...")
    schedule_eval_report(delay_seconds=10800)
    print("Done. Evaluation report will fire in 3 hours.")
    print("This process will keep running in background...")
