"""
AutoTrader OMEGA — Watchdog v4.1
Reliable process monitor with heartbeat detection.
Never stops watching. Never lets the engine stay dead.
Singleton-locked: only one instance runs at a time.

Usage (foreground):  python watchdog.py
Usage (background):  pythonw watchdog.py   (Windows, no console)
"""

import os
import sys
import subprocess
import time
import logging
import urllib.request
import json
import atexit

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
ENGINE_SCRIPT = os.path.join(SCRIPT_DIR, "run_forever.py")
LOG_DIR       = os.path.join(SCRIPT_DIR, "logs")
HEARTBEAT     = os.path.join(SCRIPT_DIR, "heartbeat.txt")
LOCKFILE      = os.path.join(SCRIPT_DIR, "watchdog.lock")
VENV_DIR      = os.path.join(SCRIPT_DIR, "venv")
os.makedirs(LOG_DIR, exist_ok=True)

# Refuse to run if we are not inside the project venv — prevents system-Python zombie watchdogs
_exe = sys.executable.lower()
if not (_exe.startswith(os.path.join(SCRIPT_DIR, "venv").lower()) or
        _exe.startswith(os.path.join(SCRIPT_DIR, "Venv").lower())):
    print(f"[WATCHDOG] Refusing to run with non-venv Python: {sys.executable}", flush=True)
    sys.exit(1)

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

# ── Singleton via Windows named mutex (atomic, no race condition) ─────────────
import ctypes
_MUTEX_NAME  = "Global\\AutoTraderOmegaWatchdog"
_mutex_handle = None

def _acquire_lock():
    """CreateMutex with bInitialOwner=True. ERROR_ALREADY_EXISTS means another instance runs."""
    global _mutex_handle
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    last_err = ctypes.windll.kernel32.GetLastError()
    if last_err == 183:  # ERROR_ALREADY_EXISTS
        print("[WATCHDOG] Another instance already running — exiting", flush=True)
        sys.exit(0)
    # Write PID to lockfile for diagnostics only
    try:
        with open(LOCKFILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    atexit.register(_release_lock)

def _release_lock():
    try:
        if _mutex_handle:
            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
    except Exception:
        pass
    try:
        if os.path.exists(LOCKFILE):
            os.remove(LOCKFILE)
    except Exception:
        pass

_acquire_lock()

# ── .env loader ───────────────────────────────────────────────────────────────
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
CHECK_INTERVAL       = 30    # seconds between health checks
HEARTBEAT_MAX_AGE    = 300   # 5 min — if heartbeat older, engine is frozen

# ── State ─────────────────────────────────────────────────────────────────────
_engine_proc   = None
_restart_count = 0

# ── Telegram (urllib, no external deps) ───────────────────────────────────────
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT, "text": msg}).encode("utf-8")
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram failed: {e}")

# ── Process helpers ───────────────────────────────────────────────────────────
def _proc_alive() -> bool:
    global _engine_proc
    if _engine_proc is None:
        return False
    if _engine_proc.poll() is not None:
        return False
    if _PSUTIL:
        try:
            p = psutil.Process(_engine_proc.pid)
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False
    return True


def _heartbeat_ok() -> bool:
    """Returns False if heartbeat file is too old (engine frozen)."""
    if not os.path.exists(HEARTBEAT):
        return True  # not yet written — engine just started, give it time
    try:
        age = time.time() - os.path.getmtime(HEARTBEAT)
        if age > HEARTBEAT_MAX_AGE:
            log.warning(f"Heartbeat stale: {age:.0f}s old (max {HEARTBEAT_MAX_AGE}s) — engine frozen")
            return False
    except Exception:
        pass
    return True


def is_running() -> bool:
    return _proc_alive() and _heartbeat_ok()


def start_engine() -> bool:
    global _engine_proc, _restart_count
    log.info(f"Starting engine: {ENGINE_SCRIPT}")
    try:
        # Remove stale heartbeat so we don't immediately flag it
        if os.path.exists(HEARTBEAT):
            try:
                os.remove(HEARTBEAT)
            except Exception:
                pass

        stdout_log = open(os.path.join(LOG_DIR, "engine_stdout.log"), "a", encoding="utf-8")
        stderr_log = open(os.path.join(LOG_DIR, "engine_stderr.log"), "a", encoding="utf-8")
        _engine_proc = subprocess.Popen(
            [PYTHON_EXE, ENGINE_SCRIPT],
            cwd=SCRIPT_DIR,
            stdout=stdout_log,
            stderr=stderr_log,
        )
        _restart_count += 1
        log.info(f"Engine started — PID {_engine_proc.pid} (restart #{_restart_count})")
        return True
    except Exception as e:
        log.error(f"Failed to start engine: {e}")
        return False


def kill_stale_engines():
    """Kill any stray run_forever.py processes (not the one we own)."""
    current_pid = _engine_proc.pid if _engine_proc else None
    if not _PSUTIL:
        return
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
            if "run_forever.py" in cmdline and proc.info["pid"] != current_pid:
                proc.terminate()
                log.info(f"Killed stale engine PID {proc.info['pid']}")
        except Exception:
            pass


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 58)
    log.info("AutoTrader OMEGA Watchdog v4.1 — STARTING")
    log.info(f"Engine  : {ENGINE_SCRIPT}")
    log.info(f"Python  : {PYTHON_EXE}")
    log.info(f"Check   : every {CHECK_INTERVAL}s | Heartbeat max: {HEARTBEAT_MAX_AGE}s")
    log.info("=" * 58)

    send_telegram("🐕 Watchdog v4.1 started — launching engine...")

    kill_stale_engines()
    if not start_engine():
        send_telegram("❌ Watchdog: FAILED to start engine on first attempt — will keep retrying")

    frozen_warned = False

    while True:
        try:
            time.sleep(CHECK_INTERVAL)

            proc_ok = _proc_alive()
            hb_ok   = _heartbeat_ok()

            if not proc_ok:
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                log.warning(f"Engine process DEAD at {ts} — restarting...")
                send_telegram(f"⚠️ [{ts}] Engine process died — restarting (attempt #{_restart_count + 1})")
                frozen_warned = False

                kill_stale_engines()
                time.sleep(3)
                if start_engine():
                    send_telegram(f"✅ Engine restarted — PID {_engine_proc.pid}")
                else:
                    send_telegram("❌ Engine restart FAILED — retrying next cycle")

            elif not hb_ok:
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                if not frozen_warned:
                    log.warning(f"Engine FROZEN at {ts} — killing and restarting...")
                    send_telegram(f"🥶 [{ts}] Engine frozen (no heartbeat) — force restarting")
                    frozen_warned = False

                    # Force kill frozen process
                    try:
                        if _PSUTIL:
                            psutil.Process(_engine_proc.pid).kill()
                        else:
                            _engine_proc.kill()
                    except Exception:
                        pass
                    time.sleep(3)

                    kill_stale_engines()
                    if start_engine():
                        send_telegram(f"✅ Engine force-restarted — PID {_engine_proc.pid}")
                    else:
                        send_telegram("❌ Force restart FAILED — retrying next cycle")
            else:
                log.debug(f"Engine healthy — PID {_engine_proc.pid}")
                frozen_warned = False

        except KeyboardInterrupt:
            log.info("Watchdog stopped by KeyboardInterrupt")
            send_telegram("🛑 Watchdog stopped manually — engine may still be running")
            sys.exit(0)
        except Exception as e:
            log.error(f"Watchdog loop error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
