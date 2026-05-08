"""
watchdog.py — Auto-restart monitor for run_forever.py.

Monitors the evolution loop process. If it crashes, waits 10 seconds,
logs the reason, sends a Telegram alert, and restarts automatically.

Usage (foreground):    python watchdog.py [--pairs XAUUSD,...] [--hours 0]
Usage (background):    pythonw watchdog.py          (Windows — no console window)
Usage (via bat file):  start_watchdog.bat

The watchdog itself never exits unless you kill it (Ctrl+C or Task Manager).
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE  = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
RUNNER      = os.path.join(SCRIPT_DIR, "run_forever.py")
LOG_DIR     = os.path.join(SCRIPT_DIR, "logs")
WATCHDOG_LOG = os.path.join(LOG_DIR, "watchdog.log")
RESTART_DELAY = 10   # seconds before restart after crash

os.makedirs(LOG_DIR, exist_ok=True)

# Redirect stdout/stderr to log file when running without a console (pythonw.exe)
if sys.stdout is None:
    _headless_log = open(WATCHDOG_LOG, "a", buffering=1, encoding="utf-8")
    sys.stdout = _headless_log
    sys.stderr = _headless_log


def _log(msg: str):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | WATCHDOG | {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    with open(WATCHDOG_LOG, "a") as f:
        f.write(line + "\n")


def _send_telegram(subject: str, body: str):
    """Fire-and-forget Telegram alert — skip if not configured."""
    try:
        sys.path.insert(0, SCRIPT_DIR)
        from alerts.telegram_bot import TelegramAlert
        tg = TelegramAlert()
        tg.send(subject, body)
    except Exception as e:
        _log(f"Telegram alert failed: {e}")


STATE_PATH = os.path.join(SCRIPT_DIR, "local_db", "auto_loop_state.json")
_PERF_CHECK_INTERVAL = 1800   # 30 minutes
_perf_last_iter = 0
_perf_last_wr   = 0.0


def _read_state_file() -> dict:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _performance_monitor():
    """
    Background thread: every 30 min checks if the evolution loop is progressing.
    Alerts via Telegram if stuck (no new iterations in 30 min).
    """
    global _perf_last_iter, _perf_last_wr
    time.sleep(1800)   # first check after 30 min
    while True:
        try:
            state = _read_state_file()
            iter_now   = state.get("iteration", 0)
            wr_now     = state.get("best_xauusd_wr_real",
                                   state.get("best_xauusd_wr", 0))
            no_improve = state.get("no_improvement_count", 0)
            saved_time = state.get("last_saved", "")

            _log(f"[30min] iter={iter_now} xau_wr={wr_now:.1%} no_improve={no_improve}")

            # Stall: same iteration as 30 min ago
            if iter_now == _perf_last_iter and iter_now > 0:
                msg = (f"STALL DETECTED: iter={iter_now} unchanged for 30 min. "
                       f"XAUUSD WR={wr_now:.1%}. last_saved={saved_time}")
                _log(f"[30min] {msg}")
                _send_telegram("Stall Detected — Watchdog Alert", msg)

            # Stuck in local optimum
            if no_improve >= 130:
                msg = (f"Stuck: {no_improve} iters no improvement. "
                       f"XAUUSD WR={wr_now:.1%}. Random restart should fire soon.")
                _log(f"[30min] {msg}")
                _send_telegram("Stuck Detected — Random Restart Due", msg)

            # WR regression (shouldn't happen with WR floor, but alert if it does)
            if _perf_last_wr > 0 and wr_now < _perf_last_wr - 0.05:
                msg = (f"WR regression: {_perf_last_wr:.1%} → {wr_now:.1%} "
                       f"(>5% drop). Investigating …")
                _log(f"[30min] WARNING: {msg}")
                _send_telegram("WR Regression Warning", msg)

            _perf_last_iter = iter_now
            _perf_last_wr   = max(_perf_last_wr, wr_now)

        except Exception as e:
            _log(f"[30min] Performance monitor error: {e}")

        time.sleep(_PERF_CHECK_INTERVAL)


def _start_performance_monitor():
    """Start the 30-min performance monitor as a daemon thread."""
    t = threading.Thread(
        target=_performance_monitor, daemon=True, name="PerfMonitor"
    )
    t.start()
    _log("Performance monitor started (30-min interval)")
    return t


def _save_crash_to_db(crash_info: str):
    try:
        sys.path.insert(0, SCRIPT_DIR)
        from database.supabase_client import SupabaseClient
        db = SupabaseClient()
        db.set_state("last_crash", crash_info[:500])
        db.set_state("last_crash_time", datetime.utcnow().isoformat())
    except Exception:
        pass


def run_watchdog(pairs: str, hours: str):
    cmd = [PYTHON_EXE, RUNNER, "--pairs", pairs, "--hours", hours]
    crash_count = 0

    _log(f"Watchdog started | runner: {RUNNER}")
    _log(f"Command: {' '.join(cmd)}")
    _send_telegram(
        "AutoTrader Watchdog Started",
        f"Monitoring run_forever.py\nPairs: {pairs}\nAuto-restart on crash: YES\n30min performance check: ACTIVE"
    )

    # Start background performance monitor
    _start_performance_monitor()

    while True:
        _log(f"Starting run_forever.py (crash_count={crash_count})")
        start_time = datetime.now()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # Stream output to watchdog log
            output_lines = []
            for line in proc.stdout:
                line = line.rstrip()
                try:
                    print(line, flush=True)
                except Exception:
                    pass
                with open(WATCHDOG_LOG, "a") as f:
                    f.write(line + "\n")
                output_lines.append(line)
                if len(output_lines) > 200:
                    output_lines = output_lines[-200:]

            proc.wait()
            exit_code = proc.returncode
            uptime = (datetime.now() - start_time).total_seconds()

        except FileNotFoundError:
            exit_code = -1
            uptime = 0
            output_lines = [f"ERROR: {PYTHON_EXE} not found"]
        except Exception as e:
            exit_code = -1
            uptime = 0
            output_lines = [f"ERROR launching process: {e}", traceback.format_exc()]

        # Normal exit (target reached or Ctrl+C propagated)
        if exit_code == 0:
            _log(f"run_forever.py exited cleanly (code=0, uptime={uptime:.0f}s)")
            _log("Watchdog done — restart not needed for clean exit")
            break

        # Crash
        crash_count += 1
        last_lines   = "\n".join(output_lines[-20:])
        crash_msg    = (
            f"run_forever.py CRASHED (exit={exit_code}, "
            f"uptime={uptime:.0f}s, crash #{crash_count})\n\n"
            f"Last output:\n{last_lines}"
        )
        _log(crash_msg)
        _save_crash_to_db(crash_msg)
        _send_telegram(
            f"AutoTrader CRASH #{crash_count} — Restarting in {RESTART_DELAY}s",
            crash_msg[:1000]
        )

        _log(f"Waiting {RESTART_DELAY}s before restart …")
        time.sleep(RESTART_DELAY)

        _send_telegram(
            "AutoTrader Restarting",
            f"Crash #{crash_count} handled. Resuming from saved state."
        )


def main():
    parser = argparse.ArgumentParser(description="AutoTrader Watchdog")
    parser.add_argument("--pairs",  default="XAUUSD,XAGUSD,XPTUSD,GBPUSD,EURUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD,EURJPY,GBPJPY,BTCUSD,ETHUSD,NAS100,US30,GER40,GC=F,SI=F")
    parser.add_argument("--hours",  default="0", help="0 = run forever")
    args = parser.parse_args()
    run_watchdog(args.pairs, args.hours)


if __name__ == "__main__":
    main()
