import sys
import json
import csv
import sqlite3
import time
import time
from datetime import datetime, timezone, timedelta
import os
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts", "breeze_env", "lib", "python3.9", "site-packages"))
from breeze_connect import BreezeConnect

from bar_store import save_bars, DB_PATH

# Auto-resolve and refresh Breeze mappings if missing or older than 7 days
def ensure_breeze_mappings():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_json = os.path.join(root, "strategy_framework", "config", "breeze_symbol_map.json")
    local_zip = os.path.join(root, "SecurityMaster.zip")
    
    # Check if JSON exists and is less than 7 days old
    if os.path.exists(target_json):
        age_days = (time.time() - os.path.getmtime(target_json)) / 86400
        if age_days < 7:
            try:
                with open(target_json) as jf:
                    return json.load(jf)
            except Exception:
                pass

    sys.stderr.write("Refreshing Breeze symbol mapping from NSE and Security Master...\n")
    import urllib.request
    import zipfile
    import io
    
    # 1. Fetch live Nifty 50 constituents list from NSE
    nse_url = "https://archives.nseindia.com/content/indices/ind_nifty50list.csv"
    req = urllib.request.Request(nse_url, headers={'User-Agent': 'Mozilla/5.0'})
    nse_symbols = []
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                sym = row.get("Symbol")
                if sym:
                    nse_symbols.append(sym.strip())
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to fetch from NSE ({e}), using default fallback.\n")
        nse_symbols = [
            'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK', 
            'BAJAJ-AUTO', 'BAJFINANCE', 'BAJAJFINSV', 'BEL', 'BHARTIARTL', 
            'CIPLA', 'COALINDIA', 'DRREDDY', 'EICHERMOT', 'ZOMATO', 'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE', 'HINDALCO', 
            'HINDUNILVR', 'ICICIBANK', 'ITC', 'INFY', 'INDIGO', 
            'JSWSTEEL', 'JIOFIN', 'KOTAKBANK', 'LT', 'M&M', 
            'MARUTI', 'MAXHEALTH', 'NTPC', 'NESTLEIND', 'ONGC', 
            'POWERGRID', 'RELIANCE', 'SBILIFE', 'SHRIRAMFIN', 'SBIN', 
            'SUNPHARMA', 'TCS', 'TATACONSUM', 'TATAMOTORS', 'TATASTEEL', 
            'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO'
        ]

    # 2. Download SecurityMaster.zip if missing or older than 7 days
    if not os.path.exists(local_zip) or (time.time() - os.path.getmtime(local_zip)) / 86400 >= 7:
        zip_url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
        try:
            req_zip = urllib.request.Request(zip_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_zip, timeout=60) as resp:
                with open(local_zip, "wb") as f:
                    f.write(resp.read())
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to download SecurityMaster.zip ({e}).\n")

    # 3. Parse and map symbols
    mapping = {}
    if os.path.exists(local_zip):
        try:
            z = zipfile.ZipFile(local_zip)
            with z.open("NSEScripMaster.txt") as f:
                content = f.read().decode('utf-8', errors='ignore')
                reader = csv.reader(io.StringIO(content))
                headers = next(reader)
                headers = [h.strip().replace('"', '') for h in headers]
                symbol_idx = headers.index("ExchangeCode")
                breeze_code_idx = headers.index("ShortName")
                series_idx = headers.index("Series")
                for row in reader:
                    if len(row) > symbol_idx:
                        sym = row[symbol_idx].strip().replace('"', '')
                        series = row[series_idx].strip().replace('"', '')
                        if sym in nse_symbols and series == "EQ":
                            mapping[sym] = row[breeze_code_idx].strip().replace('"', '')
        except Exception as e:
            sys.stderr.write(f"Error parsing NSEScripMaster.txt: {e}\n")

    if mapping:
        try:
            os.makedirs(os.path.dirname(target_json), exist_ok=True)
            with open(target_json, "w") as jf:
                json.dump(mapping, jf, indent=4)
        except Exception as e:
            sys.stderr.write(f"Error saving breeze_symbol_map.json: {e}\n")
    return mapping

BREEZE_SYMBOL_MAP = ensure_breeze_mappings()
BREEZE_SYMBOL_MAP["NIFTY"] = "NIFTY"
BREEZE_SYMBOL_MAP["INDIAVIX"] = "INDVIX"
BREEZE_SYMBOL_MAP["NSEBANK"] = "CNXBAN"
BREEZE_SYMBOL_MAP["CNXIT"] = "CNXIT"

NIFTY_50_SYMBOLS = ["NIFTY", "INDIAVIX", "NSEBANK", "CNXIT"] + sorted(list(k for k in BREEZE_SYMBOL_MAP.keys() if k not in ("NIFTY", "INDIAVIX", "NSEBANK", "CNXIT")))

def sync_all_symbols():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "detail": "Session token required."}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        results = []
        total_bars_saved = 0
        
        for symbol in NIFTY_50_SYMBOLS:
            breeze_symbol = BREEZE_SYMBOL_MAP.get(symbol.upper(), symbol)
            
            # Find max timestamp for this symbol in database
            c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=? AND timeframe='1m'", (symbol,))
            row = c.fetchone()
            watermark = row[0] if row else None
            
            if watermark:
                # Parse watermark as UTC and shift to IST representation
                dt_utc = datetime.fromisoformat(watermark.replace('Z', '+00:00'))
                start_dt = dt_utc + timedelta(hours=5, minutes=30) + timedelta(minutes=1)
            else:
                # If no data exists, only bootstrap from today's market open to avoid heavy fetching
                start_dt = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            
            # If current time is past market close, clamp query to 15:30
            end_dt = now_ist
            if end_dt.hour > 15 or (end_dt.hour == 15 and end_dt.minute > 30):
                end_dt = end_dt.replace(hour=15, minute=30, second=0, microsecond=0)
                
            # Skip if time range is negative or too small
            if start_dt >= end_dt:
                results.append({"symbol": symbol, "status": "up_to_date", "count": 0})
                continue
                
            from_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            to_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            try:
                res = breeze.get_historical_data_v2(
                    interval="1minute",
                    from_date=from_str,
                    to_date=to_str,
                    stock_code=breeze_symbol,
                    exchange_code="NSE",
                    product_type="cash"
                )
                
                success_rows = res.get("Success", []) if res else []
                if not success_rows:
                    results.append({"symbol": symbol, "status": "empty", "count": 0})
                    continue
                    
                formatted_rows = []
                for item in success_rows:
                    dt_str = item.get("datetime")
                    if dt_str:
                        try:
                            # Breeze returns datetime string in IST format without timezone suffix (e.g. YYYY-MM-DD HH:MM:SS)
                            dt_item = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                            dt_item_utc = dt_item - timedelta(hours=5, minutes=30)
                            ts_iso = dt_item_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        except Exception:
                            ts_iso = dt_str
                    else:
                        continue
                        
                    formatted_rows.append((
                        ts_iso,
                        float(item.get("open", 0.0)),
                        float(item.get("high", 0.0)),
                        float(item.get("low", 0.0)),
                        float(item.get("close", 0.0)),
                        float(item.get("volume", 0.0))
                    ))
                    
                if formatted_rows:
                    saved = save_bars(formatted_rows, exchange="NSE", symbol=symbol, timeframe="1m", db=DB_PATH)
                    total_bars_saved += saved
                    results.append({"symbol": symbol, "status": "success", "count": saved})
                else:
                    results.append({"symbol": symbol, "status": "no_valid_rows", "count": 0})
                    
            except Exception as e:
                results.append({"symbol": symbol, "status": "error", "error": str(e), "count": 0})
                
            # Keep query rate safe
            time.sleep(0.1)
            
        # ---------------------------------------------------------------
        # NO EQUITY DAILY BARS HERE — deliberately removed.
        #
        # Breeze serves only ~1 year of daily history and wrote it under a
        # different ts string ('...T00:00:00Z') than the Yahoo rows already in
        # price_bars ('...T00:00:00'). Since ts is part of the primary key, those
        # are two keys for the SAME session: the feeds never overwrote each other,
        # they accumulated. That produced 22 duplicated NIFTY sessions and left
        # TATAMOTORS/ZOMATO stuck at 246 bars.
        #
        # Daily equity bars are now owned by ONE writer:
        #     data_agent/fetching/sync_nifty50_bars_yf.py  (Yahoo, incremental)
        # which sync_all_auxiliary.py runs right after this script.
        #
        # This script still uses Breeze for 1-MINUTE bars (above) and for FUTURES
        # daily bars (below), which Yahoo does not carry.
        # ---------------------------------------------------------------

        # Download 1-day frequency data for Nifty Futures
        try:
            from download_nifty_futures import get_nifty_futures_expiries
            exp1, exp2 = get_nifty_futures_expiries()
            for symbol, expiry in [("NIFTY_FUT_1", exp1), ("NIFTY_FUT_2", exp2)]:
                c.execute("SELECT MAX(ts) FROM price_bars WHERE symbol=? AND timeframe='1d'", (symbol,))
                row = c.fetchone()
                watermark = row[0] if row else None
                if watermark:
                    dt_utc = datetime.fromisoformat(watermark.replace('Z', '+00:00'))
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                    start_dt = dt_utc + timedelta(hours=5, minutes=30) + timedelta(days=1)
                else:
                    start_dt = one_year_ago
                    
                end_dt = now_ist
                if start_dt < end_dt:
                    from_str = start_dt.strftime("%Y-%m-%dT00:00:00.000Z")
                    to_str = end_dt.strftime("%Y-%m-%dT23:59:59.000Z")
                    res = breeze.get_historical_data_v2(
                        interval="1day",
                        from_date=from_str,
                        to_date=to_str,
                        stock_code="NIFTY",
                        exchange_code="NFO",
                        product_type="futures",
                        expiry_date=expiry
                    )
                    success_rows = res.get("Success", []) if res else []
                    formatted_rows = []
                    for item in success_rows:
                        dt_str = item.get("datetime")
                        if dt_str:
                            try:
                                dt_item = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                                ts_iso = dt_item.strftime("%Y-%m-%dT00:00:00Z")
                            except Exception:
                                ts_iso = dt_str
                            formatted_rows.append((
                                ts_iso,
                                float(item.get("open", 0.0)),
                                float(item.get("high", 0.0)),
                                float(item.get("low", 0.0)),
                                float(item.get("close", 0.0)),
                                float(item.get("volume", 0.0))
                            ))
                    if formatted_rows:
                        save_bars(formatted_rows, exchange="NFO", symbol=symbol, timeframe="1d", db=DB_PATH)
        except Exception as err:
            sys.stderr.write(f"Failed daily futures sync: {err}\n")

        conn.close()

        # Automatically sync Nifty futures (NIFTY_FUT_1 & NIFTY_FUT_2)
        print("Automatically syncing Nifty Futures...")
        import subprocess
        time.sleep(3)
        fut_res = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "download_nifty_futures.py"), session_token], capture_output=True, text=True)
        print("Futures Sync Output:", fut_res.stdout)
            
        print(json.dumps({"success": True, "total_saved": total_bars_saved, "details": results}))
    except Exception as e:
        print(json.dumps({"success": False, "detail": str(e)}))

if __name__ == "__main__":
    sync_all_symbols()
