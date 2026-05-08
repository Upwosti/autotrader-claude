"""
auto_updater.py — Self-analysis and code optimization monitor.

Every 6 hours: analyze performance bottlenecks, log findings.
Every 24 hours: full health check, test all modules, report.
Never modifies core trading logic automatically.
Runs as daemon thread.
"""

import os
import sys
import time
import threading
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from loguru import logger

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("auto_updater: psutil not available — system stats will return -1")

# Key modules to health-check on 24h cycle
_KEY_MODULES = [
    "evolution.autonomous_loop",
    "evolution.ml_layer",
    "evolution.skill_builder",
    "strategy.trend_engine",
    "backtester.walk_forward",
    "database.supabase_client",
]

# Required directories (relative to project root)
_REQUIRED_DIRS = ["logs", "local_db", "local_db/ml_models"]

# Required data files
_REQUIRED_FILES = [
    "local_db/auto_loop_state.json",
    "local_db/skills.json",
]

# Thresholds
_LOG_SIZE_WARN_MB = 100
_STATE_STALE_MIN = 10
_RAM_WARN_PCT = 85.0
_CPU_WARN_PCT = 90.0

# Intervals in seconds
_INTERVAL_6H = 6 * 3600
_INTERVAL_24H = 24 * 3600
_POLL_INTERVAL = 60  # check every minute whether an interval has elapsed


class AutoUpdater:
    """
    Daemon monitor that performs periodic system health checks and
    logs findings. Never modifies core trading logic automatically.
    """

    def __init__(self, telegram=None, db=None):
        self._telegram = telegram
        self._db = db
        self._last_6h: float = 0.0
        self._last_24h: float = 0.0
        self._root = Path(__file__).parent.resolve()

    # ------------------------------------------------------------------ #
    #  Public start
    # ------------------------------------------------------------------ #

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._loop, daemon=True, name="AutoUpdater")
        t.start()
        logger.info("AutoUpdater daemon started")
        return t

    # ------------------------------------------------------------------ #
    #  Main loop
    # ------------------------------------------------------------------ #

    def _loop(self):
        now = time.time()
        self._last_6h = now
        self._last_24h = now

        while True:
            try:
                current = time.time()
                if current - self._last_6h >= _INTERVAL_6H:
                    self._check_6hour()
                    self._last_6h = current
                if current - self._last_24h >= _INTERVAL_24H:
                    self._check_24hour()
                    self._last_24h = current
            except Exception as e:
                logger.error(f"AutoUpdater loop error: {e}")
            time.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    #  6-hour check
    # ------------------------------------------------------------------ #

    def _check_6hour(self):
        logger.info("AutoUpdater: running 6-hour check")
        stats = self._get_system_stats()

        # Log size check
        log_size_mb = stats.get("log_size_mb", 0)
        if log_size_mb > _LOG_SIZE_WARN_MB:
            msg = f"Log directory is large: {log_size_mb:.1f} MB (threshold {_LOG_SIZE_WARN_MB} MB)"
            logger.warning(f"AutoUpdater: {msg}")
            self._send("Log Size Warning", msg)

        # State file staleness
        state_age_min = stats.get("state_age_min", 0)
        if 0 < state_age_min > _STATE_STALE_MIN:
            msg = f"State file not updated for {state_age_min:.1f} min — possible stall"
            logger.warning(f"AutoUpdater: {msg}")
            self._send("State File Stall Warning", msg)

        # RAM check
        ram_pct = stats.get("ram_pct", -1)
        if ram_pct >= 0 and ram_pct > _RAM_WARN_PCT:
            msg = f"High RAM usage: {ram_pct:.1f}% (threshold {_RAM_WARN_PCT}%)"
            logger.warning(f"AutoUpdater: {msg}")
            self._send("High RAM Usage", msg)

        # CPU check
        cpu_pct = stats.get("cpu_pct", -1)
        if cpu_pct >= 0 and cpu_pct > _CPU_WARN_PCT:
            msg = f"High CPU usage: {cpu_pct:.1f}% (threshold {_CPU_WARN_PCT}%)"
            logger.warning(f"AutoUpdater: {msg}")
            self._send("High CPU Usage", msg)

        logger.info(
            f"AutoUpdater 6h summary | CPU={cpu_pct:.1f}% RAM={ram_pct:.1f}% "
            f"Disk={stats.get('disk_pct', -1):.1f}% "
            f"LogSize={log_size_mb:.1f}MB StateAge={state_age_min:.1f}min"
        )

    # ------------------------------------------------------------------ #
    #  24-hour check
    # ------------------------------------------------------------------ #

    def _check_24hour(self):
        logger.info("AutoUpdater: running 24-hour full health check")
        report: Dict = {"timestamp": datetime.now(timezone.utc).isoformat()}
        module_results: Dict[str, bool] = {}

        # Module import tests
        for module_path in _KEY_MODULES:
            try:
                # Try to import without caching side effects
                if module_path in sys.modules:
                    module_results[module_path] = True
                else:
                    importlib.import_module(module_path)
                    module_results[module_path] = True
                logger.debug(f"AutoUpdater: module OK — {module_path}")
            except ImportError as e:
                module_results[module_path] = False
                logger.warning(f"AutoUpdater: module MISSING — {module_path}: {e}")
            except Exception as e:
                module_results[module_path] = False
                logger.warning(f"AutoUpdater: module ERROR — {module_path}: {e}")

        report["modules"] = module_results

        # Directory checks
        dir_results: Dict[str, bool] = {}
        for d in _REQUIRED_DIRS:
            path = self._root / d
            exists = path.is_dir()
            dir_results[d] = exists
            if not exists:
                logger.warning(f"AutoUpdater: missing directory — {path}")
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"AutoUpdater: created directory — {path}")
                except Exception as e:
                    logger.error(f"AutoUpdater: could not create {path}: {e}")

        report["directories"] = dir_results

        # File checks
        file_results: Dict[str, bool] = {}
        for f in _REQUIRED_FILES:
            path = self._root / f
            exists = path.is_file()
            file_results[f] = exists
            if not exists:
                logger.warning(f"AutoUpdater: missing file — {path}")

        report["files"] = file_results

        # System stats
        stats = self._get_system_stats()
        report["system"] = stats

        # Summarize
        ok_modules = sum(1 for v in module_results.values() if v)
        total_modules = len(module_results)
        ok_dirs = sum(1 for v in dir_results.values() if v)
        ok_files = sum(1 for v in file_results.values() if v)

        summary = (
            f"24h Health Check\n"
            f"Modules: {ok_modules}/{total_modules} OK\n"
            f"Dirs:    {ok_dirs}/{len(_REQUIRED_DIRS)} present\n"
            f"Files:   {ok_files}/{len(_REQUIRED_FILES)} present\n"
            f"CPU:     {stats.get('cpu_pct', -1):.1f}%\n"
            f"RAM:     {stats.get('ram_pct', -1):.1f}%\n"
            f"Disk:    {stats.get('disk_pct', -1):.1f}%\n"
            f"LogMB:   {stats.get('log_size_mb', 0):.1f} MB"
        )

        logger.info(f"AutoUpdater 24h report:\n{summary}")

        if ok_modules < total_modules:
            missing = [k for k, v in module_results.items() if not v]
            self._send(
                "24h Health — Module Warnings",
                summary + f"\n\nMissing modules:\n" + "\n".join(missing),
            )
        else:
            self._send("24h Health Check — All OK", summary)

    # ------------------------------------------------------------------ #
    #  System stats
    # ------------------------------------------------------------------ #

    def _get_system_stats(self) -> Dict:
        stats: Dict = {
            "cpu_pct": -1,
            "ram_pct": -1,
            "disk_pct": -1,
            "log_size_mb": 0.0,
            "state_age_min": -1.0,
        }

        if PSUTIL_AVAILABLE:
            try:
                stats["cpu_pct"] = psutil.cpu_percent(interval=1)
            except Exception:
                pass
            try:
                vm = psutil.virtual_memory()
                stats["ram_pct"] = vm.percent
            except Exception:
                pass
            try:
                du = psutil.disk_usage(str(self._root))
                stats["disk_pct"] = du.percent
            except Exception:
                pass

        # Log directory size
        log_dir = self._root / "logs"
        if log_dir.is_dir():
            try:
                total_bytes = sum(
                    f.stat().st_size for f in log_dir.rglob("*") if f.is_file()
                )
                stats["log_size_mb"] = total_bytes / (1024 * 1024)
            except Exception:
                pass

        # State file age
        state_file = self._root / "local_db" / "auto_loop_state.json"
        if state_file.is_file():
            try:
                mtime = state_file.stat().st_mtime
                age_min = (time.time() - mtime) / 60.0
                stats["state_age_min"] = age_min
            except Exception:
                pass

        return stats

    # ------------------------------------------------------------------ #
    #  Telegram helper
    # ------------------------------------------------------------------ #

    def _send(self, subject: str, body: str):
        if self._telegram is None:
            return
        try:
            self._telegram.send(subject, body)
        except Exception as e:
            logger.error(f"AutoUpdater._send telegram failed: {e}")
