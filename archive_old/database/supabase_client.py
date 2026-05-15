"""
Supabase client wrapper with graceful offline fallback.
When Supabase is not configured, logs to local JSON files instead.
"""

import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from loguru import logger

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

from config import SUPABASE_URL, SUPABASE_KEY

LOCAL_DB_DIR = "C:\\Users\\Administrator\\Desktop\\AutoTraderClaude\\local_db"


class SupabaseClient:
    """Wraps Supabase with a local JSON fallback when offline."""

    def __init__(self):
        self.client: Optional[Any] = None
        self.online = False
        os.makedirs(LOCAL_DB_DIR, exist_ok=True)
        self._connect()

    def _connect(self):
        if not SUPABASE_AVAILABLE:
            logger.debug("supabase not installed — local fallback")
            return
        if not SUPABASE_URL or not SUPABASE_KEY:
            logger.debug("SUPABASE not configured — local fallback")
            return
        try:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            # Test connection
            self.client.table("system_state").select("key").limit(1).execute()
            self.online = True
            logger.info("Supabase connected")
        except Exception as e:
            logger.warning(f"Supabase offline: {e} — using local fallback")
            self.online = False

    # ─── Generic CRUD ─────────────────────────────────────────────────────

    def insert(self, table: str, data: Dict[str, Any]) -> Optional[Dict]:
        """Insert a row. Falls back to local JSON."""
        data["_inserted_at"] = datetime.utcnow().isoformat()
        if self.online:
            try:
                res = self.client.table(table).insert(data).execute()
                return res.data[0] if res.data else None
            except Exception as e:
                logger.error(f"Supabase insert error ({table}): {e}")
        return self._local_insert(table, data)

    def select(self, table: str, filters: Optional[Dict] = None,
               limit: int = 1000) -> List[Dict]:
        """Select rows with optional filters."""
        if self.online:
            try:
                q = self.client.table(table).select("*")
                if filters:
                    for k, v in filters.items():
                        q = q.eq(k, v)
                res = q.limit(limit).execute()
                return res.data or []
            except Exception as e:
                logger.error(f"Supabase select error ({table}): {e}")
        return self._local_select(table, filters, limit)

    def update(self, table: str, match: Dict, data: Dict) -> Optional[Dict]:
        """Update rows matching the match dict."""
        if self.online:
            try:
                q = self.client.table(table)
                for k, v in match.items():
                    q = q.eq(k, v)
                res = q.update(data).execute()
                return res.data[0] if res.data else None
            except Exception as e:
                logger.error(f"Supabase update error ({table}): {e}")
        return self._local_update(table, match, data)

    def upsert(self, table: str, data: Dict) -> Optional[Dict]:
        """Insert or update."""
        if self.online:
            try:
                res = self.client.table(table).upsert(data).execute()
                return res.data[0] if res.data else None
            except Exception as e:
                logger.error(f"Supabase upsert error ({table}): {e}")
        return self._local_insert(table, data)

    # ─── Local JSON fallback ──────────────────────────────────────────────

    def _table_path(self, table: str) -> str:
        return os.path.join(LOCAL_DB_DIR, f"{table}.json")

    def _load_table(self, table: str) -> List[Dict]:
        path = self._table_path(table)
        if not os.path.exists(path):
            return []
        with open(path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []

    def _save_table(self, table: str, rows: List[Dict]):
        with open(self._table_path(table), "w") as f:
            json.dump(rows, f, indent=2, default=str)

    def _local_insert(self, table: str, data: Dict) -> Dict:
        rows = self._load_table(table)
        data["id"] = len(rows) + 1
        rows.append(data)
        self._save_table(table, rows)
        return data

    def _local_select(self, table: str, filters: Optional[Dict], limit: int) -> List[Dict]:
        rows = self._load_table(table)
        if filters:
            for k, v in filters.items():
                rows = [r for r in rows if r.get(k) == v]
        return rows[:limit]

    def _local_update(self, table: str, match: Dict, data: Dict) -> Optional[Dict]:
        rows = self._load_table(table)
        updated = None
        for row in rows:
            if all(row.get(k) == v for k, v in match.items()):
                row.update(data)
                updated = row
        self._save_table(table, rows)
        return updated

    # ─── Convenience helpers ──────────────────────────────────────────────

    def get_state(self, key: str) -> Optional[str]:
        rows = self.select("system_state", {"key": key}, limit=1)
        return rows[0]["value"] if rows else None

    def set_state(self, key: str, value: str):
        self.upsert("system_state", {"key": key, "value": str(value)})

    def get_current_version(self) -> int:
        v = self.get_state("current_version")
        return int(v) if v else 1

    def get_total_trades(self) -> int:
        v = self.get_state("total_trades")
        return int(v) if v else 0

    def increment_trades(self, n: int = 1):
        current = self.get_total_trades()
        self.set_state("total_trades", str(current + n))

