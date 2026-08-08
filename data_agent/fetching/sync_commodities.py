import os
import sys
import sqlite3
import requests
import pandas as pd
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.append(REPO_ROOT)
sys.path.append(os.path.join(REPO_ROOT, "scratch_scripts"))

DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"
from upstox_auth import get_upstox_token
UPSTOX_ACCESS_TOKEN = get_upstox_token()
# Mapping of symbols in DB to Upstox keys and their exchange codes
SYMBOLS_MAP = {
    "CRUDEOIL": {"key": "MCX_FO|560977", "exchange": "MCX"},
    "USDINR": {"key": "GLOBAL_INDICATOR|USDINR", "exchange": "CDS"},
    "GOLD": {"key": "MCX_FO|466583", "exchange": "MCX"},
    "SILVER": {"key": "MCX_FO|471725", "exchange": "MCX"},
    "COPPER": {"key": "MCX_FO|562048", "exchange": "MCX"},
    "GIFTNIFTY": {"key": "GLOBAL_INDEX|SGX NIFTY", "exchange": "NSEIX"}
}

def to_utc_str(ist_timestamp_str):
    # Upstox returns: "2026-07-27T23:29:00+05:30"
    # Convert to UTC ISO format: "2026-07-27T17:59:00Z"
    dt = datetime.strptime(ist_timestamp_str, "%Y-%m-%dT%H:%M:%S%z")
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_upstox_historical(instrument_key, from_date, to_date):
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
    headers = {
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
        'Accept': 'application/json'
    }
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        print(f"Failed to fetch historical for key {instrument_key}. Status: {response.status_code}")
        return []
    return response.json().get("data", {}).get("candles", [])

def fetch_upstox_intraday(instrument_key):
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{instrument_key}/1minute"
    headers = {
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
        'Accept': 'application/json'
    }
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        print(f"Failed to fetch intraday for key {instrument_key}. Status: {response.status_code}")
        return []
    return response.json().get("data", {}).get("candles", [])

def fetch_upstox_daily_historical(instrument_key, from_date, to_date):
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {
        'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}',
        'Accept': 'application/json'
    }
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        print(f"Failed to fetch daily historical for key {instrument_key}. Status: {response.status_code}")
        return []
    return response.json().get("data", {}).get("candles", [])

def sync_symbol(conn, symbol, config):
    print(f"\n--- Syncing {symbol} ({config['key']}) ---")
    
    # 1. Fetch Historical Batches (1m)
    from datetime import datetime, timedelta
    start_dt = datetime.strptime("2026-06-29", "%Y-%m-%d")
    now_dt = datetime.now()
    batches = []
    cur = start_dt
    while cur < now_dt:
        next_cur = min(cur + timedelta(days=7), now_dt)
        batches.append((cur.strftime("%Y-%m-%d"), next_cur.strftime("%Y-%m-%d")))
        cur = next_cur + timedelta(days=1)
    
    all_candles = []
    
    for from_date, to_date in batches:
        print(f"Fetching historical 1m batch from {from_date} to {to_date}...")
        candles = fetch_upstox_historical(config["key"], from_date, to_date)
        all_candles.extend(candles)
        
    # 2. Fetch Intraday (Today's candles 1m)
    print("Fetching today's active 1m intraday candles...")
    intraday_candles = fetch_upstox_intraday(config["key"])
    all_candles.extend(intraday_candles)
    
    # 3. Deduplicate by UTC timestamp (1m)
    seen_timestamps = set()
    unique_rows = []
    
    for c in all_candles:
        try:
            utc_ts = to_utc_str(c[0])
            if utc_ts in seen_timestamps:
                continue
            seen_timestamps.add(utc_ts)
            
            # Scale down USDINR indicator (10x scaled in Upstox)
            scale = 10.0 if symbol == "USDINR" else 1.0
            
            unique_rows.append((
                config["exchange"],
                symbol,
                "1m",
                utc_ts,
                float(c[1]) / scale,  # open
                float(c[2]) / scale,  # high
                float(c[3]) / scale,  # low
                float(c[4]) / scale,  # close
                float(c[5]),  # volume
                float(c[6]) if len(c) > 6 and c[6] is not None else None # open_interest
            ))
        except Exception as e:
            print(f"Error parsing candle {c}: {e}")
            
    cursor = conn.cursor()
    
    if unique_rows:
        min_ts = min(unique_rows, key=lambda x: x[3])[3]
        max_ts = max(unique_rows, key=lambda x: x[3])[3]
        print(f"Parsed {len(unique_rows)} unique 1m candles. Deleting existing data between {min_ts} and {max_ts}...")
        # 4. Delete existing 1m data for the range
        cursor.execute("""
            DELETE FROM price_bars 
            WHERE symbol = ? 
              AND timeframe = '1m'
              AND ts >= ? AND ts <= ?
        """, (symbol, min_ts, max_ts))
        # 5. Insert the new 1m data
        cursor.executemany("""
            INSERT OR REPLACE INTO price_bars (exchange, symbol, timeframe, ts, open, high, low, close, volume, open_interest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, unique_rows)
        print(f"Successfully inserted {cursor.rowcount} new 1m rows for {symbol} into database.")

    # 6. Fetch and Insert Daily (1d) Candles
    print("Fetching historical daily (1d) candles...")
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_candles = fetch_upstox_daily_historical(config["key"], "2025-07-30", today_str)
    
    unique_daily_rows = []
    for c in daily_candles:
        try:
            dt = datetime.strptime(c[0][:10], "%Y-%m-%d")
            utc_ts = dt.strftime("%Y-%m-%dT00:00:00Z")
            
            # Scale down USDINR indicator (10x scaled in Upstox)
            scale = 10.0 if symbol == "USDINR" else 1.0
            
            unique_daily_rows.append((
                config["exchange"],
                symbol,
                "1d",
                utc_ts,
                float(c[1]) / scale,  # open
                float(c[2]) / scale,  # high
                float(c[3]) / scale,  # low
                float(c[4]) / scale,  # close
                float(c[5]),  # volume
                float(c[6]) if len(c) > 6 and c[6] is not None else None
            ))
        except Exception as e:
            print(f"Error parsing daily candle {c}: {e}")
            
    if unique_daily_rows:
        cursor.execute("""
            DELETE FROM price_bars 
            WHERE symbol = ? 
              AND timeframe = '1d'
              AND ts >= '2025-07-30T00:00:00Z'
        """, (symbol,))
        cursor.executemany("""
            INSERT OR REPLACE INTO price_bars (exchange, symbol, timeframe, ts, open, high, low, close, volume, open_interest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, unique_daily_rows)
        print(f"Successfully synced {len(unique_daily_rows)} daily (1d) rows for {symbol} into database.")
        
    conn.commit()

def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database file not found at: {DB_PATH}")
        return
        
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    
    try:
        for symbol, config in SYMBOLS_MAP.items():
            sync_symbol(conn, symbol, config)
        print("\n[SUCCESS] All commodities, USDINR, and GIFTNIFTY synced successfully!")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
