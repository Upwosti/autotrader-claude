"""
Version snapshotting — saves periodic strategy snapshots for rollback/comparison.
"""

import copy
from datetime import datetime
from loguru import logger

from config import StrategyParams
from database.supabase_client import SupabaseClient
from backtester.engine import BacktestResult


class VersionManager:
    """Manages strategy version snapshots during evolution."""

    def __init__(self, db: SupabaseClient):
        self.db = db

    def snapshot(self, params: StrategyParams, result: BacktestResult, iteration: int):
        """Save a snapshot of the current best params at a given iteration."""
        data = {
            "iteration": iteration,
            "strategy_version": params.version,
            "params_json": params.to_dict(),
            "win_rate": result.win_rate,
            "avg_rrr": result.avg_rrr,
            "max_drawdown": result.max_drawdown_pct,
            "total_trades": result.total_trades,
            "total_return_pct": result.total_return_pct,
            "overfitting_flag": result.overfitting_flag,
            "snapshotted_at": datetime.utcnow().isoformat(),
        }
        self.db.insert("version_snapshots", data)
        logger.info(f"Snapshot saved: v{params.version} at iteration {iteration}")

    def get_best_snapshot(self) -> dict:
        """Return the snapshot with the highest win rate."""
        rows = self.db.select("version_snapshots")
        if not rows:
            return {}
        return max(rows, key=lambda r: r.get("win_rate", 0))

    def list_snapshots(self, limit: int = 20) -> list:
        """Return recent snapshots ordered by iteration."""
        rows = self.db.select("version_snapshots", limit=limit)
        return sorted(rows, key=lambda r: r.get("iteration", 0))

    def restore(self, iteration: int) -> StrategyParams:
        """Restore params from a specific iteration snapshot."""
        rows = self.db.select("version_snapshots", {"iteration": iteration}, limit=1)
        if not rows:
            raise ValueError(f"No snapshot found for iteration {iteration}")
        params_dict = rows[0]["params_json"]
        if isinstance(params_dict, str):
            import json
            params_dict = json.loads(params_dict)
        logger.info(f"Restoring params from iteration {iteration}")
        return StrategyParams.from_dict(params_dict)
