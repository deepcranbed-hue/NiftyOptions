import sys
import os
import argparse
import urllib.request
import urllib.parse
import json
import csv
from datetime import datetime
import hashlib

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
    parser = argparse.ArgumentParser(description="Zerodha Kite Connect Commodity Downloader (Pure Python REST version)")
    parser.add_argument("--api_key", default="x2ob63qqr9dhyj6o", help="Zerodha Kite API Key")
    parser.add_argument("--api_secret", default="10swuoyms3l3id21cfcwri8f0cj7sapn", help="Zerodha Kite API Secret")
    parser.add_argument("--request_token", help="Request token received after login redirect")
    parser.add_argument("--access_token", help="Direct access token if already generated")
    parser.add_argument("--symbol", default="GOLD", help="Symbol to download")
    parser.add_argument("--from_date", default="2026-07-01", help="Start Date (YYYY-MM-DD)")
    parser.add_argument("--to_date", default="2026-07-06", help="End Date (YYYY-MM-DD)")
    parser.add_argument("--interval", default="minute", choices=["minute", "day"], help="Data frequency")
    
    args = parser.parse_args()
    
    api_key = args.api_key
    access_token = args.access_token
    
    if not access_token and args.request_token:
        # Generate checksum: sha256(api_key + request_token + api_secret)
        print("Exchanging request_token for access_token...")
        raw_checksum = api_key + args.request_token + args.api_secret
        checksum = hashlib.sha256(raw_checksum.encode("utf-8")).hexdigest()
        
        token_url = "https://api.kite.trade/session/token"
        headers = {
            "X-Kite-Version": "3",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "api_key": api_key,
            "request_token": args.request_token,
            "checksum": checksum
        }
        try:
            resp = make_request(token_url, headers=headers, method="POST", data=data).decode("utf-8")
            session_data = json.loads(resp)
            access_token = session_data["data"]["access_token"]
            print(f"Authentication Successful! Access Token: {access_token}")
        except Exception as e:
            print(f"Failed to exchange token: {e}")
            if hasattr(e, "read"):
                print("Server response:", e.read().decode("utf-8"))
            sys.exit(1)
            
    if not access_token:
        # Generate the login URL
        login_url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
        print("\n=== Zerodha Kite Authentication Required ===")
        print("1. Open the following URL in your browser to log in:")
        print(f"   {login_url}")
        print("\n2. After logging in, you will be redirected to your Redirect URL.")
        print("3. Copy the 'request_token' parameter from the URL address bar.")
        print("\n4. Re-run this script with the --request_token parameter:")
        print(f"   python test_kite_connect.py --request_token COPIED_TOKEN --symbol {args.symbol}\n")
        sys.exit(0)
        
    api_key = args.api_key
    access_token = args.access_token
    
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}"
    }
    
    # 1. Download instruments CSV and find matching token
    print("Fetching active Zerodha instruments...")
    instruments_url = "https://api.kite.trade/instruments"
    try:
        csv_data = make_request(instruments_url).decode("utf-8")
    except Exception as e:
        print(f"Failed to fetch instruments: {e}")
        sys.exit(1)
        
    print("Parsing instruments to locate contract...")
    reader = csv.DictReader(csv_data.splitlines())
    
    target_symbol = args.symbol.upper()
    if target_symbol in ("GIFTNIFTY", "GIFT NIFTY"):
        target_exchange = "NSEIX"
    elif target_symbol == "USDINR":
        target_exchange = "CDS"
    else:
        target_exchange = "MCX"
    
    matching = []
    for row in reader:
        # Columns: instrument_token, exchange_token, tradingsymbol, name, last_price, expiry, strike, tick_size, lot_size, instrument_type, segment, exchange
        if row["exchange"] == target_exchange:
            if target_symbol in ("GIFTNIFTY", "GIFT NIFTY") and row["name"].upper() in ("GIFT NIFTY", "GIFTNIFTY"):
                matching.append(row)
            elif row["name"] == target_symbol:
                # Prioritize futures (FUT) contracts for clean price series
                if row["instrument_type"] == "FUT":
                    matching.append(row)
                    
    # Fallback to matches with startswith if none found
    if not matching and target_symbol not in ("GIFTNIFTY", "GIFT NIFTY"):
        print("No exact name Futures contracts found, checking fallback matches...")
        reader = csv.DictReader(csv_data.splitlines())
        for row in reader:
            if row["exchange"] == target_exchange:
                if row["name"] == target_symbol or row["tradingsymbol"].startswith(target_symbol):
                    if row["instrument_type"] == "FUT":
                        matching.append(row)
                
    # For USDINR, prioritize the monthly contract (e.g. USDINR26JULFUT which ends with a 3-letter month + FUT)
    # as Zerodha does not store historical data for weekly CDS contracts.
    if target_symbol == "USDINR":
        months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        monthly_matches = [r for r in matching if any(r["tradingsymbol"].endswith(f"{m}FUT") for m in months)]
        if monthly_matches:
            matching = monthly_matches

    if not matching:
        print(f"No matching instruments found for symbol: {target_symbol} on exchange: {target_exchange}")
        sys.exit(1)
        
    # Sort contracts by expiry to find the active near-month contract
    # Daily bars or minute bars require selecting the correct contract token
    def parse_expiry(r):
        exp = r.get("expiry")
        if not exp:
            return datetime.max
        try:
            return datetime.strptime(exp, "%Y-%m-%d")
        except ValueError:
            return datetime.max
            
    matching.sort(key=parse_expiry)
    selected_contract = matching[0]
    
    print(f"\nFound {len(matching)} contracts. Selected contract details:")
    print(f"  Trading Symbol: {selected_contract['tradingsymbol']}")
    print(f"  Instrument Token: {selected_contract['instrument_token']}")
    print(f"  Expiry: {selected_contract.get('expiry')}")
    print(f"  Exchange: {selected_contract['exchange']}")
    
    # 2. Query Historical Data
    inst_token = selected_contract["instrument_token"]
    interval = args.interval
    from_date = args.from_date
    to_date = args.to_date
    
    hist_url = f"https://api.kite.trade/instruments/historical/{inst_token}/{interval}?from={from_date}&to={to_date}"
    print(f"\nDownloading historical data from: {hist_url}")
    
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
        
    # 3. Convert candles into database rows
    # Candle format: [date_str, open, high, low, close, volume]
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
        
    # Save to SQLite price_bars table using save_bars
    tf = "1m" if args.interval == "minute" else "1d"
    print(f"Saving data to SQLite price_bars table under symbol '{target_symbol}' (Exchange: {selected_contract['exchange']})...")
    saved = save_bars(
        db_rows,
        exchange=selected_contract["exchange"],
        symbol=target_symbol,
        timeframe=tf
    )
    print(f"Successfully saved {saved} clean, normalized bars to the database!")

if __name__ == "__main__":
    main()
