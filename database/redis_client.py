"""RedisClient — fast cache layer for live prices and signals."""

import os
import json
import time
from typing import Optional, Dict, Any
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis not available — using in-memory fallback cache")


class RedisClient:
    """Redis cache client with transparent in-memory fallback."""

    def __init__(self):
        self.available = REDIS_AVAILABLE
        self._r = None
        self._cache: Dict[str, tuple] = {}  # key -> (value, expires_at)

        if not self.available:
            logger.warning("RedisClient: redis package not installed, using in-memory dict")
            return

        self._host = os.environ.get("REDIS_HOST", "localhost")
        self._port = int(os.environ.get("REDIS_PORT", 6379))
        self._db = int(os.environ.get("REDIS_DB", 0))

        try:
            self._connect()
            logger.info(f"RedisClient connected to {self._host}:{self._port}/{self._db}")
        except Exception as e:
            logger.error(f"RedisClient init failed: {e} — falling back to in-memory cache")
            self.available = False

    def _connect(self):
        self._r = redis_lib.Redis(
            host=self._host,
            port=self._port,
            db=self._db,
            socket_connect_timeout=3,
            decode_responses=True,
        )
        self._r.ping()

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _set(self, key: str, value: str, ttl: int):
        if self.available and self._r is not None:
            try:
                self._r.setex(key, ttl, value)
                return
            except Exception as e:
                logger.debug(f"Redis SET failed ({e}), using in-memory fallback")
        expires_at = time.time() + ttl if ttl > 0 else float("inf")
        self._cache[key] = (value, expires_at)

    def _get(self, key: str) -> Optional[str]:
        if self.available and self._r is not None:
            try:
                return self._r.get(key)
            except Exception as e:
                logger.debug(f"Redis GET failed ({e}), using in-memory fallback")
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._cache[key]
            return None
        return value

    def _del_pattern(self, pattern: str):
        if self.available and self._r is not None:
            try:
                keys = self._r.keys(pattern)
                if keys:
                    self._r.delete(*keys)
                return
            except Exception as e:
                logger.debug(f"Redis DEL pattern failed ({e}), using in-memory fallback")
        prefix = pattern.rstrip("*")
        to_delete = [k for k in list(self._cache.keys()) if k.startswith(prefix)]
        for k in to_delete:
            del self._cache[k]

    # ------------------------------------------------------------------ #
    #  Price
    # ------------------------------------------------------------------ #

    def set_price(self, pair: str, price: float, ttl: int = 60):
        self._set(f"price:{pair}", str(price), ttl)

    def get_price(self, pair: str) -> Optional[float]:
        val = self._get(f"price:{pair}")
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    #  Regime
    # ------------------------------------------------------------------ #

    def set_regime(self, pair: str, regime: str, ttl: int = 3600):
        self._set(f"regime:{pair}", regime, ttl)

    def get_regime(self, pair: str) -> Optional[str]:
        return self._get(f"regime:{pair}")

    # ------------------------------------------------------------------ #
    #  Signal
    # ------------------------------------------------------------------ #

    def set_signal(self, pair: str, signal: dict, ttl: int = 300):
        self._set(f"signal:{pair}", json.dumps(signal), ttl)

    def get_signal(self, pair: str) -> Optional[Dict]:
        val = self._get(f"signal:{pair}")
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    #  Generic metric
    # ------------------------------------------------------------------ #

    def set_metric(self, key: str, value: Any, ttl: int = 7200):
        if isinstance(value, (dict, list)):
            serialized = json.dumps(value)
        else:
            serialized = str(value)
        self._set(f"metric:{key}", serialized, ttl)

    def get_metric(self, key: str) -> Any:
        val = self._get(f"metric:{key}")
        if val is None:
            return None
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val

    # ------------------------------------------------------------------ #
    #  Session status
    # ------------------------------------------------------------------ #

    def set_session_status(self, status: str):
        self._set("session:status", status, ttl=86400)

    def get_session_status(self) -> str:
        val = self._get("session:status")
        return val if val is not None else "unknown"

    # ------------------------------------------------------------------ #
    #  Bulk ops
    # ------------------------------------------------------------------ #

    def flush_signals(self):
        """Delete all signal:* keys."""
        self._del_pattern("signal:*")

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        if self.available and self._r is not None:
            try:
                return self._r.ping()
            except Exception:
                return False
        return True  # in-memory always "alive"

    def is_connected(self) -> bool:
        return self.ping()
