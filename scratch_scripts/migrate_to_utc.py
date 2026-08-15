import sqlite3
import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure self-contained timezone helpers
IST = timezone(timedelta(hours=5, minutes=30))

def parse_ist_str(ts_str: str) -> datetime:
    s = ts_str.strip()
    if ' ' in s:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=IST)
    elif 'T' in s:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=IST)
        return dt
    else:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=IST)

def to_db_ts(dt_val) -> str:
    if isinstance(dt_val, str):
        dt = parse_ist_str(dt_val)
    else:
        dt = dt_val
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

def to_db_minute(dt_val) -> str:
    if isinstance(dt_val, str):
        dt = parse_ist_str(dt_val)
    else:
        dt = dt_val
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:00Z")

def run_migration():
    db_path = "option_chains.db"
    if not os.path.exists(db_path):
        print(f"Error: Database {db_path} not found.")
        sys.exit(1)

    print("Connecting to database...")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;") # Temporary off for migration rebuilds
    cursor = conn.cursor()

    try:
        # Start a single transaction
        cursor.execute("BEGIN TRANSACTION;")

        # === 1. Migrate captures table ===
        print("Checking captures table columns...")
        # Get existing columns in captures
        cursor.execute("PRAGMA table_info(captures);")
        cols = [row[1] for row in cursor.fetchall()]

        if "exchange_code" not in cols:
            print("Adding exchange_code to captures...")
            cursor.execute("ALTER TABLE captures ADD COLUMN exchange_code TEXT NOT NULL DEFAULT 'NFO';")
        if "underlying" not in cols:
            print("Adding underlying to captures...")
            cursor.execute("ALTER TABLE captures ADD COLUMN underlying TEXT NOT NULL DEFAULT 'NIFTY';")
        if "snapshot_minute" not in cols:
            print("Adding snapshot_minute to captures...")
            cursor.execute("ALTER TABLE captures ADD COLUMN snapshot_minute TEXT;")
        if "status" not in cols:
            print("Adding status to captures...")
            cursor.execute("ALTER TABLE captures ADD COLUMN status TEXT NOT NULL DEFAULT 'complete';")
        if "trigger" not in cols:
            print("Adding trigger to captures...")
            cursor.execute("ALTER TABLE captures ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual';")

        # Update and format timestamps in captures
        print("Migrating captures timestamps to UTC and populating snapshot_minute...")
        cursor.execute("SELECT capture_id, captured_at FROM captures;")
        captures_rows = cursor.fetchall()
        for cid, cat in captures_rows:
            utc_ts = to_db_ts(cat)
            utc_minute = to_db_minute(cat)
            cursor.execute(
                "UPDATE captures SET captured_at = ?, snapshot_minute = ? WHERE capture_id = ?;",
                (utc_ts, utc_minute, cid)
            )

        # Create unique index on captures
        print("Creating unique index on captures...")
        cursor.execute("DROP INDEX IF EXISTS ix_cap_time;")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_cap_time ON captures(captured_at);")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_captures_snapshot ON captures(exchange_code, underlying, snapshot_minute);")

        # === 2. Migrate price_bars table ===
        print("Checking price_bars table schema...")
        cursor.execute("PRAGMA table_info(price_bars);")
        bar_cols = [row[1] for row in cursor.fetchall()]

        # Rebuild price_bars to support composite natural key: (exchange, symbol, timeframe, ts)
        cursor.execute("ALTER TABLE price_bars RENAME TO price_bars_old;")
        
        cursor.execute("""
        CREATE TABLE price_bars (
            exchange  TEXT NOT NULL DEFAULT 'NSE',
            symbol    TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts        TEXT NOT NULL,
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            volume    REAL,
            PRIMARY KEY (exchange, symbol, timeframe, ts)
        );
        """)

        print("Converting and migrating price_bars to UTC...")
        cursor.execute("SELECT symbol, timeframe, ts, open, high, low, close, volume FROM price_bars_old;")
        bar_rows = cursor.fetchall()
        
        batch = []
        for symbol, timeframe, ts, o, h, l, c, v in bar_rows:
            utc_ts = to_db_ts(ts)
            batch.append(('NSE', symbol, timeframe, utc_ts, o, h, l, c, v))
            
        cursor.executemany(
            "INSERT OR REPLACE INTO price_bars(exchange, symbol, timeframe, ts, open, high, low, close, volume) VALUES(?,?,?,?,?,?,?,?,?);",
            batch
        )
        
        cursor.execute("DROP TABLE price_bars_old;")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_bars ON price_bars(exchange, symbol, timeframe, ts);")

        # === 3. Create instruments table ===
        print("Creating instruments table...")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            exchange_code TEXT NOT NULL,
            underlying    TEXT NOT NULL,
            lot_size      INTEGER,
            strike_step   REAL,
            tick_size     REAL,
            session_open  TEXT,
            session_close TEXT,
            holiday_calendar TEXT,
            PRIMARY KEY (exchange_code, underlying)
        );
        """)
        # Seed default row
        cursor.execute("""
        INSERT OR REPLACE INTO instruments (exchange_code, underlying, lot_size, strike_step, tick_size, session_open, session_close, holiday_calendar)
        VALUES ('NFO', 'NIFTY', 75, 50, 0.05, '09:15', '15:30', 'NSE_2026');
        """)

        # Commit transaction
        conn.execute("COMMIT;")
        print("Migration transaction completed successfully.")

    except Exception as e:
        conn.execute("ROLLBACK;")
        print("Migration failed, transaction rolled back!")
        raise e
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
