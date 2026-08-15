import sys
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from breeze_connect import BreezeConnect

import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars, DB_PATH

# We will query: CNXIT (Nifty IT) and CNXAUTO (Nifty Auto)
INDICES_MAP = {
    "CNXIT": "CNXIT",
    "CNXAUTO": "CNXAUTO"
}

# Days list starting from June 29th, 2026 to present
# (We exclude weekends as markets are closed)
DATES = [
    "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03",
    "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20"
]

def backfill_sector_indices():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing session token. Usage: python script.py <session_token>"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    print(f"Starting Sector Indices 1m bar backfill (June 29 to Present)")
    print(f"Target Database: {DB_PATH}")
    
    total_saved = 0
    results = []
    
    for symbol, breeze_symbol in INDICES_MAP.items():
        print(f"Fetching index: {symbol}...")
        symbol_rows = []
        
        for date_str in DATES:
            from_date = f"{date_str}T09:15:00.000Z"
            to_date = f"{date_str}T15:30:00.000Z"
            
            try:
                res = breeze.get_historical_data_v2(
                    interval="1minute",
                    from_date=from_date,
                    to_date=to_date,
                    stock_code=breeze_symbol,
                    exchange_code="NSE",
                    product_type="cash"
                )
                
                success_rows = res.get("Success", []) if res else []
                if success_rows:
                    for item in success_rows:
                        dt_str = item.get("datetime")
                        if dt_str:
                            try:
                                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                                dt_utc = dt - timedelta(hours=5, minutes=30)
                                ts_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                            except Exception:
                                ts_iso = dt_str
                        else:
                            continue
                            
                        symbol_rows.append((
                            ts_iso,
                            float(item.get("open", 0.0)),
                            float(item.get("high", 0.0)),
                            float(item.get("low", 0.0)),
                            float(item.get("close", 0.0)),
                            float(item.get("volume", 0.0))
                        ))
            except Exception as e:
                print(f"Error fetching {symbol} on {date_str}: {e}")
                
            time.sleep(0.1) # Sleep to keep rate limit safe
            
        if symbol_rows:
            saved = save_bars(symbol_rows, exchange="NSE", symbol=symbol, timeframe="1m", db=DB_PATH)
            total_saved += saved
            results.append({"symbol": symbol, "status": "success", "count": saved})
            print(f"Saved {saved} bars for index: {symbol}")
        else:
            results.append({"symbol": symbol, "status": "no_data", "count": 0})
            print(f"No data recovered for index: {symbol}")
            
    print(json.dumps({"success": True, "total_saved": total_saved, "details": results}))

if __name__ == "__main__":
    backfill_sector_indices()
