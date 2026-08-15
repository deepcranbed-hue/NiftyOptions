import sqlite3

DB_PATH = "options.db" # or option_chains.db depending on what main.py uses

def run():
    # Check both potential database files
    for db in ["options.db", "option_chains.db", "nifty_options.db", "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"]:
        try:
            conn = sqlite3.connect(db)
            print(f"Initializing realized tables in {db}...")
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT NOT NULL,
                ts TEXT NOT NULL,
                o REAL,
                h REAL,
                l REAL,
                c REAL,
                v REAL,
                quality_flags TEXT,
                PRIMARY KEY (symbol, ts)
            );
            """)
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS realized_metrics (
                ts TEXT NOT NULL,
                window INTEGER NOT NULL,
                rv_index REAL,
                rv_constituent_weighted REAL,
                corr_avg REAL,
                dispersion REAL,
                flags TEXT,
                PRIMARY KEY (ts, window)
            );
            """)
            
            conn.commit()
            conn.close()
            print("Done.")
        except Exception as e:
            print(f"Error for {db}: {e}")

if __name__ == "__main__":
    run()
