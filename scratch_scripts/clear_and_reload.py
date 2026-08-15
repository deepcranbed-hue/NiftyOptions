import sqlite3
import subprocess
import sys
import os

# We will resolve DB_PATH directly from bar_store to be safe
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import DB_PATH

def clear_and_reload():
    session_token = "56218283"
    expiry = "2026-07-07T06:00:00.000Z"
    
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        # Delete only option chain rows and manual captures
        print("Clearing option chain rows and captures...")
        conn.execute("DELETE FROM chain_rows")
        conn.execute("DELETE FROM captures WHERE trigger='manual'")
        conn.commit()
        print("Option chain tables cleared successfully.")
    except Exception as e:
        print(f"Error clearing database: {e}")
        return
    finally:
        conn.close()
        
    print("Launching fetch_historical_option_chain.py backfill...")
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_historical_option_chain.py")
    python_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "breeze_env", "bin", "python")
    
    cmd = [
        python_env,
        script_path,
        session_token,
        expiry,
        "NIFTY",
        "1minute",
        "2026-06-29",
        "2026-07-06"
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        print("STDOUT:")
        print(res.stdout)
        print("STDERR:")
        print(res.stderr)
        if res.returncode == 0:
            print("Option chain reload completed successfully! 🎉")
        else:
            print(f"Reload failed with exit code: {res.returncode}")
    except Exception as e:
        print(f"Subprocess run failed: {e}")

if __name__ == "__main__":
    clear_and_reload()
