import subprocess
import os
import sys
import sqlite3
from datetime import datetime, timedelta

def sync_commodities():
    if len(sys.argv) < 2:
        print("Error: access_token required as first argument.")
        sys.exit(1)
    access_token = sys.argv[1]
    symbols = ["GOLD", "SILVER", "COPPER", "CRUDEOIL", "USDINR", "GIFTNIFTY"]
    
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_kite_connect.py")
    python_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "breeze_env", "bin", "python")
    
    # Resolve DB_PATH
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from bar_store import DB_PATH
    except ImportError:
        DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

    to_date_str = datetime.now().strftime("%Y-%m-%d")
    
    for symbol in symbols:
        # Determine from_date based on database watermark
        from_date_str = "2026-06-29" if symbol == "GIFTNIFTY" else "2026-07-01"
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=? AND timeframe='1m'", (symbol,))
            row = c.fetchone()
            if row and row[0]:
                watermark_date = row[0][:10]
                dt = datetime.strptime(watermark_date, "%Y-%m-%d") - timedelta(days=1)
                from_date_str = dt.strftime("%Y-%m-%d")
            conn.close()
        except Exception as db_err:
            print(f"Failed to query watermark for {symbol}: {db_err}")
            
        print(f"\n--- Syncing {symbol} from {from_date_str} to {to_date_str}... ---")
        cmd = [
            python_env,
            script_path,
            "--access_token", access_token,
            "--symbol", symbol,
            "--from_date", from_date_str,
            "--to_date", to_date_str,
            "--interval", "minute"
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            print("STDOUT:")
            print(res.stdout)
            if res.returncode != 0:
                print("STDERR:")
                print(res.stderr)
        except Exception as e:
            print(f"Failed to sync {symbol}: {e}")

if __name__ == "__main__":
    sync_commodities()
