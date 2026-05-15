"""PostgreSQLClient — local PostgreSQL for primary storage."""

import os
import json
from typing import List, Dict, Optional
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    logger.warning("psycopg2 not available — PostgreSQL storage disabled")


class PostgreSQLClient:
    """Local PostgreSQL client for primary trade and iteration storage."""

    def __init__(self):
        self.available = PSYCOPG2_AVAILABLE
        self.conn = None

        if not self.available:
            logger.warning("PostgreSQLClient: psycopg2 not installed, running as no-op")
            return

        self._host = os.environ.get("POSTGRES_HOST", "localhost")
        self._port = int(os.environ.get("POSTGRES_PORT", 5432))
        self._db = os.environ.get("POSTGRES_DB", "autotrader")
        self._user = os.environ.get("POSTGRES_USER", "autotrader")
        self._password = os.environ.get("POSTGRES_PASSWORD", "")

        try:
            self._connect()
            self._ensure_tables()
            logger.info(f"PostgreSQLClient connected to {self._host}:{self._port}/{self._db}")
        except Exception as e:
            logger.error(f"PostgreSQLClient init failed: {e}")
            self.available = False

    def _connect(self):
        self.conn = psycopg2.connect(
            host=self._host,
            port=self._port,
            dbname=self._db,
            user=self._user,
            password=self._password,
            connect_timeout=5,
        )
        self.conn.autocommit = True

    def _ensure_tables(self):
        if not self.available or self.conn is None:
            return
        ddl = """
        CREATE TABLE IF NOT EXISTS trades (
            id          SERIAL PRIMARY KEY,
            pair        TEXT,
            direction   TEXT,
            entry       FLOAT,
            sl          FLOAT,
            tp          FLOAT,
            size        FLOAT,
            outcome     TEXT,
            pnl_pct     FLOAT,
            rrr_achieved FLOAT,
            opened_at   TIMESTAMP,
            closed_at   TIMESTAMP,
            iteration   INT,
            params      JSONB
        );

        CREATE TABLE IF NOT EXISTS iterations (
            id          SERIAL PRIMARY KEY,
            iteration   INT,
            xauusd_wr   FLOAT,
            agg_wr      FLOAT,
            score       FLOAT,
            params      JSONB,
            saved_at    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ml_metrics (
            id          SERIAL PRIMARY KEY,
            model_name  TEXT,
            accuracy    FLOAT,
            n_trades    INT,
            trained_at  TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pair_performance (
            id          SERIAL PRIMARY KEY,
            pair        TEXT,
            wr          FLOAT,
            rrr         FLOAT,
            n_trades    INT,
            updated_at  TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS key_value (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TIMESTAMP DEFAULT NOW()
        );
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(ddl)
            logger.debug("PostgreSQL tables ensured")
        except Exception as e:
            logger.error(f"_ensure_tables failed: {e}")

    # ------------------------------------------------------------------ #
    #  Write helpers
    # ------------------------------------------------------------------ #

    def insert_trade(self, trade_dict: dict):
        """INSERT a completed trade record."""
        if not self.available or self.conn is None:
            return
        try:
            sql = """
            INSERT INTO trades
                (pair, direction, entry, sl, tp, size, outcome, pnl_pct,
                 rrr_achieved, opened_at, closed_at, iteration, params)
            VALUES
                (%(pair)s, %(direction)s, %(entry)s, %(sl)s, %(tp)s,
                 %(size)s, %(outcome)s, %(pnl_pct)s, %(rrr_achieved)s,
                 %(opened_at)s, %(closed_at)s, %(iteration)s, %(params)s)
            """
            row = dict(trade_dict)
            if "params" in row and isinstance(row["params"], dict):
                row["params"] = json.dumps(row["params"])
            with self.conn.cursor() as cur:
                cur.execute(sql, row)
        except Exception as e:
            logger.error(f"insert_trade failed: {e}")
            self._try_reconnect()

    def insert_iteration(self, iter_dict: dict):
        """INSERT an evolution iteration snapshot."""
        if not self.available or self.conn is None:
            return
        try:
            sql = """
            INSERT INTO iterations
                (iteration, xauusd_wr, agg_wr, score, params, saved_at)
            VALUES
                (%(iteration)s, %(xauusd_wr)s, %(agg_wr)s, %(score)s,
                 %(params)s, %(saved_at)s)
            """
            row = dict(iter_dict)
            if "params" in row and isinstance(row["params"], dict):
                row["params"] = json.dumps(row["params"])
            with self.conn.cursor() as cur:
                cur.execute(sql, row)
        except Exception as e:
            logger.error(f"insert_iteration failed: {e}")
            self._try_reconnect()

    # ------------------------------------------------------------------ #
    #  Read helpers
    # ------------------------------------------------------------------ #

    def get_last_n_iterations(self, n: int = 200) -> List[Dict]:
        """Return the last n iteration rows as a list of dicts."""
        if not self.available or self.conn is None:
            return []
        try:
            sql = """
            SELECT iteration, xauusd_wr, agg_wr, score, params, saved_at
            FROM   iterations
            ORDER  BY id DESC
            LIMIT  %s
            """
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (n,))
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"get_last_n_iterations failed: {e}")
            self._try_reconnect()
            return []

    def get_pair_performance(self) -> Dict[str, Dict]:
        """Return latest per-pair metrics keyed by pair symbol."""
        if not self.available or self.conn is None:
            return {}
        try:
            sql = """
            SELECT DISTINCT ON (pair)
                pair, wr, rrr, n_trades, updated_at
            FROM   pair_performance
            ORDER  BY pair, updated_at DESC
            """
            with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            return {r["pair"]: dict(r) for r in rows}
        except Exception as e:
            logger.error(f"get_pair_performance failed: {e}")
            self._try_reconnect()
            return {}

    # ------------------------------------------------------------------ #
    #  Key-value store
    # ------------------------------------------------------------------ #

    def save_state(self, key: str, value: str):
        """Upsert a key-value pair into the key_value table."""
        if not self.available or self.conn is None:
            return
        try:
            sql = """
            INSERT INTO key_value (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = NOW()
            """
            with self.conn.cursor() as cur:
                cur.execute(sql, (key, value))
        except Exception as e:
            logger.error(f"save_state failed: {e}")
            self._try_reconnect()

    # ------------------------------------------------------------------ #
    #  Health
    # ------------------------------------------------------------------ #

    def is_connected(self) -> bool:
        if not self.available or self.conn is None:
            return False
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def reconnect(self):
        """Attempt to re-establish the database connection."""
        if not self.available:
            return
        try:
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self._connect()
            logger.info("PostgreSQLClient reconnected")
        except Exception as e:
            logger.error(f"PostgreSQLClient reconnect failed: {e}")

    def _try_reconnect(self):
        if not self.is_connected():
            self.reconnect()
