import unittest
import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure python can import backend
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from chain_store import init_db, save_from_json_rows, load_capture, DB_PATH
from bar_store import init_bars, save_bars, get_bars, get_bar_range
from backend.timeutil import to_db_ts, to_db_minute

class TestDbMigration(unittest.TestCase):
    def setUp(self):
        # Use a temporary test database file
        self.test_db = "test_option_chains.db"
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_db(self.test_db)
        init_bars(self.test_db)

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_schema_columns(self):
        conn = sqlite3.connect(self.test_db)
        cursor = conn.cursor()
        
        # Verify captures table has status and trigger columns
        cursor.execute("PRAGMA table_info(captures)")
        cols = {row[1]: row for row in cursor.fetchall()}
        self.assertIn("exchange_code", cols)
        self.assertIn("underlying", cols)
        self.assertIn("snapshot_minute", cols)
        self.assertIn("status", cols)
        self.assertIn("trigger", cols)
        
        # Verify instruments table exists and NIFTY is seeded
        cursor.execute("SELECT * FROM instruments WHERE underlying='NIFTY'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "NFO")
        self.assertEqual(row[2], 75) # NIFTY lot size
        
        conn.close()

    def test_timezone_bars(self):
        # Insert raw bars
        rows = [
            ("2026-07-03T14:41:00+05:30", 24200.0, 24210.0, 24190.0, 24205.0, 100.0)
        ]
        
        # This will convert IST to UTC Z before saving
        utc_ts = to_db_ts("2026-07-03T14:41:00+05:30")
        self.assertEqual(utc_ts, "2026-07-03T09:11:00Z")
        
        save_bars([(utc_ts, 24200.0, 24210.0, 24190.0, 24205.0, 100.0)], exchange="NSE", symbol="NIFTY", timeframe="1m", db=self.test_db)
        
        # Query from DB and check timezone
        bars = get_bars(exchange="NSE", symbol="NIFTY", timeframe="1m", db=self.test_db)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["ts"], "2026-07-03T09:11:00Z")
        
        # Test range query
        range_info = get_bar_range(exchange="NSE", symbol="NIFTY", timeframe="1minute", db=self.test_db)
        self.assertEqual(range_info["min_date"], "2026-07-03T09:11:00Z")
        self.assertEqual(range_info["max_date"], "2026-07-03T09:11:00Z")
        self.assertEqual(range_info["count"], 1)

    def test_duplicate_snapshot_collision(self):
        # Insert a capture
        ts = "2026-07-04T13:55:00+05:30"
        utc_ts = to_db_ts(ts) # 2026-07-04T08:25:00Z
        
        cid1 = save_from_json_rows(
            [{"strike": 24000.0, "call_ltp": 120.0, "put_ltp": 95.0}],
            expiry="2026-07-09",
            spot=24200.0,
            captured_at=utc_ts,
            exchange_code="NFO",
            underlying="NIFTY",
            status="complete",
            trigger="manual",
            db=self.test_db
        )
        self.assertIsNotNone(cid1)
        
        # Attempt to insert same minute capture (should collide and return None)
        # E.g. different exact seconds (13:55:12) but floored to same minute
        ts2 = "2026-07-04T13:55:12+05:30"
        utc_ts2 = to_db_ts(ts2)
        
        cid2 = save_from_json_rows(
            [{"strike": 24000.0, "call_ltp": 122.0, "put_ltp": 93.0}],
            expiry="2026-07-09",
            spot=24200.0,
            captured_at=utc_ts2,
            exchange_code="NFO",
            underlying="NIFTY",
            status="complete",
            trigger="manual",
            db=self.test_db
        )
        self.assertIsNone(cid2) # Unique index check skips and returns None

if __name__ == '__main__':
    unittest.main()
