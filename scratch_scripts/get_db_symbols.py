import sqlite3
import os

DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Get tables in the database
    tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Tables in database:", tables)

    for table in tables:
        try:
            # Check distinct symbols in each table if it has a 'symbol' column
            columns = [col[1] for col in c.execute(f"PRAGMA table_info({table})").fetchall()]
            if 'symbol' in columns:
                distinct_symbols = [row[0] for row in c.execute(f"SELECT DISTINCT symbol FROM {table} LIMIT 20").fetchall()]
                print(f"Distinct symbols in table '{table}': {distinct_symbols}")
            elif 'underlying' in columns:
                distinct_symbols = [row[0] for row in c.execute(f"SELECT DISTINCT underlying FROM {table} LIMIT 20").fetchall()]
                print(f"Distinct underlyings in table '{table}': {distinct_symbols}")
        except Exception as e:
            print(f"Error reading table '{table}': {e}")

if __name__ == "__main__":
    main()
