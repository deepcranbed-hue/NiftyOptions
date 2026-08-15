import sys
import os
from datetime import datetime, timezone, timedelta
from breeze_connect import BreezeConnect

# Add parent path to sys.path so we can import bar_store
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bar_store import save_bars, DB_PATH

DATES = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"]

def download_vix():
    if len(sys.argv) < 2:
        print("Error: session_token argument is required.")
        sys.exit(1)
        
    session_token = sys.argv[1]
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    
    symbol_rows = []
    print(f"Downloading INDIA VIX (INDVIX) 1m bars from June 29 to July 3...")
    
    for date_str in DATES:
        from_date = f"{date_str}T09:15:00.000Z"
        to_date = f"{date_str}T15:30:00.000Z"
        
        try:
            res = breeze.get_historical_data_v2(
                interval="1minute",
                from_date=from_date,
                to_date=to_date,
                stock_code="INDVIX",
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
                print(f"  Downloaded data for {date_str}.")
            else:
                print(f"  [Warning] No data for {date_str}: {res.get('Error') or 'Empty Response'}")
        except Exception as e:
            print(f"  [Error] Exception for {date_str}: {e}")
            
    if symbol_rows:
        saved = save_bars(symbol_rows, exchange="NSE", symbol="INDIAVIX", timeframe="1m", db=DB_PATH)
        print(f"\nSaved {saved} total bars for INDIAVIX to database: {DB_PATH}")
    else:
        print("\nFailed to retrieve any data for INDIAVIX.")

if __name__ == "__main__":
    download_vix()
