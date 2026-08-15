import sqlite3
import os

db_path = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

print(f"Checking database: {db_path}")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Check max cash timestamp
    c.execute("SELECT symbol, MAX(ts), COUNT(*) FROM price_bars GROUP BY symbol ORDER BY MAX(ts) DESC LIMIT 5")
    print("\n--- Max cash data timestamps ---")
    for r in c.fetchall():
        print(f"Symbol: {r[0]} | Last timestamp: {r[1]} | Total bars: {r[2]}")
        
    # Check max option chain capture timestamp
    try:
        c.execute("SELECT MAX(captured_at), COUNT(*) FROM captures")
        print("\n--- Option Chain captures ---")
        for r in c.fetchall():
            print(f"Last capture: {r[0]} | Total captures: {r[1]}")
    except Exception as e:
        print(f"Could not read captures table: {e}")
        
    conn.close()
else:
    print("Database file not found!")
