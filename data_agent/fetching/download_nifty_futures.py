import sys
import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
import dateutil.parser

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts", "breeze_env", "lib", "python3.9", "site-packages"))

from bar_store import save_bars, DB_PATH
from backend.timeutil import to_db_ts, parse_ist_str
from breeze_connect import BreezeConnect

def get_nifty_futures_expiries():
    import urllib.request
    import csv
    import io
    from datetime import datetime

    url = "https://api.kite.trade/instruments"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            csv_content = response.read().decode("utf-8")
            
        reader = csv.reader(io.StringIO(csv_content))
        header = next(reader)
        
        name_idx = header.index("name")
        expiry_idx = header.index("expiry")
        segment_idx = header.index("segment")
        
        expiries = set()
        for row in reader:
            if len(row) > max(name_idx, expiry_idx, segment_idx):
                if row[name_idx] == "NIFTY" and row[segment_idx] == "NFO-FUT":
                    expiries.add(row[expiry_idx])
                    
        # Sort expiries and format them to Upstox's required format: YYYY-MM-DDT06:00:00.000Z
        sorted_expiries = sorted(list(expiries))
        
        # We need the closest 2 expiries (near, next) that are >= today
        today_str = datetime.today().strftime("%Y-%m-%d")
        valid_expiries = [exp for exp in sorted_expiries if exp >= today_str]
        
        if len(valid_expiries) < 2:
            raise ValueError(f"Could not find 2 active NIFTY futures expiries. Found: {valid_expiries}")
            
        return (
            f"{valid_expiries[0]}T06:00:00.000Z",
            f"{valid_expiries[1]}T06:00:00.000Z"
        )
    except Exception as e:
        sys.stderr.write(f"Error fetching expiries from Kite: {e}\n")
        raise

def download_futures():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing session token"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        exp1, exp2 = get_nifty_futures_expiries()
        sys.stderr.write(f"Expiries found: Near={exp1}, Next={exp2}\n")
        
        now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        
        for symbol, expiry in [("NIFTY_FUT_1", exp1), ("NIFTY_FUT_2", exp2)]:
            watermark = None
            if len(sys.argv) > 2:
                try:
                    start_dt = datetime.strptime(sys.argv[2], "%Y-%m-%d")
                    end_dt = datetime.strptime(sys.argv[3], "%Y-%m-%d") if len(sys.argv) > 3 else start_dt
                except Exception:
                    start_dt = now_ist - timedelta(days=5)
                    end_dt = now_ist
            else:
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        r = conn.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=? AND timeframe='1m'", (symbol,)).fetchone()
                        if r and r[0]:
                            watermark = r[0]
                except Exception:
                    pass
                
                if watermark:
                    wm_dt = dateutil.parser.parse(watermark).astimezone(timezone(timedelta(hours=5, minutes=30)))
                    if wm_dt.date() == now_ist.date():
                        start_dt = wm_dt.replace(hour=9, minute=15, second=0, microsecond=0)
                    else:
                        start_dt = (wm_dt + timedelta(days=1)).replace(hour=9, minute=15, second=0, microsecond=0)
                else:
                    start_dt = now_ist - timedelta(days=5)
                end_dt = now_ist

            if start_dt > end_dt:
                start_dt = end_dt

            breeze_start = start_dt.strftime("%Y-%m-%dT00:00:00.000Z")
            breeze_end = end_dt.strftime("%Y-%m-%dT23:59:59.000Z")
            
            sys.stderr.write(f"Downloading {symbol} (expiry {expiry}) from {breeze_start} to {breeze_end}...\n")
            
            res = breeze.get_historical_data_v2(
                interval="1minute",
                from_date=breeze_start,
                to_date=breeze_end,
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=expiry
            )
            
            success_rows = res.get("Success", []) if res else []
            if not success_rows:
                sys.stderr.write(f"No bars fetched for {symbol}.\n")
                continue
                
            formatted_rows = []
            for r in success_rows:
                dt = dateutil.parser.parse(r["datetime"])
                ts_iso = to_db_ts(dt)
                formatted_rows.append((
                    ts_iso,
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r.get("volume", 0.0) or 0.0),
                    float(r.get("open_interest", 0.0) or 0.0)
                ))
                
            saved = save_bars(formatted_rows, exchange="NFO", symbol=symbol, timeframe="1m")
            sys.stderr.write(f"Saved {saved} bars for {symbol}.\n")
            
        print(json.dumps({"success": True}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    download_futures()
