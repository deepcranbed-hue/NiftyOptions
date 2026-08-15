import sys
import os
import sqlite3
from datetime import datetime, timezone, timedelta

# Adjust python path to find backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.timeutil import to_db_ts

DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

def cleanup():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return
        
    print(f"Connecting to database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Fetch all rows in price_bars
    print("Reading price_bars table...")
    rows = cursor.execute("SELECT exchange, symbol, timeframe, ts, open, high, low, close, volume FROM price_bars").fetchall()
    print(f"Total rows read: {len(rows)}")
    
    # 2. Group and normalize rows in memory
    normalized_dict = {}
    duplicates_count = 0
    
    for row in rows:
        exchange, symbol, timeframe, ts, o, h, l, c, v = row
        try:
            norm_ts = to_db_ts(ts)
        except Exception as e:
            print(f"Failed to parse timestamp '{ts}': {e}")
            norm_ts = ts
            
        key = (exchange, symbol, timeframe, norm_ts)
        
        # If key already exists, increment duplicate count and keep the newer one or merge
        if key in normalized_dict:
            duplicates_count += 1
            # We keep the one with larger volume or non-null fields
            existing = normalized_dict[key]
            existing_vol = existing[8] or 0
            new_vol = v or 0
            if new_vol > existing_vol:
                normalized_dict[key] = row
        else:
            normalized_dict[key] = row
            
    print(f"Identified {duplicates_count} duplicate timestamp formats.")
    print(f"Normalized unique rows: {len(normalized_dict)}")
    
    if duplicates_count > 0:
        # 3. Truncate the table and insert clean normalized rows
        print("Cleaning up database price_bars...")
        cursor.execute("DELETE FROM price_bars")
        
        insert_batch = []
        for key, row in normalized_dict.items():
            exchange, symbol, timeframe, ts, o, h, l, c, v = row
            norm_ts = key[3]
            insert_batch.append((exchange, symbol, timeframe, norm_ts, o, h, l, c, v))
            
        cursor.executemany(
            "INSERT OR REPLACE INTO price_bars(exchange, symbol, timeframe, ts, open, high, low, close, volume) VALUES(?,?,?,?,?,?,?,?,?)",
            insert_batch
        )
        conn.commit()
        print("Database cleanup completed successfully!")
    else:
        print("No duplicate timestamp formats detected in the database. Everything is clean!")
        
    conn.close()

if __name__ == "__main__":
    cleanup()
