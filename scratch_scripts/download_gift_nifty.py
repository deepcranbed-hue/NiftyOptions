import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime

# Adjust path to find backend and bar_store
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars
from backend.timeutil import to_db_ts

def make_request(url, headers=None, method="GET", data=None):
    if data:
        data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()

def main():
    # Load Zerodha Kite session info
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # We look for today's session or fall back to any session file in zerodhasession
    session_dir = os.path.join(base_dir, "zerodhasession")
    session_file = None
    
    if os.path.exists(session_dir):
        files = sorted([f for f in os.listdir(session_dir) if f.startswith("session_") and f.endswith(".json")], reverse=True)
        if files:
            session_file = os.path.join(session_dir, files[0])

    if not session_file:
        print(json.dumps({"error": "No active Zerodha Kite session found in zerodhasession/ folder."}))
        sys.exit(1)
        
    with open(session_file, "r") as f:
        session = json.load(f)
        
    access_token = session.get("access_token")
    api_key = session.get("api_key", "x2ob63qqr9dhyj6o")
    
    if not access_token:
        print(json.dumps({"error": "Access token not found in session file."}))
        sys.exit(1)
        
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}"
    }
    
    # GIFT NIFTY index token is 291849, exchange is NSEIX
    inst_token = "291849"
    interval = "minute"
    from_date = "2026-06-29"
    to_date = datetime.now().strftime("%Y-%m-%d")
    
    hist_url = f"https://api.kite.trade/instruments/historical/{inst_token}/{interval}?from={from_date}&to={to_date}"
    print(f"Downloading GIFT NIFTY data from: {hist_url}")
    
    try:
        resp_json = make_request(hist_url, headers=headers).decode("utf-8")
        result = json.loads(resp_json)
    except Exception as e:
        print(f"Failed to download historical data: {e}")
        if hasattr(e, "read"):
            print("Server response:", e.read().decode("utf-8"))
        sys.exit(1)
        
    if result.get("status") != "success":
        print(f"API Error: {result}")
        sys.exit(1)
        
    candles = result.get("data", {}).get("candles", [])
    print(f"Successfully downloaded {len(candles)} bars.")
    
    if not candles:
        print("No bars returned for this range.")
        return
        
    db_rows = []
    for c in candles:
        ts_iso = c[0]
        db_rows.append((
            ts_iso,
            float(c[1]),
            float(c[2]),
            float(c[3]),
            float(c[4]),
            float(c[5])
        ))
        
    saved = save_bars(
        db_rows,
        exchange="NSEIX",
        symbol="GIFTNIFTY",
        timeframe="1m"
    )
    print(f"Successfully saved {saved} bars to price_bars under exchange 'NSEIX' and symbol 'GIFTNIFTY'.")

if __name__ == "__main__":
    main()
