import sys
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from breeze_connect import BreezeConnect

# Add parent path to sys.path so we can import bar_store
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars, DB_PATH

BREEZE_SYMBOL_MAP = {
    "RELIANCE": "RELIND", "M&M": "MAHMAH", "BAJAJ-AUTO": "BAAUTO", "BAJAJFINSV": "BAFINS",
    "BAJFINANCE": "BAJFI", "HINDUNILVR": "HINLEV", "HEROMOTOCO": "HERHON", "NESTLEIND": "NESIND",
    "TATASTEEL": "TATSTE", "TATAMOTORS": "TATMOT", "ADANIENT": "ADAENT", "ADANIPORTS": "ADAPOR",
    "COALINDIA": "COALIN", "APOLLOHOSP": "APOHOS", "ASIANPAINT": "ASIPAI", "BRITANNIA": "BRIIND",
    "HINDALCO": "HINDAL", "INDUSINDBK": "INDIBK", "KOTAKBANK": "KOTMAH", "ULTRACEMCO": "ULTCEM",
    "BHARTIARTL": "BHAAIR", "LTIM": "LTIINF", "GRASIM": "GRASIM", "JSWSTEEL": "JSWSTE",
    "HCLTECH": "HCLTEC", "CIPLA": "CIPLA", "DRREDDY": "DRREDD", "SUNPHARMA": "SUNPHA",
    "AXISBANK": "AXIBAN", "HDFCBANK": "HDFBAN", "ICICIBANK": "ICIBAN", "SBIN": "STABAN",
    "INFY": "INFTEC", "TCS": "TCS", "LT": "LARTOU", "POWERGRID": "POWGRI", "NTPC": "NTPC",
    "ONGC": "ONGC", "TITAN": "TITIND", "INDIGO": "INTAVI", "WIPRO": "WIPRO", 
    "EICHERMOT": "EICMOT", "TATACONSUM": "TATGLO", "BPCL": "BHAPET", "TRENT": "TRENT", 
    "JIOFIN": "JIOFIN", "BEL": "BHAELE", "SHRIRAMFIN": "SHRTRA", "NIFTY": "NIFTY",
    "SBILIFE": "SBILIF", "HDFCLIFE": "HDFSTA", "TECHM": "TECMAH", "INDIAVIX": "INDVIX"
}

NIFTY_50_SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "TCS", "LT", "BHARTIARTL", "SBIN", "BAJFINANCE", 
    "AXISBANK", "KOTAKBANK", "M&M", "MARUTI", "HINDUNILVR", "ASIANPAINT", "HCLTECH", "TATAMOTORS", "SUNPHARMA", 
    "TITAN", "ADANIENT", "ULTRACEMCO", "BAJAJFINSV", "NTPC", "POWERGRID", "ADANIPORTS", "ONGC", "COALINDIA", 
    "TATASTEEL", "INDIGO", "HINDALCO", "GRASIM", "TECHM", "WIPRO", "SBILIFE", "HDFCLIFE", "EICHERMOT", 
    "BAJAJ-AUTO", "DRREDDY", "CIPLA", "APOLLOHOSP", "JSWSTEEL", "BRITANNIA", "TATACONSUM", "NESTLEIND", 
    "HEROMOTOCO", "BPCL", "TRENT", "JIOFIN", "BEL", "SHRIRAMFIN", "INDIAVIX"
]

DATES = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]

def backfill():
    if len(sys.argv) < 2:
        print("Error: session_token argument is required.")
        sys.exit(1)
        
    session_token = sys.argv[1]
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    print(f"Starting daily-chunked Nifty 50 historical 1m bar backfill (June 29 to July 3)")
    print(f"Target Database: {DB_PATH}")
    
    success_count = 0
    fail_count = 0
    
    for symbol in NIFTY_50_SYMBOLS:
        breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
        print(f"Fetching {symbol} ({breeze_symbol})...")
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
                
                if res and res.get("Success"):
                    for item in res["Success"]:
                        dt_str = item.get("datetime")
                        if dt_str:
                            try:
                                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                                dt_utc = dt - timedelta(hours=5.5)
                                ts_iso = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
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
                else:
                    # Log warning but don't abort other days
                    print(f"  [Warning] No data for {date_str}: {res.get('Error') or 'Empty Response'}")
            except Exception as e:
                print(f"  [Error] Exception for {date_str}: {e}")
            
            # Short sleep to respect API rate limits
            time.sleep(0.1)
            
        if symbol_rows:
            saved = save_bars(symbol_rows, exchange="NSE", symbol=symbol, timeframe="1m", db=DB_PATH)
            print(f"  Saved {saved} total bars for {symbol}.")
            success_count += 1
        else:
            print(f"  Failed to save any bars for {symbol}.")
            fail_count += 1
            
    print(f"\nBackfill Complete: {success_count} symbols succeeded, {fail_count} failed.")

if __name__ == "__main__":
    backfill()
