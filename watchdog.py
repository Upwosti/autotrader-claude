"""
AutoTrader OMEGA — Watchdog v3.0
Simple, reliable process monitor. Never stops watching.

Usage (foreground):  python watchdog.py
Usage (background):  pythonw watchdog.py   (Windows, no console)
"""

import os
import sys
import subprocess
import time
import logging
import requests
import psutil
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ENGINE_SCRIPT = os.path.join(SCRIPT_DIR, "run_forever.py")
LOG_DIR       = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# When running headless (pythonw), redirect output to file
if sys.stdout is None:
    _hlog = open(os.path.join(LOG_DIR, "watchdog.log"), "a", buffering=1, encoding="utf-8")
    sys.stdout = _hlog
    sys.stderr = _hlog

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "watchdog.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watchdog")

# ── .env loader (minimal, no dependencies) ────────────────────────────────────
def _load_env():
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Python executable (use venv if present) ───────────────────────────────────
_venv_py = os.path.join(SCRIPT_DIR, "venv", "Scripts", "python.exe")
PYTHON_EXE = _venv_py if os.path.exists(_venv_py) else sys.executable

# ── Config ────────────────────────────────────────────────────────────────────
CHECK_INTERVAL = 30   # seconds between health checks

# ── State ─────────────────────────────────────────────────────────────────────
_engine_proc   = None
_restart_count = 0

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log.warning(f"Telegram failed: {e}")

# ── Process helpers ───────────────────────────────────────────────────────────
def is_running() -> bool:
    global _engine_proc
    if _engine_proc is None:
        return False
    if _engine_proc.poll() is not None:
        return False
    try:
        p = psutil.Process(_engine_proc.pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False

def start_engine() -> bool:
    global _engine_proc, _restart_count
    log.info(f"Starting engine: {ENGINE_SCRIPT}")
    try:
        stdout_log = open(os.path.join(LOG_DIR, "engine_stdout.log"), "a", encoding="utf-8")
        stderr_log = open(os.path.join(LOG_DIR, "engine_stderr.log"), "a", encoding="utf-8")
        _engine_proc = subprocess.Popen(
            [PYTHON_EXE, ENGINE_SCRIPT],
            cwd=SCRIPT_DIR,
            stdout=stdout_log,
            stderr=stderr_log,
        )
        _restart_count += 1
        log.info(f"Engine started — PID {_engine_proc.pid} (start #{_restart_count})")
        return True
    except Exception as e:
        log.error(f"Failed to start engine: {e}")
        return False

def kill_duplicates():
    current_pid = _engine_proc.pid if _engine_proc else None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if "run_forever.py" in cmdline and proc.info["pid"] != current_pid:
                proc.terminate()
                log.info(f"Killed duplicate engine PID {proc.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info("AutoTrader OMEGA Watchdog v3.0 — STARTING")
    log.info(f"Engine  : {ENGINE_SCRIPT}")
    log.info(f"Python  : {PYTHON_EXE}")
    log.info(f"Interval: {CHECK_INTERVAL}s")
    log.info("=" * 55)

    send_telegram("🐕 Watchdog v3.0 started — launching engine...")

    if not start_engine():
        send_telegram("❌ Watchdog: FAILED to start engine on first attempt — will keep retrying")

    while True:
        try:
            time.sleep(CHECK_INTERVAL)

            if not is_running():
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                log.warning(f"Engine NOT running at {ts} — restarting...")
                send_telegram(f"⚠️ [{ts}] Engine stopped — restarting (attempt #{_restart_count + 1})")

                kill_duplicates()
                time.sleep(3)

                if start_engine():
                    send_telegram(f"✅ Engine restarted — PID {_engine_proc.pid}")
                else:
                    send_telegram("❌ Engine restart FAILED — retrying in 30s")
            else:
                log.debug(f"Engine healthy — PID {_engine_proc.pid}")

        except KeyboardInterrupt:
            log.info("Watchdog stopped by KeyboardInterrupt")
            send_telegram("🛑 Watchdog stopped manually — engine may still be running")
            sys.exit(0)
        except Exception as e:
            log.error(f"Watchdog loop error: {e}")
            time.sleep(10)
            # Never stop watching

if __name__ == "__main__":
    main()
