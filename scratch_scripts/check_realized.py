import sqlite3
from bar_store import DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Get table info/columns
cursor.execute("PRAGMA table_info(realized_metrics)")
print("Columns in realized_metrics:")
for col in cursor.fetchall():
    print(col)

# Get top 5 rows
cursor.execute("SELECT * FROM realized_metrics ORDER BY ts DESC LIMIT 5")
print("\nLatest realized_metrics rows:")
for row in cursor.fetchall():
    print(row)

conn.close()
