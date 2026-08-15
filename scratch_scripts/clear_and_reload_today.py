import sqlite3
import subprocess
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import DB_PATH

def clear_and_reload_today():
    session_token = "56225492"
    expiry = "2026-07-07T06:00:00.000Z"
    
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        # Delete only captures and rows belonging to July 7th
        print("Clearing option chain rows and captures for today (2026-07-07)...")
        
        # Get today's capture IDs
        cursor = conn.execute("SELECT capture_id FROM captures WHERE captured_at LIKE '2026-07-07%'")
        cids = [r[0] for r in cursor.fetchall()]
        
        if cids:
            cids_str = ",".join(str(cid) for cid in cids)
            conn.execute(f"DELETE FROM chain_rows WHERE capture_id IN ({cids_str})")
            conn.execute(f"DELETE FROM captures WHERE capture_id IN ({cids_str})")
            conn.commit()
            print(f"Successfully deleted {len(cids)} captures and their associated option chain rows for today.")
        else:
            print("No captures found for today to delete.")
            
    except Exception as e:
        print(f"Error clearing database: {e}")
        return
    finally:
        conn.close()
        
    print("Launching fetch_historical_option_chain.py backfill for today...")
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_historical_option_chain.py")
    python_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "breeze_env", "bin", "python")
    
    cmd = [
        python_env,
        script_path,
        session_token,
        expiry,
        "NIFTY",
        "1minute",
        "2026-07-07",
        "2026-07-07"
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        print("STDOUT:")
        print(res.stdout)
        print("STDERR:")
        print(res.stderr)
        if res.returncode == 0:
            print("Today's option chain reload completed successfully! 🎉")
        else:
            print(f"Reload failed with exit code: {res.returncode}")
    except Exception as e:
        print(f"Subprocess run failed: {e}")

if __name__ == "__main__":
    clear_and_reload_today()
