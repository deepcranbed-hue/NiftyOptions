import sqlite3
import os

DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

print("Distinct expiries in database:")
cursor.execute("SELECT DISTINCT expiry FROM chain_rows")
for row in cursor.fetchall():
    print(row)

conn.close()
