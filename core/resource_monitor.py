"""
Phase 8: Resource Safety Monitor

Strict 8GB RAM enforcement for AutoTrader.
Rules:
  - Never load all pairs simultaneously
  - Never run excessive parallel optimization
  - Never duplicate historical datasets
  - Use rolling windows, batch processing, lazy loading, pair-by-pair execution

Actions:
  - Warn at 6GB / kill non-essential caches at 7GB / emergency halt at 7.5GB
  - Purge data caches
  - Enforce pair-by-pair sequential execution
"""

from __future__ import annotations

import gc
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

RESOURCE_STATE = Path(__file__).parent.parent / "local_db" / "resource_state.json"
DB_PATH        = Path(__file__).parent.parent / "data" / "autotrader.db"

RAM_WARN_GB    = 6.0
RAM_ACTION_GB  = 7.0
RAM_CRITICAL_GB = 7.5
RAM_LIMIT_GB   = 8.0

CPU_WARN_PCT   = 80.0
CPU_CRIT_PCT   = 95.0

# Rolling window sizes (bars kept in memory per pair)
MAX_BARS_PER_PAIR    = 500
MAX_PAIRS_IN_MEMORY  = 5     # never load more than 5 pairs simultaneously
MAX_CONCURRENT_OPT   = 1     # never run parallel optimization

_data_cache: Dict[str, object] = {}   # pair → DataFrame (lazy, evictable)


@dataclass
class ResourceSnapshot:
    timestamp: str
    ram_used_gb: float
    ram_pct: float
    cpu_pct: float
    pairs_in_memory: int
    cache_size_mb: float
    status: str          # "ok" | "warn" | "action" | "critical"
    actions_taken: List[str] = field(default_factory=list)


class ResourceMonitor:
    """
    Monitors and enforces resource limits.
    Call check() periodically (every iteration or every 5 min).
    """

    def __init__(self):
        self._snapshots: List[ResourceSnapshot] = []
        self._last_check: float = 0.0
        self._throttle_active: bool = False

    def check(self, force: bool = False) -> ResourceSnapshot:
        """
        Check current resource usage and take corrective action if needed.
        Rate-limited to once per 60s unless force=True.
        """
        now = time.time()
        if not force and now - self._last_check < 60:
            return self._snapshots[-1] if self._snapshots else self._empty_snapshot()
        self._last_check = now

        ram_gb, ram_pct = self._get_ram()
        cpu_pct = self._get_cpu()
        pairs_in_mem = len(_data_cache)
        cache_mb = self._estimate_cache_mb()

        actions = []

        if ram_gb >= RAM_CRITICAL_GB:
            status = "critical"
            actions.extend(self._emergency_cleanup())
        elif ram_gb >= RAM_ACTION_GB:
            status = "action"
            actions.extend(self._purge_caches())
        elif ram_gb >= RAM_WARN_GB:
            status = "warn"
            actions.extend(self._trim_caches())
        else:
            status = "ok"

        if cpu_pct >= CPU_CRIT_PCT:
            if status == "ok":
                status = "warn"
            actions.append(f"CPU critical {cpu_pct:.0f}%")

        snap = ResourceSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ram_used_gb=round(ram_gb, 2),
            ram_pct=round(ram_pct, 1),
            cpu_pct=round(cpu_pct, 1),
            pairs_in_memory=pairs_in_mem,
            cache_size_mb=round(cache_mb, 1),
            status=status,
            actions_taken=actions,
        )

        self._snapshots = self._snapshots[-100:]  # keep last 100
        self._snapshots.append(snap)

        if status in ("action", "critical"):
            logger.warning(f"[RESOURCE] {status.upper()} | RAM={ram_gb:.1f}GB "
                           f"CPU={cpu_pct:.0f}% | actions={actions}")
            self._log_to_db(snap)

        return snap

    def is_safe_to_load_pair(self, pair: str) -> bool:
        """Check if it's safe to load another pair's data into memory."""
        if pair in _data_cache:
            return True
        if len(_data_cache) >= MAX_PAIRS_IN_MEMORY:
            self._evict_oldest_pair()
        ram_gb, _ = self._get_ram()
        return ram_gb < RAM_ACTION_GB

    def load_pair_data(self, pair: str, loader_fn) -> object:
        """
        Lazy-load pair data. Evicts LRU pair if at memory limit.
        loader_fn() → DataFrame
        """
        if pair in _data_cache:
            return _data_cache[pair]

        if len(_data_cache) >= MAX_PAIRS_IN_MEMORY:
            self._evict_oldest_pair()

        ram_gb, _ = self._get_ram()
        if ram_gb >= RAM_ACTION_GB:
            logger.warning(f"[RESOURCE] Skip loading {pair} — RAM at {ram_gb:.1f}GB")
            return None

        try:
            data = loader_fn()
            # Truncate to rolling window
            if hasattr(data, '__len__') and len(data) > MAX_BARS_PER_PAIR:
                data = data.iloc[-MAX_BARS_PER_PAIR:]
            _data_cache[pair] = data
            return data
        except Exception as e:
            logger.debug(f"[RESOURCE] load {pair}: {e}")
            return None

    def evict_pair(self, pair: str):
        """Manually evict a pair from cache."""
        if pair in _data_cache:
            del _data_cache[pair]
            gc.collect()

    def get_status(self) -> dict:
        """Return current resource status summary."""
        ram_gb, ram_pct = self._get_ram()
        return {
            "ram_gb":        round(ram_gb, 2),
            "ram_pct":       round(ram_pct, 1),
            "pairs_in_mem":  len(_data_cache),
            "throttle":      self._throttle_active,
            "status":        "ok" if ram_gb < RAM_WARN_GB else
                             ("warn" if ram_gb < RAM_ACTION_GB else "action"),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _emergency_cleanup(self) -> List[str]:
        """Aggressive cleanup for critical RAM situation."""
        actions = []

        # Clear all data caches
        before = len(_data_cache)
        _data_cache.clear()
        gc.collect()
        actions.append(f"Cleared {before} pairs from data cache")

        # Force GC
        collected = gc.collect()
        actions.append(f"GC collected {collected} objects")

        self._throttle_active = True
        actions.append("Throttle activated")

        logger.error(f"[RESOURCE] EMERGENCY CLEANUP: {actions}")
        return actions

    def _purge_caches(self) -> List[str]:
        """Moderate cleanup for high RAM situation."""
        actions = []

        if len(_data_cache) > 2:
            to_remove = list(_data_cache.keys())[:-2]
            for k in to_remove:
                del _data_cache[k]
            actions.append(f"Evicted {len(to_remove)} pairs")
            gc.collect()

        return actions

    def _trim_caches(self) -> List[str]:
        """Light cleanup for warn-level RAM."""
        actions = []

        if len(_data_cache) > MAX_PAIRS_IN_MEMORY:
            to_remove = list(_data_cache.keys())[:-MAX_PAIRS_IN_MEMORY]
            for k in to_remove:
                del _data_cache[k]
            actions.append(f"Trimmed {len(to_remove)} pairs to rolling window")

        return actions

    def _evict_oldest_pair(self):
        """Evict the first (oldest) pair from cache."""
        if _data_cache:
            oldest = next(iter(_data_cache))
            del _data_cache[oldest]
            gc.collect()

    @staticmethod
    def _get_ram() -> tuple:
        """Returns (used_gb, pct). Falls back to 0 if psutil not available."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.used / (1024**3), mem.percent
        except ImportError:
            try:
                # Windows fallback via wmi-lite
                import subprocess
                result = subprocess.run(
                    ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/value"],
                    capture_output=True, text=True, timeout=5
                )
                lines = result.stdout.strip().split("\n")
                vals = {}
                for line in lines:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if v.strip().isdigit():
                            vals[k.strip()] = int(v.strip())
                total_kb = vals.get("TotalVisibleMemorySize", 8 * 1024 * 1024)
                free_kb  = vals.get("FreePhysicalMemory", 4 * 1024 * 1024)
                used_gb  = (total_kb - free_kb) / (1024 * 1024)
                pct      = (total_kb - free_kb) / total_kb * 100
                return used_gb, pct
            except Exception:
                return 0.0, 0.0

    @staticmethod
    def _get_cpu() -> float:
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            return 0.0

    @staticmethod
    def _estimate_cache_mb() -> float:
        """Rough estimate of cache size in MB."""
        total = 0
        for obj in _data_cache.values():
            try:
                if hasattr(obj, "memory_usage"):
                    total += obj.memory_usage(deep=True).sum()
                else:
                    import sys
                    total += sys.getsizeof(obj)
            except Exception:
                pass
        return total / (1024 * 1024)

    def _log_to_db(self, snap: ResourceSnapshot):
        if not DB_PATH.exists():
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO resource_log
                    (timestamp, ram_used_gb, cpu_pct, pairs_in_memory, status)
                VALUES (?, ?, ?, ?, ?)
            """, (snap.timestamp, snap.ram_used_gb, snap.cpu_pct,
                  snap.pairs_in_memory, snap.status))
            conn.commit()
            conn.close()
        except Exception:
            pass

    @staticmethod
    def _empty_snapshot() -> ResourceSnapshot:
        return ResourceSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ram_used_gb=0.0, ram_pct=0.0, cpu_pct=0.0,
            pairs_in_memory=0, cache_size_mb=0.0, status="ok",
        )


# ── Batch pair processor ──────────────────────────────────────────────────────

class PairBatchProcessor:
    """
    Executes a function over a list of pairs one-at-a-time,
    respecting memory limits and adding delays between pairs.
    """

    def __init__(self, monitor: ResourceMonitor, delay_between_pairs: float = 0.5):
        self.monitor = monitor
        self.delay = delay_between_pairs

    def process_all(self, pairs: List[str], fn, *args, **kwargs) -> Dict[str, object]:
        """
        Call fn(pair, *args, **kwargs) for each pair sequentially.
        Skips pair if memory is too high.
        Returns {pair: result}.
        """
        results = {}
        for pair in pairs:
            snap = self.monitor.check()
            if snap.status == "critical":
                logger.warning(f"[RESOURCE] Skipping {pair} — critical resource state")
                continue

            try:
                result = fn(pair, *args, **kwargs)
                results[pair] = result
            except Exception as e:
                logger.debug(f"[RESOURCE] {pair} batch error: {e}")
            finally:
                self.monitor.evict_pair(pair)
                if self.delay > 0:
                    time.sleep(self.delay)

        return results


# ── Module-level singleton ────────────────────────────────────────────────────

_monitor: Optional[ResourceMonitor] = None

def get_monitor() -> ResourceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ResourceMonitor()
    return _monitor


def check_resources() -> ResourceSnapshot:
    return get_monitor().check()


def safe_load_pair(pair: str, loader_fn) -> object:
    return get_monitor().load_pair_data(pair, loader_fn)


if __name__ == "__main__":
    mon = ResourceMonitor()
    snap = mon.check(force=True)
    print(f"RAM: {snap.ram_used_gb:.1f}GB ({snap.ram_pct:.1f}%) | "
          f"CPU: {snap.cpu_pct:.1f}% | Status: {snap.status}")
