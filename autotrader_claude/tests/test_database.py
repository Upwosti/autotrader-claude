"""
Unit tests for database layer (runs against local JSON fallback).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import tempfile
import shutil

from database.supabase_client import SupabaseClient

# Override local DB dir to temp folder for tests
import database.supabase_client as db_module


class TestSupabaseClientLocal(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = db_module.LOCAL_DB_DIR
        db_module.LOCAL_DB_DIR = self.tmpdir
        self.db = SupabaseClient()
        self.db.online = False  # Force local mode

    def tearDown(self):
        db_module.LOCAL_DB_DIR = self._orig_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_and_select(self):
        self.db.insert("test_table", {"key": "hello", "value": 42})
        rows = self.db.select("test_table")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "hello")

    def test_select_with_filter(self):
        self.db.insert("test_table", {"key": "a", "value": 1})
        self.db.insert("test_table", {"key": "b", "value": 2})
        rows = self.db.select("test_table", {"key": "a"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "a")

    def test_upsert_inserts_new(self):
        self.db.upsert("test_table", {"id": 1, "val": "first"})
        rows = self.db.select("test_table")
        self.assertGreater(len(rows), 0)

    def test_update_modifies_row(self):
        self.db.insert("test_table", {"key": "x", "value": 10})
        self.db.update("test_table", {"key": "x"}, {"value": 99})
        rows = self.db.select("test_table", {"key": "x"})
        self.assertEqual(rows[0]["value"], 99)

    def test_get_set_state(self):
        self.db.set_state("my_key", "my_value")
        val = self.db.get_state("my_key")
        self.assertEqual(val, "my_value")

    def test_get_state_returns_none_when_missing(self):
        val = self.db.get_state("nonexistent_key")
        self.assertIsNone(val)

    def test_increment_trades(self):
        self.db.increment_trades(5)
        self.assertEqual(self.db.get_total_trades(), 5)
        self.db.increment_trades(3)
        self.assertEqual(self.db.get_total_trades(), 8)

    def test_get_current_version_default(self):
        v = self.db.get_current_version()
        self.assertEqual(v, 1)

    def test_select_limit(self):
        for i in range(10):
            self.db.insert("limit_test", {"n": i})
        rows = self.db.select("limit_test", limit=3)
        self.assertEqual(len(rows), 3)

    def test_inserted_at_added(self):
        self.db.insert("ts_test", {"data": "hello"})
        rows = self.db.select("ts_test")
        self.assertIn("_inserted_at", rows[0])


if __name__ == "__main__":
    unittest.main()
