import sys
import os
import sqlite3
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chain_store import DB_PATH

def main():
    print(f"Using database path: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("ERROR: Database file does not exist!")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Get latest captures
    print("\n--- Latest 5 Captures in Database ---")
    cursor.execute("""
        SELECT capture_id, captured_at, underlying, spot 
        FROM captures 
        ORDER BY captured_at DESC LIMIT 5
    """)
    rows = cursor.fetchall()
    if not rows:
        print("No captures found in the database!")
        conn.close()
        return

    for r in rows:
        print(f"Capture ID: {r[0]} | Time: {r[1]} | Symbol: {r[2]} | Spot: {r[3]}")

    latest_capture_id = rows[0][0]
    latest_time = rows[0][1]

    # 2. Get table info/schema
    print("\n--- chain_rows Table Schema ---")
    cursor.execute("PRAGMA table_info(chain_rows)")
    columns = cursor.fetchall()
    for col in columns:
        print(f"Col ID: {col[0]} | Name: {col[1]} | Type: {col[2]}")

    print("\n--- captures Table Schema ---")
    cursor.execute("PRAGMA table_info(captures)")
    columns = cursor.fetchall()
    for col in columns:
        print(f"Col ID: {col[0]} | Name: {col[1]} | Type: {col[2]}")

    conn.close()

if __name__ == "__main__":
    main()
