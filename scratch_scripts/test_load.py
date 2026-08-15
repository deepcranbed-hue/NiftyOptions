import json
import sqlite3
from chain_store import load_capture, DB_PATH

print("DB PATH:", DB_PATH)

# Let's find the latest capture IDs
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT capture_id, captured_at FROM captures ORDER BY captured_at DESC LIMIT 5")
captures = cursor.fetchall()
print("Latest captures:")
for cap in captures:
    print(cap)
    cap_id = cap[0]
    
    # Check what expiries exist for this capture
    cursor.execute("SELECT DISTINCT expiry FROM chain_rows WHERE capture_id=?", (cap_id,))
    expiries = cursor.fetchall()
    print("  Expiries:", expiries)
    
    # Try loading with July 14th expiry
    res = load_capture(cap_id, expiry="2026-07-14T06:00:00.000Z")
    print("  Load July 14th success:", bool(res), "spot:", res.get("spot") if res else None)

conn.close()
